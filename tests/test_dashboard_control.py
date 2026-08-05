"""Tests for the dashboard control endpoints, with a fake cloud client."""

import pytest

import dashboard
from ecoflow.client import EcoFlowApiError


class FakeClient:
    """Applies commands to an in-memory quota dict via the readback field."""

    READBACK = {66: {"enabled": "inv.cfgAcEnabled", "xboost": "inv.cfgAcXboost"},
                38: {"enabled": "pd.beepState"},
                49: {"maxChgSoc": "ems.maxChargeSoc"}}

    def __init__(self, quota, apply=True, online=1):
        self.quota = dict(quota)
        self.apply = apply
        self.online = online
        self.sets = []

    def get_quota_all(self, sn):
        return dict(self.quota)

    def get_device_list(self):
        return [{"sn": sn, "online": self.online} for sn in dashboard.UNITS.values()]

    def set_quota(self, sn, cmd_id, **params):
        self.sets.append({"sn": sn, "cmd_id": cmd_id, "params": params})
        if self.apply:
            for pname, field in self.READBACK.get(cmd_id, {}).items():
                if pname in params:
                    self.quota[field] = str(params[pname])  # API returns strings
        return {}


@pytest.fixture
def web(monkeypatch):
    # No real sleeping or cloud calls in tests.
    monkeypatch.setattr(dashboard, "VERIFY_POLL", 0)
    monkeypatch.setattr(dashboard, "VERIFY_TIMEOUT", 0.2)
    dashboard._live_cache["ts"] = 0.0
    dashboard._live_cache["payload"] = None

    def install(quota, apply=True, online=1):
        fake = FakeClient(quota, apply, online)
        monkeypatch.setattr(dashboard, "client", fake)
        return fake, dashboard.app.test_client()
    return install


BASE_QUOTA = {"inv.cfgAcEnabled": "0", "inv.cfgAcXboost": "1",
              "pd.beepState": "0", "ems.maxChargeSoc": "100"}


def test_controls_registry_endpoint(web):
    _, http = web(BASE_QUOTA)
    reg = http.get("/api/controls").get_json()
    assert {e["key"] for e in reg} >= {"ac_out", "quiet", "max_chg_soc"}


def test_control_happy_path_verifies_readback(web):
    fake, http = web(BASE_QUOTA)
    r = http.post("/api/control", json={"unit": "B", "key": "ac_out", "value": 1})
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied"] is True
    assert body["readback"] == "1"
    # id 66 must carry the hardware's current xboost alongside enabled
    assert fake.sets == [{"sn": dashboard.UNITS["B"], "cmd_id": 66,
                          "params": {"enabled": 1, "xboost": 1}}]


def test_control_invalidates_live_cache(web):
    _, http = web(BASE_QUOTA)
    dashboard._live_cache["ts"] = 9e12   # pretend the cache is fresh
    http.post("/api/control", json={"unit": "A", "key": "quiet", "value": 1})
    assert dashboard._live_cache["ts"] == 0.0


def test_control_reports_not_applied_when_hardware_ignores(web):
    _, http = web(BASE_QUOTA, apply=False)
    r = http.post("/api/control", json={"unit": "B", "key": "ac_out", "value": 1})
    body = r.get_json()
    assert r.status_code == 200
    assert body["applied"] is False
    assert body["readback"] == "0"


def test_control_noop_confirms_immediately(web):
    fake, http = web(BASE_QUOTA, apply=False)   # even with a dead apply path
    r = http.post("/api/control", json={"unit": "B", "key": "ac_out", "value": 0})
    assert r.get_json()["applied"] is True      # already 0 — nothing to wait for
    assert len(fake.sets) == 1                  # but the command was still sent


