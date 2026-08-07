"""Tests for the auto-balancer: pure hysteresis on B's AC output.

Spec (docs/superpowers/specs/2026-08-03-auto-balancer-design.md): turn B's
AC output on at >= start_soc (default 99), off at <= stop_soc (default 60),
leave it alone in between. Desired action is derived from B's actual AC
state each tick, so manual toggles are respected.
"""

import json

import pytest

from ecoflow.balancer import Config, a_full_action, desired_action


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


# ---- A-full cutoff (2026-08-06-a-full-cutoff-design.md) ----
#
# A full can't absorb the transfer: B's power bypasses to the loads at a
# double-inversion loss while A's own 1200 W of solar is curtailed. Cut it.

def test_a_full_cuts_running_transfer():
    assert a_full_action(99, ac_on=True, cfg=CFG) == "off"
    assert a_full_action(100, ac_on=True, cfg=CFG) == "off"


def test_a_below_cutoff_leaves_transfer_running():
    # mid-transfer with room left in A: not this layer's business
    assert a_full_action(98.9, ac_on=True, cfg=CFG) is None
    assert a_full_action(50, ac_on=True, cfg=CFG) is None


def test_a_full_blocks_a_new_transfer():
    # the half of the rule that stops relay chatter: without it the next
    # tick turns the transfer straight back on and A returns to 99%
    assert a_full_action(99, ac_on=False, cfg=CFG) == "block"


def test_block_persists_through_the_hysteresis_band():
    # cut at 99, and A must fall past the resume level before another start
    assert a_full_action(98, ac_on=False, cfg=CFG) == "block"
    assert a_full_action(90, ac_on=False, cfg=CFG) == "block"


def test_start_allowed_once_a_falls_below_resume():
    assert a_full_action(89.9, ac_on=False, cfg=CFG) is None
    assert a_full_action(40, ac_on=False, cfg=CFG) is None


def test_in_band_transfer_runs_on_to_the_cutoff():
    # 95 blocks a *start* but must not cut a transfer already under way
    assert a_full_action(95, ac_on=True, cfg=CFG) is None


def test_unknown_a_soc_is_inert():
    # cloud flap must not kill a healthy transfer (docs/api.md)
    assert a_full_action(None, ac_on=True, cfg=CFG) is None
    assert a_full_action(None, ac_on=False, cfg=CFG) is None


def test_a_full_thresholds_are_configurable():
    cfg = Config(a_full_soc=95, a_full_resume_soc=80)
    assert a_full_action(95, ac_on=True, cfg=cfg) == "off"
    assert a_full_action(94, ac_on=True, cfg=cfg) is None
    assert a_full_action(80, ac_on=False, cfg=cfg) == "block"
    assert a_full_action(79, ac_on=False, cfg=cfg) is None


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


def test_a_full_defaults():
    cfg = Config()
    assert cfg.a_full_soc == 99
    assert cfg.a_full_resume_soc == 90


def test_validate_rejects_resume_at_or_above_cutoff():
    with pytest.raises(ValueError):
        Config(a_full_soc=99, a_full_resume_soc=99).validate()
    with pytest.raises(ValueError):
        Config(a_full_soc=90, a_full_resume_soc=95).validate()


def test_validate_rejects_a_full_out_of_range():
    with pytest.raises(ValueError):
        Config(a_full_soc=101, a_full_resume_soc=90).validate()
    with pytest.raises(ValueError):
        Config(a_full_soc=99, a_full_resume_soc=0).validate()


def test_load_pre_a_full_file_gets_defaults(tmp_path):
    # a balancer.json written before this feature must still load
    path = tmp_path / "balancer.json"
    path.write_text(json.dumps({"enabled": True, "start_soc": 80,
                                "stop_soc": 20}), encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.start_soc, cfg.stop_soc) == (80, 20)
    assert (cfg.a_full_soc, cfg.a_full_resume_soc) == (99, 90)


def test_saved_file_is_plain_json(tmp_path):
    path = tmp_path / "balancer.json"
    Config(enabled=True).save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"enabled": True, "start_soc": 99, "stop_soc": 60,
                    "emergency_enabled": True, "emergency_a_soc": 15,
                    "emergency_a_clear_soc": 30, "emergency_b_floor": 15,
                    "a_full_soc": 99, "a_full_resume_soc": 90}
