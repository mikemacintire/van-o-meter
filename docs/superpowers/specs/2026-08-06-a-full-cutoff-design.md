# A-full cutoff: stop the B→A transfer when A is full

**Date:** 2026-08-06
**Status:** implemented
**Touches:** `ecoflow/balancer.py`, `dashboard.py`, `web/index.html`

## Problem

Nothing in the balancer looks at how full A is before deciding to push more into it.
The normal layer keys entirely off B's SOC (start at `start_soc`, stop at `stop_soc`),
and the emergency layer only reacts to A being *empty*. So a transfer that begins when
B fills up keeps running after A is topped off.

That case is not merely wasteful, it is backwards:

- A full A cannot absorb the incoming power, so B's output bypasses to the loads —
  after a 10–20% double-inversion loss.
- Meanwhile A's own 1200 W of solar has nowhere to go and is **curtailed**.

So the rig burns stored charge out of B, at a loss, to run loads that A's free solar
would otherwise have covered. Cutting the transfer restores the better arrangement:
A runs its loads from its own panels and bank, and B keeps its charge.

The live config makes this sharper than the defaults suggest. As of 2026-08-06 the
running balancer is `start_soc=80`, `stop_soc=20` — a transfer starts with B only
partly full and, absent this rule, runs A up to 100% and then keeps going down to
B=20%.

A second, narrower bug is closed by the same rule. In emergency-only mode
(`enabled=False`, `emergency_enabled=True`), once a rescue fires nothing ever turns B
off again: `emergency_action()` returns `hold` below the stand-down level and `None`
above it, and with the normal layer disabled the tick's `action` stays `None`. B drains
to its 15% floor no matter how full A gets. The A-full cutoff terminates that rescue at
the right moment.

## Design

A third stateless layer in `ecoflow/balancer.py`, built to the same rules as
`emergency_action()`: a pure function of the live readback, no latch, no internal
"transferring" flag to drift out of sync with the hardware or die with the process.

```python
def a_full_action(a_soc, ac_on, cfg):
    """'off' (A is full — cut the transfer), 'block' (A too full to start one),
    or None (A has room — defer to the other layers)."""
```

| state | condition | result |
|---|---|---|
| transferring | `a_soc >= a_full_soc` | `off` |
| idle | `a_soc >= a_full_resume_soc` | `block` |
| either | otherwise, or `a_soc is None` | `None` |

### Two thresholds, not one

A bare cutoff oscillates. Cut at A=99%, A's loads pull it to 98.9% within minutes, B is
still above `start_soc`, so the next tick turns the transfer back on and A returns to
99%. That is a relay cycling every few minutes, indefinitely.

The `block` result is what makes the rule hold: the resume threshold gates *starting* a
transfer, the cutoff threshold gates *stopping* one, and A must fall through the gap
between them before another transfer may begin. This is the same shape as the emergency
layer's `EMERGENCY_REARM` margin, applied at the top of the range instead of the bottom.

Defaults: `a_full_soc = 99`, `a_full_resume_soc = 90`. The 9-point band is roughly
650 Wh of A's 7200 Wh bank, so transfers become infrequent and long rather than short
and repeated.

### Precedence

The A-full layer is consulted **first**, ahead of the emergency layer:

1. `a_full_action` → `off` cuts, `block` suppresses any start
2. `emergency_action` → `on` / `off` / `hold`
3. `desired_action` → the normal B-side hysteresis

In practice the first two cannot collide — the emergency layer triggers at A ≤ 15% and
this one at A ≥ 99%. The ordering only settles what happens under a nonsensical config,
and there the safe answer is clear: cutting power into a full battery is never the
harmful choice.

### Always in force

Unlike the other two layers this one has no enable toggle. It only ever turns the
transfer **off** or declines to start one, so it cannot surprise anyone, and it applies
whenever the tick is actuating at all — including emergency-only mode, which is where
it closes the drain-to-floor bug above.

Consequently A's SOC must be read every tick. Today it is fetched only when
`emergency_enabled` is set; that read moves out of the conditional.

### Failure posture

If A's quota read fails, `a_soc` is `None` and the layer goes inert rather than cutting.
The units flap on and off EcoFlow's cloud routinely (see `docs/api.md`), and a flap must
not kill a healthy transfer. This matches how the emergency layer already degrades, and
the existing `a_error` status field already surfaces it.

## Interface changes

**`balancer.Config`** gains `a_full_soc: int = 99` and `a_full_resume_soc: int = 90`,
loaded with defaults so a pre-existing `balancer.json` still parses. `validate()`
enforces `0 < a_full_resume_soc < a_full_soc <= 100`.

**Status** gains `a_full: true` while the layer is suppressing a start, and a
`state` of `"a-full"` so the Control view can explain an idle transfer with B full.
Actions carry a `reason` string into `logs/balancer.jsonl` as usual.

**UI:** two sliders in the Control view's Auto balance card, plus the `a-full` state
styling on the status pill.

## Testing

Pure layer (`tests/test_balancer.py`): the truth table above, the hysteresis band,
`None` SOC, config round-trip and validation.

Wiring (`tests/test_dashboard_balancer.py`): a full A cuts a running transfer; a full A
blocks a start that the normal rule would otherwise make; a mid-band A neither cuts nor
blocks; the cutoff outranks an emergency `hold`; it fires with `enabled=False` and
`emergency_enabled=True`; a failed A read leaves the transfer alone.
