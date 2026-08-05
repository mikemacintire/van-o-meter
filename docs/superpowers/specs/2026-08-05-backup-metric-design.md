# Backup metric — replace instantaneous Runtime (2026-08-05)

## Problem

The Overview's Runtime row divides stored Wh by the *present* draw. Usage swings
from ~115 W to >1 kW minute to minute, so the number answers a question nobody
is asking ("how long can I sustain this exact output?"). Mike's actual glance
question: **"if there's no solar tomorrow, how long do my batteries last at my
typical usage?"**

## Metric

Average daily battery drain over the trailing 7 days, by **energy balance** at
the system level (A + B as one battery):

```
daily_drain_wh = (solar_in + shore_in + (stored_start − stored_end)) / elapsed_days
backup_days    = usable_stored_now / daily_drain_wh
```

- **solar_in** — positive deltas of both units' `cum_solar_wh` over the window
  (reset-safe: negative delta = counter reset, dropped — same rule as
  `daily_energy`).
- **stored_start / stored_end** — first/last SOC sample in the window ×
  that unit's full capacity (passed in; capacity is constant per unit).
- **shore_in** — `max(0, ΔA.ac_in − ΔB.ac_out) + ΔB.ac_in`. A's AC-in counter
  includes the B→A transfer, which is internal to the system, so B's AC-out is
  subtracted. Off-grid this is ~0.
- Treating A+B as one system makes the B→A transfer self-accounting: B's SOC
  drop and A's SOC rise cancel except for conversion losses, which correctly
  remain in the drain. All losses (inverter overhead, double inversion) are
  captured with no assumed efficiency factor. Small bias (solar measured at
  MPPT input, before charge losses) errs conservative — backup reads slightly
  short, never long.
- **usable_stored_now** — live stored Wh above each unit's `min_dsg_soc`
  reserve (units cut off there; energy below it is not backup).
- **elapsed_days** — actual span between first and last sample in the window,
  not nominal 7, so poller gaps don't inflate the average. Counter deltas and
  SOC endpoints are both gap-proof.

## Decisions (approved 2026-08-05)

- Methodology: energy balance (Mike picked it over raw/derated port counters).
- The instantaneous runtime display is **removed entirely**, not shown
  alongside.
- Display unit is days ("X.X days"), hours below 1 day, capped at "10+ days".
- Under 48 h of window data → no number; row dims to "learning usage".
- Drain ≤ 0 (measurement noise / all-charging window) → treated as no data.

## Implementation

- `ecoflow/history.py` — new pure function
  `avg_daily_drain(rows, wh_full, now=None)` where `wh_full` maps unit → Wh
  capacity. Returns `{"wh_per_day": float, "window_days": float,
  "since": iso}` or `None` (insufficient/degenerate data). Needs both units
  present in the window; requires ≥ 48 h span.
- `dashboard.py` — `/api/history` payload gains a top-level `"drain"` field
  (computed like `coverage`, independent of the requested range). Capacities
  come from `PACK_WH` × pack count per unit (A = 2 packs, B = 1) — hardcoded
  map `DRAIN_WH_FULL`, same constants `normalize()` uses.
- `web/index.html` — the `oRun` row (Overview, POWER OUT wing) relabels
  "Runtime" → "Backup"; render block computes
  `usable_stored / drain.wh_per_day` from live limits + the drain field the
  ribbon's existing `/api/history` fetch already delivers. Only two touch
  points (row markup, render block) to minimize collision with the parallel
  UI session.

## Tests

Pytest on synthetic rows: known solar + SOC drop → expected drain; counter
reset mid-window; shore-charging window; < 48 h span → None; all-charging
window (drain ≤ 0) → None; missing unit → None.
