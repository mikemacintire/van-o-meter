"""Predict tomorrow's solar harvest from the weather forecast.

Pure logic + JSON config, no network — same discipline as balancer.py and
decision.py. `weather.py` does the fetching; this module only decides what the
numbers mean.

The method is deliberately modest. Fit a one-feature linear regression against
the days we have actually logged, cross-validate it, and use it *only* if it
beats a plain rolling average by a clear margin. That guard is not theoretical:
measured against 10 logged days, total irradiance — the obvious feature —
scored worse than predicting the flat average, while sunshine hours beat it by
half. Under a tree, what matters is how long direct sun gets through, not how
bright the sky is.

Two kinds of day are excluded from training. Partial days (poller restarts,
the machine sleeping) have counter deltas that do not describe a whole day.
Curtailed days — where a pack filled and the MPPT throttled — under-report what
was available, and training on them teaches the model that sunny days are
worse.

Spec: docs/superpowers/specs/2026-08-07-solar-forecast-design.md
"""

import json
from dataclasses import dataclass, asdict
from datetime import timedelta

from . import history

# Candidate predictors, best-first — ties break toward the earlier entry, and
# this order is the empirical ranking measured over the first 10 logged days.
CANDIDATES = ["sunshine_duration", "cloud_cover_mean", "shortwave_radiation_sum"]

MIN_TRAIN_DAYS = 5      # below this the model reports "learning" and predicts nothing
MIN_DAY_SAMPLES = 2000  # of ~2880 expected at 30 s cadence; below is a holed day
MIN_DAY_SPAN_H = 20     # first-to-last sample; below is a partial day
CURTAIL_SOC = 99        # a pack at/above this throttled the MPPT — day is censored
BEAT_BASELINE = 0.10    # a feature must cut the flat average's error by this much
CLAMP_HEADROOM = 1.25   # ceiling as a multiple of the sunniest day ever logged
OBS_DAYS = 400          # how far back daily_observations differentiates counters


@dataclass
class Config:
    # Butler Beach, FL 32080, via Open-Meteo's geocoder. Not auto-detected: the
    # rig's uplink geolocates to the carrier's POP, potentially hundreds of
    # miles off, which would quietly train the model on someone else's weather.
    latitude: float = 29.7983
    longitude: float = -81.26701
    label: str = "Butler Beach, FL"
    window_days: int = 14   # upper bound on training history, not a promise of it

    def validate(self):
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude {self.latitude} out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude {self.longitude} out of range")
        if not 3 <= self.window_days <= 90:
            raise ValueError(f"window_days {self.window_days} must be 3-90")

    def save(self, path):
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path):
        d = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                latitude=float(data.get("latitude", d.latitude)),
                longitude=float(data.get("longitude", d.longitude)),
                label=str(data.get("label", d.label)),
                window_days=int(data.get("window_days", d.window_days)))
        except (OSError, ValueError, KeyError, TypeError):
            return cls()


def daily_observations(rows, units):
    """Per local day: combined solar Wh plus the quality signals that gate it.

    `samples` and `span_h` are the *worst* unit's, since a day is only as
    complete as its thinnest coverage; `max_soc` is the best unit's, since
    either pack filling is enough to throttle that unit's harvest.
    """
    out = {}
    for unit in units:
        for day in history.daily_energy(rows, unit, OBS_DAYS):
            slot = out.setdefault(day["date"], {"wh": 0.0})
            slot["wh"] += day["solar_wh"]

    per_unit = {}
    for row in rows:
        if row["unit"] not in units:
            continue
        key = (row["unit"], row["dt"].date().isoformat())
        slot = per_unit.setdefault(key, {"n": 0, "first": row["dt"], "last": row["dt"],
                                         "soc": None})
        slot["n"] += 1
        slot["first"] = min(slot["first"], row["dt"])
        slot["last"] = max(slot["last"], row["dt"])
        if row.get("soc") is not None:
            slot["soc"] = row["soc"] if slot["soc"] is None else max(slot["soc"], row["soc"])

    for date, slot in out.items():
        stats = [v for (unit, d), v in per_unit.items() if d == date]
        slot["samples"] = min((s["n"] for s in stats), default=0)
        slot["span_h"] = min(((s["last"] - s["first"]).total_seconds() / 3600
                              for s in stats), default=0.0)
        socs = [s["soc"] for s in stats if s["soc"] is not None]
        slot["max_soc"] = max(socs) if socs else None
    return out


