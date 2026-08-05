"""/api/history serves the 7-day drain estimate alongside the range data."""

from datetime import datetime, timedelta

import pytest

import dashboard
from ecoflow.poller import HEADER


def _row(dt, unit, **cols):
    base = dict.fromkeys(
        (c for c in HEADER if c not in ("timestamp", "unit")), None)
    base.update(cols)
    return {"timestamp": dt.isoformat(), "unit": unit, "dt": dt, **base}


def _payload(monkeypatch, rows):
    monkeypatch.setattr(dashboard.history, "load_rows", lambda path: rows)
    monkeypatch.setattr(dashboard.history, "reload_if_header_changed",
                        lambda path: None)
    dashboard._hist_cache.clear()
    return dashboard.app.test_client().get("/api/history?range=24h").get_json()


def test_history_payload_includes_drain(monkeypatch):
    now = datetime.now().astimezone()
    rows = [
        _row(now - timedelta(days=3), "A", soc=80, cum_solar_wh=0),
        _row(now - timedelta(days=3), "B", soc=50, cum_solar_wh=0),
        _row(now, "A", soc=50, cum_solar_wh=3000),
        _row(now, "B", soc=50, cum_solar_wh=0),
    ]
    payload = _payload(monkeypatch, rows)
    # 3000 Wh solar + 30% of A's 7200 Wh capacity, over 3 days
    assert payload["drain"]["wh_per_day"] == pytest.approx(1720)


def test_history_drain_null_when_insufficient(monkeypatch):
    assert _payload(monkeypatch, [])["drain"] is None
