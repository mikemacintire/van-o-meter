"""Tests for the auto-balancer: pure hysteresis on B's AC output.

Spec (docs/superpowers/specs/2026-08-03-auto-balancer-design.md): turn B's
AC output on at >= start_soc (default 99), off at <= stop_soc (default 60),
leave it alone in between. Desired action is derived from B's actual AC
state each tick, so manual toggles are respected.
"""

import json

import pytest

from ecoflow.balancer import Config, desired_action


CFG = Config(enabled=True)  # defaults: start_soc=99, stop_soc=60


# ---- hysteresis truth table ----

def test_turns_on_at_start_threshold():
    assert desired_action(99, ac_on=False, cfg=CFG) == "on"
    assert desired_action(100, ac_on=False, cfg=CFG) == "on"


def test_stays_off_below_start():
    assert desired_action(98.9, ac_on=False, cfg=CFG) is None
    assert desired_action(61, ac_on=False, cfg=CFG) is None


def test_turns_off_at_stop_threshold():
    assert desired_action(60, ac_on=True, cfg=CFG) == "off"
    assert desired_action(42, ac_on=True, cfg=CFG) == "off"


def test_stays_on_between_thresholds():
    # mid-transfer: leave it running until the floor
    assert desired_action(85, ac_on=True, cfg=CFG) is None
    assert desired_action(60.1, ac_on=True, cfg=CFG) is None


def test_no_redundant_on_when_already_on():
    assert desired_action(100, ac_on=True, cfg=CFG) is None


def test_no_redundant_off_when_already_off():
    # manual off mid-transfer -> balancer idles, no re-send at the floor
    assert desired_action(55, ac_on=False, cfg=CFG) is None


def test_manual_on_below_start_still_enforces_floor():
    # Mike flips B's AC on at 70% himself: leave it, but cut at the floor.
    assert desired_action(70, ac_on=True, cfg=CFG) is None
    assert desired_action(59, ac_on=True, cfg=CFG) == "off"


def test_unknown_soc_does_nothing():
    assert desired_action(None, ac_on=True, cfg=CFG) is None
    assert desired_action(None, ac_on=False, cfg=CFG) is None


# ---- config ----

def test_defaults_ship_disabled():
    cfg = Config()
    assert cfg.enabled is False
    assert cfg.start_soc == 99
    assert cfg.stop_soc == 60


def test_config_round_trip(tmp_path):
    path = tmp_path / "balancer.json"
    Config(enabled=True, start_soc=95, stop_soc=50).save(path)
    cfg = Config.load(path)
    assert (cfg.enabled, cfg.start_soc, cfg.stop_soc) == (True, 95, 50)


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.json")
    assert (cfg.enabled, cfg.start_soc, cfg.stop_soc) == (False, 99, 60)


def test_load_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "balancer.json"
    path.write_text("{not json", encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.enabled is False


def test_validate_rejects_stop_at_or_above_start():
    with pytest.raises(ValueError):
        Config(start_soc=60, stop_soc=60).validate()
    with pytest.raises(ValueError):
        Config(start_soc=50, stop_soc=80).validate()


def test_validate_rejects_out_of_range():
    with pytest.raises(ValueError):
        Config(start_soc=101, stop_soc=60).validate()
    with pytest.raises(ValueError):
        Config(start_soc=99, stop_soc=0).validate()


def test_validate_accepts_defaults():
    Config().validate()  # must not raise


def test_saved_file_is_plain_json(tmp_path):
    path = tmp_path / "balancer.json"
    Config(enabled=True).save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"enabled": True, "start_soc": 99, "stop_soc": 60,
                    "emergency_enabled": True, "emergency_a_soc": 15,
                    "emergency_a_clear_soc": 30, "emergency_b_floor": 15}