def training_set(observations, weather_days, window_days, today,
                 min_samples=MIN_DAY_SAMPLES, min_span_h=MIN_DAY_SPAN_H,
                 curtail_soc=CURTAIL_SOC):
    """Days healthy enough to learn from, newest window only.

    Today is always excluded — it is partial by definition until midnight.
    """
    by_date = {d["date"]: d for d in weather_days}
    start = (today - timedelta(days=window_days)).isoformat()
    cutoff = today.isoformat()
    train = []
    for date in sorted(observations):
        if not start <= date < cutoff:
            continue
        obs = observations[date]
        if obs.get("samples", 0) < min_samples or obs.get("span_h", 0) < min_span_h:
            continue
        if obs.get("max_soc") is not None and obs["max_soc"] >= curtail_soc:
            continue
        weather = by_date.get(date)
        if weather is None:
            continue
        train.append({
            "date": date,
            "wh": obs["wh"],
            "features": {f: weather.get(f) for f in CANDIDATES},
        })
    return train


def _fit(xs, ys):
    """Least-squares (slope, intercept), or None if x has no spread."""
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    denom = n * sum(x * x for x in xs) - sx * sx
    if abs(denom) < 1e-12:
        return None
    slope = (n * sum(x * y for x, y in zip(xs, ys)) - sx * sy) / denom
    return slope, (sy - slope * sx) / n


def _loo_mae(xs, ys):
    """Leave-one-out cross-validated mean absolute error of a linear fit.

    Honest with a handful of points, where an in-sample fit would flatter
    itself badly. None when too few points or no fold is well-posed.
    """
    if len(xs) < 3:
        return None
    errors = []
    for i in range(len(xs)):
        fit = _fit(xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:])
        if fit is None:
            continue
        slope, intercept = fit
        errors.append(abs(slope * xs[i] + intercept - ys[i]))
    return sum(errors) / len(errors) if errors else None


def _loo_mae_mean(ys):
    """The same honesty for the baseline: predict each day from the others."""
    if len(ys) < 2:
        return None
    total = sum(ys)
    return sum(abs((total - y) / (len(ys) - 1) - y) for y in ys) / len(ys)


def select_model(train):
    """Best model for these days, or None when there are too few to fit.

    Returns either a `regression` on the winning feature or an `average` —
    the flat rolling mean — when no feature cuts the baseline's error by
    BEAT_BASELINE. Falling back is a real outcome, not a safety valve.
    """
    if len(train) < MIN_TRAIN_DAYS:
        return None
    ys = [t["wh"] for t in train]
    mean_wh = sum(ys) / len(ys)
    baseline_mae = _loo_mae_mean(ys)
    common = {"days": len(train), "baseline_mae": baseline_mae,
              "clamp_max": max(ys) * CLAMP_HEADROOM if ys else 0.0}

    best = None
    for feature in CANDIDATES:
        pairs = [(t["features"].get(feature), t["wh"]) for t in train]
        pairs = [(x, y) for x, y in pairs if x is not None]
        if len(pairs) < MIN_TRAIN_DAYS:
            continue
        xs = [p[0] for p in pairs]
        fys = [p[1] for p in pairs]
        mae = _loo_mae(xs, fys)
        fit = _fit(xs, fys)
        if mae is None or fit is None:
            continue
        # strict-by-a-hair so perfectly collinear features break toward
        # CANDIDATES order rather than floating-point noise
        if best is None or mae < best["mae"] - 1e-9:
            best = {"feature": feature, "mae": mae,
                    "slope": fit[0], "intercept": fit[1]}

    if (best is not None and baseline_mae is not None
            and best["mae"] < baseline_mae * (1 - BEAT_BASELINE)):
        return {"kind": "regression", "fallback_wh": mean_wh, **common, **best}
    return {"kind": "average", "wh": mean_wh, "mae": baseline_mae,
            "feature": None, "fallback_wh": mean_wh, **common}


def predict(model, weather_day):
    """Predicted Wh for one forecast day, or None without a model."""
    if model is None:
        return None
    if model["kind"] == "average":
        value = model["wh"]
    else:
        x = weather_day.get(model["feature"])
        value = (model["fallback_wh"] if x is None
                 else model["intercept"] + model["slope"] * x)
    return max(0.0, min(value, model["clamp_max"]))


def build(rows, units, weather_days, cfg, now, **gates):
    """Everything /api/forecast serves: the model, and the days ahead."""
    today = now.date()
    observations = daily_observations(rows, units)
    train = training_set(observations, weather_days, cfg.window_days, today, **gates)
    model = select_model(train)

    future = [d for d in weather_days if d["date"] > today.isoformat()]
    days = [{"date": d["date"],
             "wh": predict(model, d),
             "sunshine_h": d.get("sunshine_duration"),
             "cloud_pct": d.get("cloud_cover_mean"),
             "rain_mm": d.get("precipitation_sum")}
            for d in future]

    return {
        "state": "ok" if model else "learning",
        "location": {"label": cfg.label,
                     "latitude": cfg.latitude, "longitude": cfg.longitude},
        "window_days": cfg.window_days,
        "training_days": len(train),
        "min_days": MIN_TRAIN_DAYS,   # so the UI can say how far off it is
        "model": model,
        "days": days,
        "tomorrow": days[0] if (days and model) else None,
    }
