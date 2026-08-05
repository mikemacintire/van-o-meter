"""Tests for the emergency fallback: force B->A transfer when A runs low.

When A drops to <= emergency_a_soc (default 15), B's AC output is forced on
regardless of B's fill level, and stays on (ignoring the normal stop_soc rule)
until A recovers to emergency_a_clear_soc (default 30) or B drains to
emergency_b_floor (default 15). A small re-arm margin prevents relay chatter
at the floor. The layer is stateless, derived from live readback each tick,
same as the normal balancer.
"""

import pytest

import dashboard
from ecoflow.balancer import Config, EMERGENCY_REARM, emergency_action
from test_dashboard_control import FakeClient, BASE_QUOTA


CFG = Config(enabled=True)  # emergency defaults: 15 trigger / 30 clear / 15 floor


# ---- emergency_action truth table ----

def test_triggers_on_when_a_low():
    assert emergency_action(15, 50, ac_on=False, cfg=CFG) == "on"
    assert emergency_action(3, 50, ac_on=False, cfg=CFG) == "on"


def test_no_trigger_when_a_ok():
    assert emergency_action(15.1, 50, ac_on=False, cfg=CFG) is None
    assert emergency_action(80, 50, ac_on=False, cfg=CFG) is None


def test_no_trigger_when_b_at_floor():
    assert emergency_action(10, 15, ac_on=False, cfg=CFG) is None
    assert emergency_action(10, 5, ac_on=False, cfg=CFG) is None


def test_rearm_margin_prevents_chatter_at_floor():
    # B just above the floor must NOT re-trigger; only floor + margin does.
    assert emergency_action(10, 15.5, ac_on=False, cfg=CFG) is None
    assert emergency_action(10, 15 + EMERGENCY_REARM, ac_on=False, cfg=CFG) == "on"


def test_holds_on_while_a_below_clear():
    # This is the override: normal rules would cut at stop_soc=60.
    assert emergency_action(20, 40, ac_on=True, cfg=CFG) == "hold"
    assert emergency_action(29.9, 55, ac_on=True, cfg=CFG) == "hold"


def test_defers_to_normal_rules_once_a_recovers():
    assert emergency_action(30, 40, ac_on=True, cfg=CFG) is None
    assert emergency_action(80, 90, ac_on=True, cfg=CFG) is None


def test_cuts_off_at_b_floor_even_if_a_still_low():
    assert emergency_action(10, 15, ac_on=True, cfg=CFG) == "off"
    assert emergency_action(10, 12, ac_on=True, cfg=CFG) == "off"


def test_unknown_socs_do_nothing():
    assert emergency_action(None, 50, ac_on=False, cfg=CFG) is None
    assert emergency_action(10, None, ac_on=False, cfg=CFG) is None
    assert emergency_action(None, None, ac_on=True, cfg=CFG) is None


# ---- config ----

def test_emergency_defaults():
    cfg = Config()
    assert cfg.emergency_enabled is True
    assert cfg.emergency_a_soc == 15
    assert cfg.emergency_a_clear_soc == 30
    assert cfg.emergency_b_floor == 15


def test_config_round_trip_with_emergency_fields(tmp_path):
    path = tmp_path / "balancer.json"
    Config(emergency_enabled=False, emergency_a_soc=20,
           emergency_a_clear_soc=40, emergency_b_floor=10).save(path)
    cfg = Config.load(path)
    assert cfg.emergency_enabled is False
    assert (cfg.emergency_a_soc, cfg.emergency_a_clear_soc,
            cfg.emergency_b_floor) == (20, 40, 10)


def test_load_pre_emergency_file_keeps_old_settings_and_gains_defaults(tmp_path):
    # The live data/balancer.json predates this feature.
    path = tmp_path / "balancer.json"
    path.write_text('{"enabled": true, "start_soc": 95, "stop_soc": 50}',
                    encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.enabled, cfg.start_soc, cfg.stop_soc) == (True, 95, 50)
    assert cfg.emergency_enabled is True
    assert cfg.emergency_a_soc == 15


def test_validate_rejects_bad_emergency_thresholds():
    with pytest.raises(ValueError):
        Config(emergency_a_soc=30, emergency_a_clear_soc=30).validate()
    with pytest.raises(ValueError):
        Config(emergency_a_soc=40, emergency_a_clear_soc=25).validate()
    with pytest.raises(ValueError):
        Config(emergency_b_floor=0).validate()
    with pytest.raises(ValueError):
        Config(emergency_b_floor=100).validate()


# ---- dashboard tick with two units ----

class TwoUnitClient(FakeClient):
    """FakeClient that serves a separate quota for Unit A."""

    def __init__(self, b_quota, a_quota, apply=True):
        super().__init__(b_quota, apply)
        self.a_quota = dict(a_quota)
        self.a_fail = False

    def get_quota_all(self, sn):
        if sn == dashboard.UNITS["A"]:
            if self.a_fail:
                raise RuntimeError("A quota unavailable")
            return dict(self.a_quota)
        return dict(self.quota)


@pytest.fixture
def emg(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "VERIFY_POLL", 0)
    monkeypatch.setattr(dashboard, "VERIFY_TIMEOUT", 0.2)
    monkeypatch.setattr(dashboard, "BAL_CFG_PATH", tmp_path / "balancer.json")
    dashboard._live_cache["ts"] = 0.0
    dashboard._live_cache["payload"] = None
    dashboard._bal["status"] = {}

    def install(b_soc, a_soc, ac_on, cfg=None, apply=True):
        b = dict(BASE_QUOTA)
        b["ems.f32LcdShowSoc"] = b_soc
        b["inv.cfgAcEnabled"] = "1" if ac_on else "0"
        a = dict(BASE_QUOTA)
        a["ems.f32LcdShowSoc"] = a_soc
        fake = TwoUnitClient(b, a, apply)
        monkeypatch.setattr(dashboard, "client", fake)
        dashboard._bal["cfg"] = cfg or Config(enabled=True)
        return fake, dashboard.app.test_client()
    return install


