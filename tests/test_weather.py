"""Tests for the Open-Meteo client — all offline, no live calls.

Spec: docs/superpowers/specs/2026-08-07-solar-forecast-design.md
"""

import json

import pytest

from ecoflow import weather


# A trimmed but structurally real Open-Meteo response: three past days and two
# forecast days, columnar the way the API actually returns it.
PAYLOAD = {
    "latitude": 29.8,
    "longitude": -81.25,
    "timezone": "America/New_York",
    "daily_units": {"sunshine_duration": "s", "shortwave_radiation_sum": "MJ/m²"},
    "daily": {
        "time": ["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08"],
        "sunshine_duration": [34920.0, 21600.0, 32760.0, 25200.0, 18000.0],
        "cloud_cover_mean": [49, 73, 60, 64, 87],
        "shortwave_radiation_sum": [20.7, 18.07, 25.75, 14.86, 18.34],
        "precipitation_sum": [0.0, 0.0, 0.0, 1.2, 4.4],
        "temperature_2m_max": [32.1, 31.4, 33.0, 31.8, 30.2],
    },
}


def fake_fetcher(payload=None, boom=None):
    """Stand-in for weather.fetch that records how it was called."""
    calls = []

    def fetcher(lat, lon, past_days, forecast_days):
        calls.append({"lat": lat, "lon": lon,
                      "past_days": past_days, "forecast_days": forecast_days})
        if boom:
            raise boom
        return weather.parse(payload if payload is not None else PAYLOAD)

    fetcher.calls = calls
    return fetcher


# ---- parsing ----

def test_parse_transposes_columns_into_day_records():
    days = weather.parse(PAYLOAD)
    assert [d["date"] for d in days] == [
        "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08"]
    assert days[0]["cloud_cover_mean"] == 49
    assert days[0]["shortwave_radiation_sum"] == 20.7


def test_parse_converts_sunshine_seconds_to_hours():
    # the API reports seconds; hours keep the fitted slope human-readable
    days = weather.parse(PAYLOAD)
    assert days[0]["sunshine_duration"] == pytest.approx(9.7, abs=0.01)
    assert days[1]["sunshine_duration"] == pytest.approx(6.0, abs=0.01)


def test_parse_tolerates_missing_variable():
    payload = json.loads(json.dumps(PAYLOAD))
    del payload["daily"]["cloud_cover_mean"]
    days = weather.parse(payload)
    assert days[0]["cloud_cover_mean"] is None
    assert days[0]["sunshine_duration"] is not None


def test_parse_keeps_nulls_as_none():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["daily"]["sunshine_duration"][1] = None
    days = weather.parse(payload)
    assert days[1]["sunshine_duration"] is None


def test_parse_rejects_a_payload_with_no_daily_block():
    with pytest.raises(weather.WeatherError):
        weather.parse({"error": True, "reason": "bad latitude"})


# ---- disk cache ----

def test_store_then_load_round_trips(tmp_path):
    path = tmp_path / "weather.json"
    days = weather.parse(PAYLOAD)
    weather.store(path, days, fetched_at=1000.0)
    loaded, fetched_at = weather.load(path)
    assert fetched_at == 1000.0
    assert loaded == days


def test_load_missing_file_is_empty_not_an_error(tmp_path):
    assert weather.load(tmp_path / "nope.json") == (None, None)


def test_load_corrupt_file_is_empty_not_an_error(tmp_path):
    path = tmp_path / "weather.json"
    path.write_text("{not json", encoding="utf-8")
    assert weather.load(path) == (None, None)


# ---- refresh: the cache/network policy ----

def test_refresh_fetches_when_no_cache_exists(tmp_path):
    fetcher = fake_fetcher()
    days, fetched_at = weather.refresh(
        tmp_path / "w.json", 29.8, -81.25, past_days=30, forecast_days=7,
        max_age_s=3600, now=5000.0, fetcher=fetcher)
    assert len(fetcher.calls) == 1
    assert fetched_at == 5000.0
    assert len(days) == 5


def test_refresh_serves_cache_while_fresh(tmp_path):
    path = tmp_path / "w.json"
    weather.store(path, weather.parse(PAYLOAD), fetched_at=5000.0)
    fetcher = fake_fetcher()
    days, fetched_at = weather.refresh(
        path, 29.8, -81.25, past_days=30, forecast_days=7,
        max_age_s=3600, now=5000.0 + 1800, fetcher=fetcher)
    assert fetcher.calls == []          # inside the TTL: no network at all
    assert fetched_at == 5000.0
    assert len(days) == 5


def test_refresh_refetches_once_the_cache_ages_out(tmp_path):
    path = tmp_path / "w.json"
    weather.store(path, weather.parse(PAYLOAD), fetched_at=5000.0)
    fetcher = fake_fetcher()
    _, fetched_at = weather.refresh(
        path, 29.8, -81.25, past_days=30, forecast_days=7,
        max_age_s=3600, now=5000.0 + 3601, fetcher=fetcher)
    assert len(fetcher.calls) == 1
    assert fetched_at == 5000.0 + 3601


def test_refresh_falls_back_to_stale_cache_when_offline(tmp_path):
    # the whole point of the disk cache: the truck loses its uplink constantly
    path = tmp_path / "w.json"
    weather.store(path, weather.parse(PAYLOAD), fetched_at=5000.0)
    fetcher = fake_fetcher(boom=weather.WeatherError("no route to host"))
    days, fetched_at = weather.refresh(
        path, 29.8, -81.25, past_days=30, forecast_days=7,
        max_age_s=3600, now=99999.0, fetcher=fetcher)
    assert len(fetcher.calls) == 1     # it tried
    assert fetched_at == 5000.0        # and kept the old data, honestly dated
    assert len(days) == 5


def test_refresh_raises_when_offline_with_no_cache(tmp_path):
    fetcher = fake_fetcher(boom=weather.WeatherError("no route to host"))
    with pytest.raises(weather.WeatherError):
        weather.refresh(tmp_path / "w.json", 29.8, -81.25, past_days=30,
                        forecast_days=7, max_age_s=3600, now=1.0, fetcher=fetcher)


def test_refresh_writes_the_cache_it_fetched(tmp_path):
    path = tmp_path / "w.json"
    weather.refresh(path, 29.8, -81.25, past_days=30, forecast_days=7,
                    max_age_s=3600, now=5000.0, fetcher=fake_fetcher())
    days, fetched_at = weather.load(path)
    assert fetched_at == 5000.0
    assert len(days) == 5
