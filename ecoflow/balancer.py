"""Auto-balancer: hysteresis toggle of B's AC output for B->A transfer.

Pure logic + JSON config, no cloud I/O — same discipline as decision.py.
The dashboard's balancer thread derives the desired action each tick from
B's *actual* AC-output readback, so there is no internal transferring flag
to drift out of sync and manual toggles are respected.

Spec: docs/superpowers/specs/2026-08-03-auto-balancer-design.md
"""

import json
from dataclasses import dataclass, asdict


EMERGENCY_REARM = 3   # % above the B floor before an emergency may re-trigger


@dataclass
class Config:
    enabled: bool = False   # ships off: the dashboard is live with real creds
    start_soc: int = 99     # turn B's AC output on at/above this
    stop_soc: int = 60      # turn it off at/below this
    # Emergency fallback: A running dry overrides everything above.
    emergency_enabled: bool = True
    emergency_a_soc: int = 15        # A at/below this -> force B's AC on
    emergency_a_clear_soc: int = 30  # A back at/above this -> normal rules resume
    emergency_b_floor: int = 15      # never drain B below this

    def validate(self):
        if not 0 < self.stop_soc < self.start_soc <= 100:
            raise ValueError(
                f"need 0 < stop ({self.stop_soc}) < start ({self.start_soc}) <= 100")
        if not 0 < self.emergency_a_soc < self.emergency_a_clear_soc <= 100:
            raise ValueError(
                f"need 0 < trigger ({self.emergency_a_soc}) < "
                f"clear ({self.emergency_a_clear_soc}) <= 100")
        if not 0 < self.emergency_b_floor < 100:
            raise ValueError(
                f"B floor ({self.emergency_b_floor}) must be 1-99")

    def save(self, path):
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path):
        d = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                enabled=bool(data["enabled"]),
                start_soc=int(data["start_soc"]),
                stop_soc=int(data["stop_soc"]),
                # newer fields default so a pre-emergency file still loads
                emergency_enabled=bool(
                    data.get("emergency_enabled", d.emergency_enabled)),
                emergency_a_soc=int(
                    data.get("emergency_a_soc", d.emergency_a_soc)),
                emergency_a_clear_soc=int(
                    data.get("emergency_a_clear_soc", d.emergency_a_clear_soc)),
                emergency_b_floor=int(
                    data.get("emergency_b_floor", d.emergency_b_floor)))
        except (OSError, ValueError, KeyError, TypeError):
            return cls()


def desired_action(soc, ac_on, cfg):
    """'on', 'off', or None (leave alone) for B's AC output."""
    if soc is None:
        return None
    if not ac_on and soc >= cfg.start_soc:
        return "on"
    if ac_on and soc <= cfg.stop_soc:
        return "off"
    return None


def emergency_action(a_soc, b_soc, ac_on, cfg):
    """Emergency layer, checked before the normal rules each tick.

    Returns 'on' (A dry: force the transfer), 'off' (B at its floor),
    'hold' (mid-emergency: suppress the normal stop_soc cutoff), or
    None (no emergency in play — defer to the normal rules).
    """
    if a_soc is None or b_soc is None:
        return None
    if ac_on:
        if b_soc <= cfg.emergency_b_floor:
            return "off"
        if a_soc < cfg.emergency_a_clear_soc:
            return "hold"
        return None
    if (a_soc <= cfg.emergency_a_soc
            and b_soc >= cfg.emergency_b_floor + EMERGENCY_REARM):
        return "on"
    return None
