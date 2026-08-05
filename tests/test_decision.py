"""Tests for the charge-balancing decision logic.

Rules (see project memory): transfer B->A only when B's solar is curtailed
(B near full), with hysteresis — start at >=95% B SOC, stop at <=80%.
Emergency override: A critically low while its A/C load runs.
"""

from ecoflow.decision import Config, State, decide

CFG = Config()  # defaults: start=95, stop=80, min_w=200, max_w=1800,
                # emergency_a_soc=15, ac_load_w=500, emergency_stop_b_soc=30


def make_state(**kw):
    defaults = dict(b_soc=50, b_solar_w=0, a_soc=60, a_out_w=0, transferring=False)
    defaults.update(kw)
    return State(**defaults)


def test_idle_when_b_not_full():
    d = decide(make_state(b_soc=90, b_solar_w=800), CFG)
    assert d.transfer is False


def test_starts_transfer_when_b_full():
    d = decide(make_state(b_soc=96, b_solar_w=800), CFG)
    assert d.transfer is True
    assert d.charge_watts == 800
    assert "curtail" in d.reason


def test_hysteresis_keeps_transferring_between_thresholds():
    d = decide(make_state(b_soc=85, b_solar_w=600, transferring=True), CFG)
    assert d.transfer is True


def test_stops_at_floor():
    d = decide(make_state(b_soc=80, b_solar_w=600, transferring=True), CFG)
    assert d.transfer is False
    assert "floor" in d.reason


def test_charge_watts_clamped_to_range():
    low = decide(make_state(b_soc=96, b_solar_w=50), CFG)
    high = decide(make_state(b_soc=96, b_solar_w=2500), CFG)
    assert low.charge_watts == 200
    assert high.charge_watts == 1800


def test_emergency_transfer_when_a_critical_under_load():
    d = decide(make_state(b_soc=60, a_soc=10, a_out_w=900), CFG)
    assert d.transfer is True
    assert d.charge_watts == 1800
    assert "emergency" in d.reason


def test_no_emergency_when_ac_idle():
    d = decide(make_state(b_soc=60, a_soc=10, a_out_w=100), CFG)
    assert d.transfer is False


def test_emergency_respects_emergency_floor():
    # Emergency may dip below the normal 80% stop, but never below the
    # emergency floor — B must not be drained critically either.
    d = decide(make_state(b_soc=29, a_soc=10, a_out_w=900, transferring=True), CFG)
    assert d.transfer is False
