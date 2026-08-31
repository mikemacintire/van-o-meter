"""Open-Meteo daily weather — the only part of the forecast feature with I/O.

One call returns both halves of the problem: `past_days` of weather to train
against the poller's logged solar, and `forecast_days` ahead to predict from.
Free, no API key, no account.

Using this one endpoint for *both* halves is deliberate. Open-Meteo also serves
an ERA5 reanalysis archive that fits our measurements better — but ERA5 does
not exist for tomorrow, and training on one model while predicting from another
is train/serve skew (the two disagreed by up to 6 MJ/m² on the same day during
testing). Train on what you predict with.

Responses persist to disk: the truck is off-grid and drops its uplink, so a
failed fetch serves the last good payload with its true age attached rather
than nothing at all.

Spec: docs/superpowers/specs/2026-08-07-solar-forecast-design.md
"""

import json
import time
from pathlib import Path

import requests

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 20
# Every candidate predictor the model may choose between, plus the two it will
# almost certainly reject — kept because they cost nothing extra in the same
# request and let the History card show *why* the winner won.
DAILY_VARS = [
    "sunshine_duration",
    "cloud_cover_mean",
    "shortwave_radiation_sum",
    "precipitation_sum",
    "temperature_2m_max",
]
# Day boundaries must line up with history.daily_energy, which buckets by the
# machine's local date. "auto" resolves the timezone from the coordinates —
# correct here because the machine logging the samples sits at those
# coordinates, and it follows the truck if it moves.
TIMEZONE = "auto"


class WeatherError(Exception):
    pass


def parse(payload):
    """Open-Meteo's columnar `daily` block -> one dict per day.

    Missing variables and null entries become None rather than raising: the
    model treats a feature it cannot see as simply unavailable that day.
    """
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict) or "time" not in daily:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise WeatherError(f"no daily block in response: {reason or payload}")
    days = []
    for i, date in enumerate(daily["time"]):
        row = {"date": date}
        for var in DAILY_VARS:
            column = daily.get(var)
            value = column[i] if column is not None and i < len(column) else None
            # sunshine_duration ships as seconds; hours keep the fitted slope
            # readable as "Wh per hour of sun".
            if var == "sunshine_duration" and value is not None:
                value = value / 3600
            row[var] = value
        days.append(row)
    return days


def fetch(lat, lon, past_days, forecast_days):
    """Live call. Raises WeatherError on anything that isn't a usable payload."""
    try:
        response = requests.get(
            ENDPOINT,
            params={"latitude": lat, "longitude": lon,
                    "daily": ",".join(DAILY_VARS),
                    "past_days": past_days, "forecast_days": forecast_days,
                    "timezone": TIMEZONE},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        raise WeatherError(f"open-meteo fetch failed: {e}") from e
    return parse(payload)


def parse_current(payload):
    """Open-Meteo's `current` block -> {temp_c, code, is_day}."""
    current = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(current, dict) or "temperature_2m" not in current:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise WeatherError(f"no current block in response: {reason or payload}")
    return {"temp_c": current.get("temperature_2m"),
            "code": current.get("weather_code"),
            "is_day": bool(current.get("is_day"))}


def fetch_current(lat, lon):
    """Live current-conditions call. Raises WeatherError on anything unusable."""
    try:
        response = requests.get(
            ENDPOINT,
            params={"latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code,is_day",
                    "timezone": TIMEZONE},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        raise WeatherError(f"open-meteo fetch failed: {e}") from e
    return parse_current(payload)


def load(path):
    """(days, fetched_at) from disk, or (None, None) if absent or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data["days"], float(data["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def store(path, days, fetched_at):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": fetched_at, "days": days}),
                    encoding="utf-8")


def refresh(path, lat, lon, past_days, forecast_days, max_age_s,
            now=None, fetcher=None):
    """Cached fetch. Returns (days, fetched_at) — fetched_at is the honest age.

    Inside the TTL the cache is served without touching the network. Once it
    ages out a fetch is attempted, and if that fails the stale cache is served
    anyway, still stamped with when it was really fetched. Only a failure with
    nothing cached raises.
    """
    now = time.time() if now is None else now
    fetcher = fetcher or fetch
    cached, fetched_at = load(path)
    if cached is not None and fetched_at is not None and now - fetched_at < max_age_s:
        return cached, fetched_at
    try:
        days = fetcher(lat, lon, past_days, forecast_days)
    except WeatherError:
        if cached is None:
            raise
        return cached, fetched_at
    store(path, days, now)
    return days, now


def refresh_current(path, lat, lon, max_age_s, now=None, fetcher=None):
    """Cached current-conditions fetch: same policy as refresh(), own file.

    Kept separate from the daily cache because the two age differently — the
    topbar temperature goes stale in minutes where tomorrow's forecast holds
    for hours — and one must not evict the other.
    """
    now = time.time() if now is None else now
    fetcher = fetcher or fetch_current
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached, fetched_at = data["current"], float(data["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        cached, fetched_at = None, None
    if cached is not None and fetched_at is not None and now - fetched_at < max_age_s:
        return cached, fetched_at
    try:
        current = fetcher(lat, lon)
    except WeatherError:
        if cached is None:
            raise
        return cached, fetched_at
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": now, "current": current}),
                    encoding="utf-8")
    return current, now
