"""/api/weather: current conditions for the topbar, off-grid degradation."""

import pytest

import dashboard
from ecoflow import weather


def _get(monkeypatch, boom=None, fetched_at=1000.0, now=1010.0):
    calls = []

    def fake_refresh(path, lat, lon, max_age_s, now=None, fetcher=None):
        calls.append({"lat": lat, "lon": lon, "max_age_s": max_age_s})
        if boom:
            raise boom
        return {"temp_c": 28.2, "code": 1, "is_day": True}, fetched_at

    monkeypatch.setattr(dashboard.weather, "refresh_current", fake_refresh)
    monkeypatch.setattr(dashboard.time, "time", lambda: now)
    response = dashboard.app.test_client().get("/api/weather")
    return response, calls


def test_weather_returns_current_conditions(monkeypatch):
    response, calls = _get(monkeypatch)
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["temp_c"] == 28.2
    assert payload["code"] == 1
    assert payload["is_day"] is True
    assert payload["age_s"] == 10
    assert payload["stale"] is False
    assert len(calls) == 1


def test_weather_marks_old_data_stale(monkeypatch):
    payload = _get(monkeypatch, fetched_at=1000.0,
                   now=1000.0 + dashboard.WEATHER_NOW_STALE_S + 1)[0].get_json()
    assert payload["stale"] is True


def test_weather_degrades_to_unavailable_when_offline_and_uncached(monkeypatch):
    payload = _get(monkeypatch, boom=weather.WeatherError("no route"))[0].get_json()
    assert payload["state"] == "unavailable"
    assert "no route" in payload["error"]
