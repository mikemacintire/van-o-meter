"""Tests for the dashboard's auto-balancer tick and /api/balancer endpoints.

The background thread is a thin sleep-loop around balancer_tick(), so the
tests drive the tick directly with the same FakeClient the control tests use.
"""

import json

import pytest

import dashboard
from ecoflow.balancer import Config
from test_dashboard_control import FakeClient, BASE_QUOTA


@pytest.fixture
def bal(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "VERIFY_POLL", 0)
    monkeypatch.setattr(dashboard, "VERIFY_TIMEOUT", 0.2)
    monkeypatch.setattr(dashboard, "BAL_CFG_PATH", tmp_path / "balancer.json")
    dashboard._live_cache["ts"] = 0.0
    dashboard._live_cache["payload"] = None
    dashboard._bal["status"] = {}

    def install(quota, cfg=None, apply=True):
        fake = FakeClient(quota, apply)
        monkeypatch.setattr(dashboard, "client", fake)
        dashboard._bal["cfg"] = cfg or Config(enabled=True)
        return fake, dashboard.app.test_client()
    return install


def quota(soc, ac_on):
    q = dict(BASE_QUOTA)
    q["ems.f32LcdShowSoc"] = soc
    q["inv.cfgAcEnabled"] = "1" if ac_on else "0"
    return q


# ---- tick ----

def test_tick_disabled_touches_nothing(bal):
    # "disabled" means BOTH the balancer and the emergency fallback are off
    fake, _ = bal(quota(100, False),
                  cfg=Config(enabled=False, emergency_enabled=False))
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "disabled"


def test_tick_turns_on_when_b_full(bal):
    fake, _ = bal(quota(99.5, False))
    dashboard.balancer_tick()
    # same cmd-66 shape as the manual control path: companion xboost included
    assert fake.sets == [{"sn": dashboard.UNITS["B"], "cmd_id": 66,
                          "params": {"enabled": 1, "xboost": 1}}]
    last = dashboard._bal["status"]["last_action"]
    assert last["action"] == "on"
    assert last["applied"] is True


def test_tick_idles_mid_band(bal):
    fake, _ = bal(quota(80, True))
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "transferring"


def test_tick_turns_off_at_floor(bal):
    fake, _ = bal(quota(59.8, True))
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0
    assert dashboard._bal["status"]["last_action"]["action"] == "off"


def test_tick_waits_below_start(bal):
    fake, _ = bal(quota(75, False))
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "waiting"


def test_tick_survives_cloud_error_and_reports_it(bal, monkeypatch):
    fake, _ = bal(quota(100, False))
    def boom(sn):
        raise RuntimeError("cloud fell over")
    monkeypatch.setattr(fake, "get_quota_all", boom)
    dashboard.balancer_tick()   # must not raise
    assert "cloud fell over" in dashboard._bal["status"]["error"]


def test_tick_action_invalidates_live_cache(bal):
    _, _ = bal(quota(100, False))
    dashboard._live_cache["ts"] = 9e12
    dashboard.balancer_tick()
    assert dashboard._live_cache["ts"] == 0.0


def test_tick_unapplied_command_reported(bal):
    fake, _ = bal(quota(100, False), apply=False)
    dashboard.balancer_tick()
    assert dashboard._bal["status"]["last_action"]["applied"] is False


def test_tick_logs_action_to_disk(bal, monkeypatch, tmp_path):
    # Diagnosing 2026-08-05's rogue "off" required the sender's in-memory
    # status to still be reachable — actions must survive on disk.
    log_path = tmp_path / "balancer.jsonl"
    monkeypatch.setattr(dashboard, "BAL_LOG_PATH", log_path, raising=False)
    bal(quota(99.5, False))
    dashboard.balancer_tick()
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["action"] == "on"
    assert entry["applied"] is True
    assert entry["b_soc"] == 99.5


def test_tick_without_action_logs_nothing(bal, monkeypatch, tmp_path):
    log_path = tmp_path / "balancer.jsonl"
    monkeypatch.setattr(dashboard, "BAL_LOG_PATH", log_path, raising=False)
    bal(quota(75, False))
    dashboard.balancer_tick()
    assert not log_path.exists()


def test_actuation_uses_fresh_companion_state(bal, monkeypatch):
    """The cmd-66 companion (xboost) must come from a quota fetched at
    actuation time under the control lock — not the decision-time read —
    so the balancer can't revert a manual setting changed in between."""
    fake, _ = bal(quota(99.5, False))
    reads = {"n": 0}
    real = fake.get_quota_all
    def aging_quota(sn):
        reads["n"] += 1
        q = real(sn)
        if reads["n"] > 2:   # reads 1+2 are the B+A decision reads
            q["inv.cfgAcXboost"] = "0"
        return q
    monkeypatch.setattr(fake, "get_quota_all", aging_quota)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["xboost"] == 0


# ---- endpoints ----

def test_get_balancer_returns_config_and_status(bal):
    _, http = bal(quota(80, True))
    dashboard.balancer_tick()
    body = http.get("/api/balancer").get_json()
    assert body["config"] == {"enabled": True, "start_soc": 99, "stop_soc": 60,
                              "emergency_enabled": True, "emergency_a_soc": 15,
                              "emergency_a_clear_soc": 30,
                              "emergency_b_floor": 15}
    assert body["status"]["state"] == "transferring"


def test_post_balancer_updates_and_persists(bal):
    _, http = bal(quota(80, False))
    r = http.post("/api/balancer", json={"enabled": True, "start_soc": 95,
                                         "stop_soc": 50})
    assert r.status_code == 200
    assert dashboard._bal["cfg"].start_soc == 95
    assert Config.load(dashboard.BAL_CFG_PATH).stop_soc == 50


def test_post_balancer_partial_update_keeps_rest(bal):
    _, http = bal(quota(80, False), cfg=Config(enabled=True, start_soc=97))
    r = http.post("/api/balancer", json={"enabled": False})
    assert r.status_code == 200
    assert dashboard._bal["cfg"].enabled is False
    assert dashboard._bal["cfg"].start_soc == 97


def test_post_balancer_rejects_bad_thresholds(bal):
    _, http = bal(quota(80, False))
    r = http.post("/api/balancer", json={"start_soc": 50, "stop_soc": 80})
    assert r.status_code == 400
    assert dashboard._bal["cfg"].start_soc == 99   # unchanged
