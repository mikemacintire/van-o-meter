"""Tests for the control registry — validation, param building, readback."""

import json

import pytest

from ecoflow import controls


def test_registry_integrity():
    assert len(controls.CONTROLS) == 16
    group_names = {g for g, _ in controls.GROUPS}
    for key, c in controls.CONTROLS.items():
        assert c["kind"] in ("toggle", "slider", "choice"), key
        assert c["group"] in group_names, key
        assert "." in c["readback"], key
        assert isinstance(c["cmd_id"], int), key
        if c["kind"] == "slider":
            assert c["min"] < c["max"], key
        if c["kind"] == "choice":
            assert len(c["choices"]) >= 2, key


def test_validate_toggle():
    assert controls.validate("ac_out", 1) == 1
    assert controls.validate("ac_out", "0") == 0
    with pytest.raises(ValueError):
        controls.validate("ac_out", 2)


def test_validate_slider_range():
    assert controls.validate("max_chg_soc", 85) == 85
    with pytest.raises(ValueError):
        controls.validate("max_chg_soc", 49)   # ceiling floor is 50
    with pytest.raises(ValueError):
        controls.validate("min_dsg_soc", 31)   # floor cap is 30
    with pytest.raises(ValueError):
        controls.validate("ac_chg_watts", 2000)


def test_validate_choice():
    assert controls.validate("car_in_amps", 6000) == 6000
    with pytest.raises(ValueError):
        controls.validate("car_in_amps", 5000)


def test_validate_unknown_key_and_garbage():
    with pytest.raises(KeyError):
        controls.validate("warp_drive", 1)
    with pytest.raises(ValueError):
        controls.validate("ac_out", "on")


def test_build_params_66_always_sends_both():
    # AC output carries current xboost along; xboost carries current enabled.
    quota = {"inv.cfgAcXboost": "1", "inv.cfgAcEnabled": 0}
    assert controls.build_params("ac_out", 0, quota) == {"enabled": 0, "xboost": 1}
    assert controls.build_params("xboost", 1, quota) == {"xboost": 1, "enabled": 0}


def test_build_params_companion_default_when_missing():
    assert controls.build_params("ac_out", 1, {}) == {"enabled": 1, "xboost": 0}
    assert controls.build_params("ac_out", 1, None) == {"enabled": 1, "xboost": 0}


def test_build_params_plain():
    assert controls.build_params("max_chg_soc", 90) == {"maxChgSoc": 90}


def test_matches_coerces_strings():
    # quota/all is documented returning numbers as strings.
    assert controls.matches("ac_out", 1, {"inv.cfgAcEnabled": "1"})
    assert controls.matches("max_chg_soc", 90, {"ems.maxChargeSoc": 90.0})
    assert not controls.matches("ac_out", 1, {"inv.cfgAcEnabled": "0"})
    assert not controls.matches("ac_out", 1, {})


def test_current_values_maps_readbacks():
    quota = {"inv.cfgAcEnabled": 1, "pd.beepState": "0"}
    vals = controls.current_values(quota)
    assert vals["ac_out"] == 1
    assert vals["quiet"] == "0"
    assert vals["max_chg_soc"] is None


def test_public_registry_is_json_safe_and_ordered():
    reg = controls.public_registry()
    assert len(reg) == 16
    json.dumps(reg)  # would raise on tuples/sets
    assert reg[0]["key"] == "ac_out"
    by_key = {e["key"]: e for e in reg}
    assert by_key["ac_out"]["risky"] is True
    assert by_key["quiet"]["risky"] is False
    assert by_key["lcd_timeout"]["choices"][0] == [10, "10s"]
    assert "readback" not in by_key["ac_out"]  # internal fields stay internal
