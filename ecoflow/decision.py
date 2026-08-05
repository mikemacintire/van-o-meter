"""Pure decision logic for B->A charge balancing.

Transfer only when B's solar would otherwise be curtailed (B near full),
because the double inversion costs ~10-20%. Hysteresis prevents relay
chatter. Emergency override keeps A alive under A/C load.
"""

from dataclasses import dataclass


@dataclass
class Config:
    start_b_soc: int = 95      # begin transfer when B at/above this
    stop_b_soc: int = 80       # end transfer when B at/below this
    min_charge_w: int = 200    # Delta Pro slowChgPower range (verify live)
    max_charge_w: int = 1800
    emergency_a_soc: int = 15  # A SOC at/below this...
    ac_load_w: int = 500       # ...while A's output exceeds this -> emergency
    emergency_stop_b_soc: int = 30  # emergency never drains B below this
    # NOTE: if B's hardware minDsgSoc backstop is set to 80, emergency
    # transfers below 80% require lowering it first (id 51) — resolve with
    # Mike before enabling live control.


@dataclass
class State:
    b_soc: int
    b_solar_w: int
    a_soc: int
    a_out_w: int
    transferring: bool


@dataclass
class Decision:
    transfer: bool
    charge_watts: int | None
    reason: str


def _clamp(w, cfg):
    return max(cfg.min_charge_w, min(cfg.max_charge_w, w))


def decide(state, cfg):
    if state.a_soc <= cfg.emergency_a_soc and state.a_out_w >= cfg.ac_load_w:
        if state.b_soc <= cfg.emergency_stop_b_soc:
            return Decision(
                False, None,
                f"emergency wanted but B at emergency floor ({state.b_soc}%)",
            )
        return Decision(
            True, cfg.max_charge_w,
            f"emergency: A at {state.a_soc}% with {state.a_out_w}W load",
        )

    if state.b_soc <= cfg.stop_b_soc:
        return Decision(False, None, f"B at floor ({state.b_soc}% <= {cfg.stop_b_soc}%)")

    if state.transferring:
        return Decision(
            True, _clamp(state.b_solar_w, cfg),
            f"transferring until B hits {cfg.stop_b_soc}% floor",
        )

    if state.b_soc >= cfg.start_b_soc:
        return Decision(
            True, _clamp(state.b_solar_w, cfg),
            f"B full ({state.b_soc}%), solar would curtail",
        )

    return Decision(False, None, f"idle: B at {state.b_soc}%")