def test_tick_emergency_turns_on_despite_b_not_full(emg):
    fake, _ = emg(b_soc=50, a_soc=12, ac_on=False)
    dashboard.balancer_tick()
    assert fake.sets == [{"sn": dashboard.UNITS["B"], "cmd_id": 66,
                          "params": {"enabled": 1, "xboost": 1}}]
    assert dashboard._bal["status"]["state"] == "emergency"
    assert "emergency" in dashboard._bal["status"]["last_action"]["reason"].lower()


def test_tick_emergency_hold_overrides_normal_stop(emg):
    # Normal rules say off at B <= 60; emergency keeps feeding while A < 30.
    fake, _ = emg(b_soc=40, a_soc=20, ac_on=True)
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "emergency"


def test_tick_emergency_cuts_at_b_floor(emg):
    fake, _ = emg(b_soc=14.5, a_soc=12, ac_on=True)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0


def test_tick_normal_rules_resume_after_a_recovers(emg):
    # A back to 45%: emergency defers, normal cuts at B <= 60.
    fake, _ = emg(b_soc=40, a_soc=45, ac_on=True)
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 0
    assert dashboard._bal["status"]["state"] == "waiting"


def test_tick_emergency_disabled_falls_back_to_normal(emg):
    fake, _ = emg(b_soc=50, a_soc=10, ac_on=False,
                  cfg=Config(enabled=True, emergency_enabled=False))
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "waiting"


def test_tick_emergency_works_with_normal_balancer_disabled(emg):
    fake, _ = emg(b_soc=50, a_soc=10, ac_on=False, cfg=Config(enabled=False))
    dashboard.balancer_tick()
    assert fake.sets[0]["params"]["enabled"] == 1
    assert dashboard._bal["status"]["state"] == "emergency"


def test_tick_both_disabled_is_disabled(emg):
    fake, _ = emg(b_soc=100, a_soc=10, ac_on=False,
                  cfg=Config(enabled=False, emergency_enabled=False))
    dashboard.balancer_tick()
    assert fake.sets == []
    assert dashboard._bal["status"]["state"] == "disabled"


def test_tick_a_read_failure_degrades_to_normal_rules(emg):
    fake, _ = emg(b_soc=100, a_soc=10, ac_on=False)
    fake.a_fail = True
    dashboard.balancer_tick()   # must not raise; normal start at B=100 still fires
    assert fake.sets[0]["params"]["enabled"] == 1
    assert "A quota unavailable" in dashboard._bal["status"]["a_error"]


def test_tick_b_quota_none_reports_clearly(emg):
    # The cloud flap serves data:null; the old AttributeError text was cryptic.
    fake, _ = emg(b_soc=50, a_soc=12, ac_on=False)
    fake.quota = None
    monkey_get = fake.get_quota_all

    def get(sn):
        if sn == dashboard.UNITS["B"]:
            return None
        return monkey_get(sn)
    fake.get_quota_all = get
    dashboard.balancer_tick()
    assert dashboard._bal["status"]["state"] == "error"
    assert "quota unavailable" in dashboard._bal["status"]["error"]


def test_tick_a_quota_none_degrades_to_normal_rules(emg):
    fake, _ = emg(b_soc=100, a_soc=12, ac_on=False)
    real_get = TwoUnitClient.get_quota_all

    def get(sn):
        if sn == dashboard.UNITS["A"]:
            return None
        return real_get(fake, sn)
    fake.get_quota_all = get
    dashboard.balancer_tick()   # A data missing -> emergency inert, normal fires
    assert fake.sets[0]["params"]["enabled"] == 1
    assert dashboard._bal["status"]["a_soc"] is None


def test_tick_reports_a_soc(emg):
    _, _ = emg(b_soc=80, a_soc=55, ac_on=False)
    dashboard.balancer_tick()
    assert dashboard._bal["status"]["a_soc"] == 55


# ---- endpoints ----

def test_get_balancer_includes_emergency_config(emg):
    _, http = emg(b_soc=80, a_soc=55, ac_on=False)
    body = http.get("/api/balancer").get_json()
    assert body["config"]["emergency_enabled"] is True
    assert body["config"]["emergency_a_soc"] == 15
    assert body["config"]["emergency_b_floor"] == 15


def test_post_balancer_updates_emergency_fields(emg):
    _, http = emg(b_soc=80, a_soc=55, ac_on=False)
    r = http.post("/api/balancer", json={"emergency_a_soc": 20,
                                         "emergency_b_floor": 10})
    assert r.status_code == 200
    assert dashboard._bal["cfg"].emergency_a_soc == 20
    assert Config.load(dashboard.BAL_CFG_PATH).emergency_b_floor == 10
    assert dashboard._bal["cfg"].start_soc == 99   # untouched


def test_post_balancer_rejects_bad_emergency_thresholds(emg):
    _, http = emg(b_soc=80, a_soc=55, ac_on=False)
    r = http.post("/api/balancer", json={"emergency_a_soc": 50,
                                         "emergency_a_clear_soc": 40})
    assert r.status_code == 400
    assert dashboard._bal["cfg"].emergency_a_soc == 15   # unchanged
