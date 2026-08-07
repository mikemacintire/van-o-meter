"""/api/forecast: caching, off-grid degradation, and isolation from history.

Spec: docs/superpowers/specs/2026-08-07-solar-forecast-design.md
"""

from datetime import datetime, timedelta

import pytest

import dashboard
from ecoflow import weather
from ecoflow.poller import HEADER


def _row(dt, unit, **cols):
    base = dict.fromkeys(
        (c for c in HEADER if c not in ("timestamp", "unit")), None)
    base.update(cols)
    return {"timestamp": dt.isoformat(), "unit": unit, "dt": dt, **base}


def _weather_days(n=10):
    """Weather spanning yesterday-ish through several days ahead."""
    today = datetime.now().astimezone().date()
    return [{"date": (today - timedelta(days=n // 2) + timedelta(days=i)).isoformat(),
             "sunshine_duration": 6.0 + i, "cloud_cover_mean": 50,
             "shortwave_radiation_sum": 18.0, "precipitation_sum": 0.0,
             "temperature_2m_max": 30.0}
            for i in range(n)]


@pytest.fixture(autouse=True)
def _clear_cache():
    dashboard._fc_cache.update(ts=0.0, payload=None)
    yield
    dashboard._fc_cache.update(ts=0.0, payload=None)


def _get(monkeypatch, rows=(), days=None, boom=None):
    calls = []

    def fake_refresh(path, lat, lon, past_days, forecast_days, max_age_s,
                     now=None, fetcher=None):
        calls.append({"lat": lat, "lon": lon, "past_days": past_days})
        if boom:
            raise boom
        return (days if days is not None else _weather_days()), 1000.0

    monkeypatch.setattr(dashboard.weather, "refresh", fake_refresh)
    monkeypatch.setattr(dashboard.history, "load_rows", lambda path: list(rows))
    monkeypatch.setattr(dashboard.history, "reload_if_header_changed",
                        lambda path: None)
    response = dashboard.app.test_client().get("/api/forecast")
    return response, calls


def test_forecast_returns_days_and_location(monkeypatch):
    response, _ = _get(monkeypatch)
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["location"]["label"]
    assert payload["days"]                       # future days present
    assert "weather_age_s" in payload


def test_forecast_reports_learning_without_logged_history(monkeypatch):
    payload = _get(monkeypatch, rows=[])[0].get_json()
    assert payload["state"] == "learning"
    assert payload["tomorrow"] is None


def test_forecast_degrades_to_unavailable_when_offline_and_uncached(monkeypatch):
    payload = _get(monkeypatch, boom=weather.WeatherError("no route"))[0].get_json()
    assert payload["state"] == "unavailable"
    assert "no route" in payload["error"]
    assert payload["location"]["label"]          # still says where it meant to look


def test_forecast_marks_stale_weather(monkeypatch):
    # fetched_at is pinned at epoch 1000, so by now it is years old
    payload = _get(monkeypatch)[0].get_json()
    assert payload["weather_stale"] is True


def test_forecast_caches_between_requests(monkeypatch):
    _, calls = _get(monkeypatch)
    assert len(calls) == 1
    dashboard.app.test_client().get("/api/forecast")   # second hit, same TTL
    assert len(calls) == 1


def test_forecast_requests_within_open_meteo_past_days_limit(monkeypatch):
    _, calls = _get(monkeypatch)
    assert 0 < calls[0]["past_days"] <= dashboard.PAST_DAYS_MAX


def test_forecast_failure_does_not_touch_history(monkeypatch):
    """The whole reason this is a separate endpoint."""
    _get(monkeypatch, boom=weather.WeatherError("down"))
    dashboard._hist_cache.clear()
    response = dashboard.app.test_client().get("/api/history?range=24h")
    assert response.status_code == 200
    assert "series" in response.get_json()
