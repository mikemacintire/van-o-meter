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

    def install(quota, cfg=None, apply=True, a_soc=50):
        # FakeClient serves one quota dict for every SN, but the emergency and
        # A-full layers read A: give A its own SOC, defaulting to a mid level
        # where neither layer engages. B keeps the dict that commands apply to.
        fake = FakeClient(quota, apply)
        real = fake.get_quota_all

        def per_sn(sn):
            q = real(sn)
            if sn == dashboard.UNITS["A"]:
                q["ems.f32LcdShowSoc"] = a_soc
            return q
        monkeypatch.setattr(fake, "get_quota_all", per_sn)
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


# ---- A-full cutoff (2026-08-06-a-full-cutoff-design.md) ----

def test_tick_cuts_transfer_when_a_is_full(bal):
    # B at 80% would normally keep transferring down to stop_soc
    fake, _ = bal(quota(80, True), a_soc=99)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0
    last = dashboard._bal["status"]["last_action"]
    assert last["action"] == "off"
    assert "full" in last["reason"].lower()
    assert last["a_soc"] == 99


def test_tick_blocks_start_while_a_is_full(bal):
    # B full enough to start, but A has no room — must not chatter it on
    fake, _ = bal(quota(99.5, False), a_soc=95)
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "a-full"


def test_tick_starts_once_a_falls_below_resume(bal):
    fake, _ = bal(quota(99.5, False), a_soc=89)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 1


def test_tick_leaves_transfer_alone_mid_band(bal):
    # 95 blocks a start but must not cut a transfer already under way
    fake, _ = bal(quota(80, True), a_soc=95)
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "transferring"


def test_a_full_outranks_emergency_hold(bal):
    # pathological config: rescue band overlapping the full mark. Cutting
    # power into a full battery is never the harmful call.
    cfg = Config(enabled=True, emergency_a_soc=98, emergency_a_clear_soc=100)
    fake, _ = bal(quota(80, True), cfg=cfg, a_soc=99)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0


def test_a_full_cuts_even_with_normal_balancer_off(bal):
    # emergency-only mode: before this rule a fired rescue ran B down to its
    # floor no matter how full A got, because nothing ever said stop
    cfg = Config(enabled=False, emergency_enabled=True)
    fake, _ = bal(quota(80, True), cfg=cfg, a_soc=100)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0


def test_a_read_failure_leaves_transfer_alone(bal, monkeypatch):
    # cloud flap must not cut a healthy transfer (docs/api.md)
    fake, _ = bal(quota(80, True))
    real = fake.get_quota_all

    def flaky(sn):
        if sn == dashboard.UNITS["A"]:
            raise RuntimeError("A unreachable")
        return real(sn)
    monkeypatch.setattr(fake, "get_quota_all", flaky)
    dashboard.balancer_tick()
    assert fake.sets == []
    assert "A unreachable" in dashboard._bal["status"]["a_error"]


def test_a_soc_read_even_when_emergency_disabled(bal):
    # the A-full layer has no toggle, so A's SOC is no longer optional
    cfg = Config(enabled=True, emergency_enabled=False)
    fake, _ = bal(quota(80, True), cfg=cfg, a_soc=99)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0


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
                              "emergency_b_floor": 15, "a_full_soc": 99,
                              "a_full_resume_soc": 90}
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
