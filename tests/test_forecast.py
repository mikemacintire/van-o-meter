"""Tests for the solar forecast model — pure, offline, no network.

Spec: docs/superpowers/specs/2026-08-07-solar-forecast-design.md

The model earns its place against a flat rolling average or it is not used;
most of what is tested here is that rule and the training gates that feed it.
"""

from datetime import datetime, timedelta

import pytest

from ecoflow import forecast


TZ = datetime.now().astimezone().tzinfo
SAMPLES = 48          # per synthetic day; real days log ~2880 at 30 s cadence
GATES = {"min_samples": 40, "min_span_h": 20}


def make_rows(daily_wh, unit="A", start="2026-08-01", samples=SAMPLES,
              max_soc=50.0, span_h=23.5):
    """Poller rows whose cumulative counters differentiate back to `daily_wh`.

    Each day opens at the previous day's closing counter value (no solar
    overnight) and ramps to +wh by its last sample, so history.daily_energy
    attributes exactly `wh` to that day.
    """
    day0 = datetime.fromisoformat(start).replace(tzinfo=TZ)
    rows, cum = [], 1000.0
    for offset, wh in enumerate(daily_wh):
        base = day0 + timedelta(days=offset)
        for i in range(samples):
            frac = i / (samples - 1)
            rows.append({
                "unit": unit,
                "dt": base + timedelta(hours=span_h * frac),
                "soc": max_soc if i == samples // 2 else 20.0,
                "cum_solar_wh": cum + wh * frac,
                # daily_energy differences every cumulative counter, so a row
                # has to carry them all the way the real CSV does
                "cum_ac_in_wh": 0.0, "cum_dc_in_wh": 0.0,
                "cum_ac_out_wh": 0.0, "cum_dc_out_wh": 0.0,
            })
        cum += wh
    return rows


def weather_days(start="2026-08-01", sun=(), cloud=None):
    """Daily weather records aligned to make_rows' dates."""
    day0 = datetime.fromisoformat(start).date()
    out = []
    for i, s in enumerate(sun):
        out.append({
            "date": (day0 + timedelta(days=i)).isoformat(),
            "sunshine_duration": s,
            "cloud_cover_mean": cloud[i] if cloud else 100 - s * 8,
            "shortwave_radiation_sum": None,
            "precipitation_sum": 0.0,
            "temperature_2m_max": 30.0,
        })
    return out


# ---- daily observations: combining units and grading day quality ----