def test_control_rejects_bad_input(web):
    _, http = web(BASE_QUOTA)
    cases = [
        {"unit": "C", "key": "ac_out", "value": 1},        # unknown unit
        {"unit": "A", "key": "warp_drive", "value": 1},    # unknown key
        {"unit": "A", "key": "ac_out", "value": 5},        # bad toggle value
        {"unit": "A", "key": "max_chg_soc", "value": 20},  # below slider min
    ]
    for payload in cases:
        assert http.post("/api/control", json=payload).status_code == 400, payload


def test_control_1008_on_offline_unit_names_the_real_cause(web, monkeypatch):
    # EcoFlow answers "1008 check your params" for writes to an offline unit
    # (found live 2026-08-03) — the endpoint must translate that.
    fake, http = web(BASE_QUOTA, online=0)
    def bounce(sn, cmd_id, **params):
        raise EcoFlowApiError("API code 1008: request fail,please check your params")
    monkeypatch.setattr(fake, "set_quota", bounce)
    r = http.post("/api/control", json={"unit": "A", "key": "quiet", "value": 1})
    assert r.status_code == 502
    assert "offline" in r.get_json()["error"]


def test_control_1008_on_online_unit_stays_a_param_error(web, monkeypatch):
    fake, http = web(BASE_QUOTA, online=1)
    def bounce(sn, cmd_id, **params):
        raise EcoFlowApiError("API code 1008: request fail,please check your params")
    monkeypatch.setattr(fake, "set_quota", bounce)
    r = http.post("/api/control", json={"unit": "A", "key": "quiet", "value": 1})
    assert r.status_code == 502
    assert "1008" in r.get_json()["error"]


def test_live_payload_tracks_data_age(web, monkeypatch):
    monkeypatch.setattr(dashboard, "LIVE_TTL", -1)   # force refetch every call
    fake, http = web(BASE_QUOTA)
    dashboard._freshness.clear()

    d1 = http.get("/api/live").get_json()
    assert d1["units"]["A"]["data_age_s"] == 0       # first sight counts as fresh

    # unchanged payload -> age grows from the recorded change time
    dashboard._freshness["A"]["ts"] -= 400
    d2 = http.get("/api/live").get_json()
    assert d2["units"]["A"]["data_age_s"] >= 400

    # any movement in the payload -> age snaps back to 0
    fake.quota["pd.wattsOutSum"] = 999
    d3 = http.get("/api/live").get_json()
    assert d3["units"]["A"]["data_age_s"] == 0


def test_live_payload_carries_online_flag(web):
    _, http = web(BASE_QUOTA, online=0)
    d = http.get("/api/live").get_json()
    assert d["units"]["A"]["online"] == 0
    assert d["units"]["B"]["online"] == 0


def test_control_surfaces_api_error_as_502(web, monkeypatch):
    fake, http = web(BASE_QUOTA)
    def boom(sn, cmd_id, **params):
        raise EcoFlowApiError("API code 1008: request fail")
    monkeypatch.setattr(fake, "set_quota", boom)
    r = http.post("/api/control", json={"unit": "B", "key": "ac_out", "value": 1})
    assert r.status_code == 502
    assert "1008" in r.get_json()["error"]


def test_live_payload_carries_settings_and_pack_watts():
    quota = dict(BASE_QUOTA)
    quota.update({
        "ems.bms0Online": 3, "ems.bms1Online": 3,
        "bmsMaster.remainCap": 40000, "bmsMaster.fullCap": 80000,
        "bmsMaster.inputWatts": 0, "bmsMaster.outputWatts": 120,
        "bmsSlave1.remainCap": 20000, "bmsSlave1.fullCap": 80000,
        "bmsSlave1.inputWatts": 47, "bmsSlave1.outputWatts": 0,
    })
    u = dashboard.normalize(quota)
    assert u["settings"]["ac_out"] == "0"
    assert u["settings"]["max_chg_soc"] == "100"
    assert [p["in_w"] for p in u["packs"]] == [0, 47]
    assert [p["out_w"] for p in u["packs"]] == [120, 0]
