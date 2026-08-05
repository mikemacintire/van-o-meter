"""Tests for the BLE mapping layer and the dashboard's BLE fallback.

No radio involved: the mapping is pure, and the dashboard tests inject a
fake BLE client. Live-path validation is scripts/ble_poc.py (needs range).
"""

import pytest

import dashboard
from ecoflow import controls
from ecoflow.ble_client import _CONTROL_MAP, BleError, BleUnsupported, snapshot_to_quota
from ecoflow.client import EcoFlowApiError


class FakeDev:
    """Bare attribute holder standing in for an eflib Device."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_snapshot_maps_names_and_scalings():
    dev = FakeDev(battery_level=88.25, battery_charge_limit_min=0,
                  battery_charge_limit_max=100, ac_output_power=432,
                  ac_input_power=0, ac_input_voltage=121.5, ac_ports=True,
                  ac_charging_speed=1800, dc_input_power=81.0,
                  dc_input_voltage=42.3, dc_input_current=1.91,
                  dc_output_power=0, dc_12v_port=False,
                  input_power=81, output_power=432,
                  battery_1_enabled=True, battery_1_battery_level=32.2,
                  battery_2_enabled=False)
    q = snapshot_to_quota(dev)
    assert q["ems.f32LcdShowSoc"] == 88.25
    assert q["inv.cfgAcEnabled"] == 1
    assert q["mppt.inWatts"] == 810          # cloud serves 0.1 W units
    assert q["mppt.inVol"] == 423
    assert q["mppt.inAmp"] == 191
    assert q["inv.acInVol"] == 121500        # cloud serves mV
    assert q["ems.bms0Online"] == 3
    assert q["ems.bms1Online"] == 3
    assert q["bmsSlave1.f32ShowSoc"] == 32.2
    assert q["ems.bms2Online"] == 0
    # normalize() must accept a BLE snapshot without blowing up
    u = dashboard.normalize(q)
    assert u["soc"] == 88.25
    assert u["solar"]["w"] == 81.0


def test_snapshot_tolerates_missing_fields():
    q = snapshot_to_quota(FakeDev(battery_level=50.0))
    assert "inv.cfgAcEnabled" not in q
    assert dashboard.normalize(q)["ac"]["out_enabled"] is None


def test_control_map_covers_the_ble_capable_controls():
    # every mapped key must exist in the registry; the four cloud-only
    # commands must be absent so set_control refuses them
    assert set(_CONTROL_MAP) <= set(controls.CONTROLS)
    assert {"pv_chg_type", "bypass_auto", "gen_auto_on", "gen_auto_off"} \
        .isdisjoint(_CONTROL_MAP)


def test_control_map_value_adapters():
    assert _CONTROL_MAP["quiet"][1](1) is False       # quiet on = buzzer off
    assert _CONTROL_MAP["quiet"][1](0) is True
    assert _CONTROL_MAP["car_in_amps"][1](6000) == 6  # cloud mA -> BLE A
    assert _CONTROL_MAP["ac_out"][1](1) is True


class FakeBle:
    def __init__(self, quota, fail=False):
        self.quota = dict(quota)
        self.fail = fail
        self.sets = []

    def get_quota_all(self, sn):
        if self.fail:
            raise BleError("out of range")
        return dict(self.quota)

    def set_control(self, sn, key, value):
        if key not in _CONTROL_MAP:
            raise BleUnsupported(f"{key} has no verified BLE command")
        self.sets.append((sn, key, value))
        self.quota[controls.CONTROLS[key]["readback"]] = value


BLE_QUOTA = {"ems.f32LcdShowSoc": 88.0, "inv.cfgAcEnabled": 0,
             "ems.bms0Online": 3, "ems.bms1Online": 0, "ems.bms2Online": 0,
             "pd.beepState": None}


@pytest.fixture
def offline_cloud(monkeypatch):
    """Cloud client whose writes bounce 1008 and whose unit reads offline."""
    monkeypatch.setattr(dashboard, "VERIFY_POLL", 0)
    monkeypatch.setattr(dashboard, "VERIFY_TIMEOUT", 0.2)

    class OfflineCloud:
        def get_quota_all(self, sn):
            return {"inv.cfgAcEnabled": "0", "inv.cfgAcXboost": "0"}

        def get_device_list(self):
            return [{"sn": sn, "online": 0} for sn in dashboard.UNITS.values()]

        def set_quota(self, sn, cmd_id, **params):
            raise EcoFlowApiError("API code 1008: request fail,please check your params")

    monkeypatch.setattr(dashboard, "client", OfflineCloud())
    return dashboard.app.test_client()


def test_offline_write_falls_through_to_ble(offline_cloud, monkeypatch):
    fake = FakeBle(BLE_QUOTA)
    monkeypatch.setattr(dashboard, "ble", fake)
    r = offline_cloud.post("/api/control",
                           json={"unit": "B", "key": "ac_out", "value": 1})
    body = r.get_json()
    assert r.status_code == 200
    assert body["via"] == "ble"
    assert body["applied"] is True
    assert fake.sets == [(dashboard.UNITS["B"], "ac_out", 1)]


def test_offline_write_ble_unsupported_reports_both_causes(offline_cloud, monkeypatch):
    monkeypatch.setattr(dashboard, "ble", FakeBle(BLE_QUOTA))
    r = offline_cloud.post("/api/control",
                           json={"unit": "B", "key": "pv_chg_type", "value": 1})
    assert r.status_code == 502
    err = r.get_json()["error"]
    assert "offline" in err and "no BLE command" in err


def test_offline_write_ble_out_of_range_reports_both_causes(offline_cloud, monkeypatch):
    fake = FakeBle(BLE_QUOTA, fail=True)
    def dead_set(sn, key, value):
        raise BleError("out of range")
    fake.set_control = dead_set
    monkeypatch.setattr(dashboard, "ble", fake)
    r = offline_cloud.post("/api/control",
                           json={"unit": "B", "key": "ac_out", "value": 1})
    assert r.status_code == 502
    err = r.get_json()["error"]
    assert "offline" in err and "BLE fallback failed" in err


def test_stale_cloud_read_swaps_in_ble_snapshot(monkeypatch):
    monkeypatch.setattr(dashboard, "LIVE_TTL", -1)

    class StaleCloud:
        def get_quota_all(self, sn):
            return {"inv.cfgAcEnabled": "0"}     # never changes

        def get_device_list(self):
            return [{"sn": sn, "online": 0} for sn in dashboard.UNITS.values()]

    monkeypatch.setattr(dashboard, "client", StaleCloud())
    monkeypatch.setattr(dashboard, "ble", FakeBle(BLE_QUOTA))
    dashboard._freshness.clear()
    http = dashboard.app.test_client()

    http.get("/api/live")                        # seed freshness tracking
    for u in dashboard._freshness.values():      # age the cloud data
        u["ts"] -= dashboard.BLE_STALE_S + 1
    d = http.get("/api/live").get_json()
    assert d["units"]["A"]["source"] == "ble"
    assert d["units"]["A"]["soc"] == 88.0
    assert d["units"]["A"]["data_age_s"] == 0


def test_fresh_cloud_read_stays_cloud(monkeypatch):
    monkeypatch.setattr(dashboard, "LIVE_TTL", -1)

    class FreshCloud:
        n = 0
        def get_quota_all(self, sn):
            FreshCloud.n += 1
            return {"inv.cfgAcEnabled": "0", "pd.wattsOutSum": FreshCloud.n}

        def get_device_list(self):
            return [{"sn": sn, "online": 1} for sn in dashboard.UNITS.values()]

    ble = FakeBle(BLE_QUOTA)
    monkeypatch.setattr(dashboard, "client", FreshCloud())
    monkeypatch.setattr(dashboard, "ble", ble)
    dashboard._freshness.clear()
    d = dashboard.app.test_client().get("/api/live").get_json()
    assert d["units"]["A"]["source"] == "cloud"