def test_observations_combine_both_units():
    rows = make_rows([1000.0, 1200.0], unit="A") + make_rows([600.0, 700.0], unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    assert obs["2026-08-01"]["wh"] == pytest.approx(1600, abs=1)
    assert obs["2026-08-02"]["wh"] == pytest.approx(1900, abs=1)


def test_observations_record_sample_count_and_span():
    rows = make_rows([1000.0], unit="A") + make_rows([600.0], unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    day = obs["2026-08-01"]
    assert day["samples"] == SAMPLES        # per-unit, not the sum of both
    assert day["span_h"] == pytest.approx(23.5, abs=0.1)


def test_observations_take_the_highest_soc_across_units():
    rows = (make_rows([1000.0], unit="A", max_soc=42.0)
            + make_rows([600.0], unit="B", max_soc=100.0))
    assert forecast.daily_observations(rows, ("A", "B"))["2026-08-01"]["max_soc"] == 100.0


# ---- training gates ----

def build_training(sun, wh, **overrides):
    """Training rows for parallel sunshine/Wh sequences, all days healthy."""
    rows = make_rows(wh, unit="A") + make_rows([0.0] * len(wh), unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    obs.update(overrides.pop("obs_patch", {}))
    return forecast.training_set(
        obs, weather_days(sun=sun), window_days=30,
        today=datetime.fromisoformat("2026-08-20").replace(tzinfo=TZ).date(),
        **{**GATES, **overrides})


def test_training_keeps_healthy_days():
    train = build_training([6.0, 9.0, 12.0], [1500.0, 2000.0, 2500.0])
    assert len(train) == 3


def test_training_drops_days_with_too_few_samples():
    # a machine sleep leaves a real but hole-ridden day; it must not train
    patch = {"obs_patch": {"2026-08-02": {"wh": 2000.0, "samples": 12,
                                          "span_h": 23.0, "max_soc": 50.0}}}
    train = build_training([6.0, 9.0, 12.0], [1500.0, 2000.0, 2500.0], **patch)
    assert [t["date"] for t in train] == ["2026-08-01", "2026-08-03"]


def test_training_drops_partial_days_by_span():
    patch = {"obs_patch": {"2026-08-01": {"wh": 1500.0, "samples": 44,
                                          "span_h": 6.0, "max_soc": 50.0}}}
    train = build_training([6.0, 9.0, 12.0], [1500.0, 2000.0, 2500.0], **patch)
    assert [t["date"] for t in train] == ["2026-08-02", "2026-08-03"]


def test_training_drops_curtailed_days():
    # a full pack throttles the MPPT, so the day understates available solar
    patch = {"obs_patch": {"2026-08-03": {"wh": 2500.0, "samples": 48,
                                          "span_h": 23.0, "max_soc": 100.0}}}
    train = build_training([6.0, 9.0, 12.0], [1500.0, 2000.0, 2500.0], **patch)
    assert [t["date"] for t in train] == ["2026-08-01", "2026-08-02"]


def test_training_drops_days_with_no_weather():
    rows = make_rows([1500.0, 2000.0], unit="A") + make_rows([0.0, 0.0], unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    train = forecast.training_set(
        obs, weather_days(sun=[6.0]),   # weather for day one only
        window_days=30,
        today=datetime.fromisoformat("2026-08-20").replace(tzinfo=TZ).date(),
        **GATES)
    assert [t["date"] for t in train] == ["2026-08-01"]


def test_training_honours_the_rolling_window():
    rows = make_rows([1500.0, 2000.0, 2500.0], unit="A") + make_rows([0.0] * 3, unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    train = forecast.training_set(
        obs, weather_days(sun=[6.0, 9.0, 12.0]), window_days=2,
        today=datetime.fromisoformat("2026-08-04").replace(tzinfo=TZ).date(),
        **GATES)
    # two days back from today, and today itself is never included
    assert [t["date"] for t in train] == ["2026-08-02", "2026-08-03"]


def test_training_excludes_today_itself():
    rows = make_rows([1500.0, 2000.0], unit="A") + make_rows([0.0, 0.0], unit="B")
    obs = forecast.daily_observations(rows, ("A", "B"))
    train = forecast.training_set(
        obs, weather_days(sun=[6.0, 9.0]), window_days=30,
        today=datetime.fromisoformat("2026-08-02").replace(tzinfo=TZ).date(),
        **GATES)
    assert [t["date"] for t in train] == ["2026-08-01"]


# ---- model selection ----

def clean_training(sun, wh):
    return [{"date": f"2026-08-{i + 1:02d}", "wh": w,
             "features": {"sunshine_duration": s, "cloud_cover_mean": 100 - s * 8,
                          "shortwave_radiation_sum": None}}
            for i, (s, w) in enumerate(zip(sun, wh))]


def test_selects_a_regression_when_the_signal_is_strong():
    # dead-linear: 300 Wh baseline + 200 Wh per sunshine hour
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    model = forecast.select_model(clean_training(sun, [300 + 200 * s for s in sun]))
    assert model["kind"] == "regression"
    assert model["feature"] == "sunshine_duration"
    assert model["slope"] == pytest.approx(200, rel=0.02)
    assert model["intercept"] == pytest.approx(300, abs=20)


def test_falls_back_to_the_average_when_weather_explains_nothing():
    # yield uncorrelated with sun: no feature can beat the flat average
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    wh = [2000.0, 1400.0, 2100.0, 1350.0, 1900.0, 2050.0, 1500.0]
    model = forecast.select_model(clean_training(sun, wh))
    assert model["kind"] == "average"
    assert model["wh"] == pytest.approx(sum(wh) / len(wh), rel=0.01)


def test_a_marginal_feature_does_not_displace_the_average():
    # barely-better-than-noise must not be promoted; the margin is the guard
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    wh = [1800.0, 1500.0, 2000.0, 1450.0, 2000.0, 1900.0, 1600.0]
    model = forecast.select_model(clean_training(sun, wh))
    assert model["kind"] == "average"


def test_selection_ignores_features_that_are_never_present():
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    model = forecast.select_model(clean_training(sun, [300 + 200 * s for s in sun]))
    assert model["feature"] != "shortwave_radiation_sum"   # all None above


def test_selection_reports_its_own_accuracy():
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    model = forecast.select_model(clean_training(sun, [300 + 200 * s for s in sun]))
    assert model["mae"] < model["baseline_mae"]
    assert model["days"] == 7


def test_selection_needs_a_minimum_number_of_days():
    assert forecast.select_model(clean_training([4.0, 8.0], [1100.0, 1900.0])) is None
    assert forecast.select_model([]) is None


# ---- prediction ----

def strong_model():
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    return forecast.select_model(clean_training(sun, [300 + 200 * s for s in sun]))


def test_predicts_from_the_selected_feature():
    model = strong_model()
    day = {"date": "2026-08-21", "sunshine_duration": 7.0}
    assert forecast.predict(model, day) == pytest.approx(300 + 200 * 7, rel=0.03)


def test_prediction_falls_back_to_the_average_when_the_feature_is_missing():
    model = strong_model()
    assert forecast.predict(model, {"date": "x", "sunshine_duration": None}) == \
        pytest.approx(model["fallback_wh"], rel=0.01)


def test_prediction_never_goes_negative():
    model = strong_model()
    assert forecast.predict(model, {"date": "x", "sunshine_duration": -50.0}) == 0


def test_prediction_clamps_absurd_extrapolation():
    model = strong_model()
    huge = forecast.predict(model, {"date": "x", "sunshine_duration": 400.0})
    assert huge == pytest.approx(model["clamp_max"])


def test_average_model_predicts_the_average_regardless_of_weather():
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0]
    wh = [2000.0, 1400.0, 2100.0, 1350.0, 1900.0, 2050.0, 1500.0]
    model = forecast.select_model(clean_training(sun, wh))
    assert model["kind"] == "average"
    assert forecast.predict(model, {"date": "x", "sunshine_duration": 12.0}) == \
        pytest.approx(model["wh"], rel=0.01)


# ---- build: the whole payload ----

def full_build(n_days=8, today="2026-08-09"):
    sun = [4.0, 6.0, 8.0, 10.0, 12.0, 5.0, 9.0, 7.0][:n_days]
    wh = [300 + 200 * s for s in sun]
    rows = make_rows(wh, unit="A") + make_rows([0.0] * len(wh), unit="B")
    days = weather_days(sun=sun + [8.0, 3.0])   # two days past the logged ones
    return forecast.build(
        rows, ("A", "B"), days, forecast.Config(),
        now=datetime.fromisoformat(today).replace(tzinfo=TZ), **GATES)


def test_build_reports_learning_with_too_little_history():
    out = full_build(n_days=3)
    assert out["state"] == "learning"
    assert out["tomorrow"] is None
    assert out["training_days"] == 3


def test_build_predicts_once_there_is_enough_history():
    out = full_build()
    assert out["state"] == "ok"
    assert out["training_days"] == 8
    assert out["model"]["feature"] == "sunshine_duration"


def test_build_returns_only_future_days():
    out = full_build(today="2026-08-08")
    assert out["days"]
    assert all(d["date"] > "2026-08-08" for d in out["days"])


def test_build_tomorrow_is_the_first_future_day():
    out = full_build(today="2026-08-08")
    assert out["tomorrow"]["date"] == "2026-08-09"
    assert out["tomorrow"]["wh"] == out["days"][0]["wh"]


def test_build_carries_the_location_label():
    out = full_build()
    assert out["location"]["label"] == forecast.Config().label


def test_build_survives_having_no_weather_at_all():
    rows = make_rows([1500.0], unit="A") + make_rows([0.0], unit="B")
    out = forecast.build(rows, ("A", "B"), [], forecast.Config(),
                         now=datetime.fromisoformat("2026-08-20").replace(tzinfo=TZ),
                         **GATES)
    assert out["state"] == "learning"
    assert out["days"] == []


# ---- config ----

def test_config_round_trips(tmp_path):
    path = tmp_path / "forecast.json"
    cfg = forecast.Config(latitude=30.1, longitude=-81.5, label="Elsewhere",
                          window_days=7)
    cfg.save(path)
    assert forecast.Config.load(path) == cfg


def test_config_load_defaults_when_file_is_missing(tmp_path):
    assert forecast.Config.load(tmp_path / "nope.json") == forecast.Config()


def test_config_load_defaults_when_file_is_corrupt(tmp_path):
    path = tmp_path / "forecast.json"
    path.write_text("{ nope", encoding="utf-8")
    assert forecast.Config.load(path) == forecast.Config()


def test_config_rejects_impossible_coordinates():
    with pytest.raises(ValueError):
        forecast.Config(latitude=99.0).validate()
    with pytest.raises(ValueError):
        forecast.Config(longitude=-999.0).validate()


def test_config_rejects_a_useless_window():
    with pytest.raises(ValueError):
        forecast.Config(window_days=1).validate()


def test_config_defaults_to_butler_beach():
    cfg = forecast.Config()
    assert cfg.latitude == pytest.approx(29.7983, abs=0.01)
    assert cfg.longitude == pytest.approx(-81.267, abs=0.01)
    cfg.validate()
