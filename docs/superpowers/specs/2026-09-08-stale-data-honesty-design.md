# Stale-data honesty: frozen rows, chart gaps, counter bridges

**Date:** 2026-09-08
**Status:** approved (Mike, 2026-09-08)

## Problem

EcoFlow's cloud keeps serving the last snapshot of a unit that has dropped
off WiFi, with `code 0`, for hours. The poller logs each of those snapshots as
if it were a live reading, and the History charts trust every row. The night
of 2026-09-07 Unit A froze for 44 min, 4.0 h, 4.2 h, 81 min and 74 min while
Unit B updated every 30 s. The result on the 24 h chart:

- **SOC** drew flat plateaus and then 5–7 point cliffs at each reconnect.
- **Load** drew a solid zero all night, because every fresh sample happened
  to land while the A/C compressor was off (inverter 0 W, 5 W USB light) and
  the frozen rows repeated it. The unit's own `pd.dsgPowerAc` counter, which
  lives on the device and does not care about reporting gaps, advanced
  216 Wh across the two long freezes.

The Overview already detects this (`/api/live` compares consecutive quota
payloads and shows "data frozen Nm" after 300 s). The CSV/History side has no
equivalent. That is the gap this design closes.

Nothing here changes the SOC number itself. The unit's gauge is the only
measurement of charge there is.

## Design

### 1. Poller marks frozen rows

- New CSV column `stale` (0/1), appended to `poller.FIELDS` after the pack
  columns. `poller.migrate()` handles the header change as today (rewrite by
  column name; `history.load_rows` already full-reparses on a header change).
- `extract_sample(quota, prev=None)` sets `sample["stale"] = 1` when `prev`
  is not `None` and `quota == prev` (dict equality over the full ~300-key
  payload), else `0`. `run_poller.py` keeps the previous payload per unit
  across ticks and passes it in.
- The signal is the same one `/api/live` uses. A live Delta Pro payload
  changes nearly every tick (cell voltages, remaining-time, currents), so an
  idle unit will at most mark an isolated row or two, which a 5-minute bucket
  absorbs. A genuinely frozen unit marks every row after the first.
- The device-list `online` flag is **not** logged: docs/api.md records it
  flapping and lagging while quota stays fresh. Identical payload is the
  honest signal.
- `None` quota (cloud answers `data: null`) keeps raising inside
  `extract_sample` and is logged as an ERROR with no row, as today. A partial
  payload (fewer keys) differs from the previous one and is written with
  `stale = 0` and blanks where fields are missing; blanks already mean
  "unknown", never zero, throughout `history.py`.

### 2. History derives the flag for rows logged before the column existed

- `history._parse` reads `stale` as an int when present.
- `load_rows` keeps the last parsed row per unit in the cache. When a row's
  `stale` is blank (pre-migration rows), it is set to `1` if every logged
  field (all of `poller.FIELDS` except `stale`) equals the previous row of
  the same unit, else `0`. The comparison is over the 19 logged columns
  rather than the full payload, so it is less sensitive than the poller's
  flag, but for the multi-hour freezes it is exact: those rows are
  byte-identical. Last night's chart is corrected by this rule without a
  backfill script.
- The per-unit "last row" survives incremental tail parses (it lives in
  `_cache`) and is reset with the rest of the cache on a full reparse.
- **Refinement during implementation:** whichever signal says "identical"
  (the poller flag or the 19-column comparison), a row is stale only once
  the reading has sat unchanged for `STALE_AFTER_S` = 300 s, the same
  threshold as the Overview's "data frozen" badge. Without it, a live idle
  unit's 19 columns can repeat for a few minutes (SOC ticks 0.1 % every
  ~6 min at 36 W; LFP pack mV sits flat), and the derived flag striped
  Unit A's night with false five-minute gaps. `load_rows` tracks
  `frozen_since` per row for this.
- **Known limit found on the real data:** the cloud caches per module. After
  the 2026-09-07 freeze the first full read carried fresh BMS values but a
  `pd.*` block still frozen at the pre-outage counters, so the bridge for
  the second half of the night reports the whole outage's 216 Wh across
  4.2 h and the first half stays an unbridged gap. Energy total right,
  timing late. Documented in docs/api.md; not worth a per-module flag yet.

### 3. Aggregations ignore stale rows

- `bucket_series`: means and the bucket's SOC come from fresh rows only. A
  bucket whose rows are all stale is still emitted (its `t` stays on the
  axis) with `None` for every metric, so the chart sees a gap rather than a
  missing bucket, which would otherwise draw a straight line across the
  outage.
- `summarize`: duty cycle, averages, peaks and SOC min/max use fresh rows.
  `samples` still counts every row (it describes the log).
- `hourly_profile`: fresh rows only.
- `avg_daily_drain`: the SOC endpoints come from fresh rows. Counter deltas
  are unaffected (a frozen counter differences to zero).
- `daily_energy`: unchanged. Counters are device-side integrals and a stale
  row cannot corrupt a delta.

### 4. Counter bridges across gaps

`bucket_series` gains a second pass. For each run of consecutive all-`None`
buckets it finds the last fresh row before the run and the first fresh row
after it (searching the unit's full row list, so a fresh row just outside
the requested range still bounds a gap at the edge), and fills the run's
power metrics with the counter rate over that exact span:

| bucket metric | bridged from | value |
|---|---|---|
| `solar_w` | `cum_solar_wh` | ΔWh ÷ Δhours |
| `ac_out_w` | `cum_ac_out_wh` | ΔWh ÷ Δhours |
| `dc_out_w` | `cum_dc_out_wh` | ΔWh ÷ Δhours |
| `ac_charge_w` | `cum_ac_in_wh` | ΔWh ÷ Δhours |
| `watts_out` | `cum_ac_out_wh + cum_dc_out_wh` | ΔWh ÷ Δhours |
| `soc` | nothing | stays `None` |

A parallel list `bridged` (one bool per bucket) is added to each unit's
series. A gap with no fresh row after it (the unit is frozen right now) or
a counter that went backwards (reset) is left `None` and not bridged.

Rationale for the hybrid: making the whole load line counter-derived would
be more uniform, but the counters are integer Wh, and at 5-minute buckets a
40 W solar trickle is 3 Wh per bucket, quantizing to ±30 %. Sampled means
stay smooth where sampling worked; counters fill only where it did not, and
the styling says which is which.

### 5. Charts draw gaps and bridges

`web/index.html`:

- `powerRows` stops coercing `null` to `0`. Each row carries `gapA`, `gapB`
  (metric was `null` for that unit) and `bridgedA`, `bridgedB`.
- `drawPower`: solar areas and the load and transfer lines are split into
  runs at gap rows (nothing drawn there). Runs made of bridged buckets are
  drawn **dashed** (`stroke-dasharray`), solid otherwise. The stacked solar
  fill continues through bridged buckets (they carry real energy) but breaks
  at gaps. The tooltip appends "avg over outage" to a bridged value and shows
  "no data" for a gap.
- `drawSoc`: gaps become breaks in the line. Between the last point before
  a gap and the first point after it, a **dotted** straight segment is
  drawn (`stroke-dasharray="2 4"`), because a straight interpolation across
  an outage is far closer to the truth than a plateau and a cliff. The soft
  fill under the line stays one continuous volume across gaps (breaking it
  striped the chart on a flaky night). The Overview ribbon calls the same
  function and inherits this.
- `drawProfile` and `drawDaily` need no change (they consume
  `hourly_profile` and `daily_energy`).
- A one-line legend note under the power chart: "dashed = average from the
  unit's energy counters while its cloud link was frozen".

### 6. What is not in scope

- No "packs disagree" badge. A spread between A's two packs is real state
  (the Extra Battery was unplugged for 14 h on 2026-09-07 and came back 10
  points ahead), and the Units view already shows both packs.
- No change to the SOC figure, the backup metric, the forecast or the
  balancer. The balancer reads live quota, not the CSV.
- No logging of the device-list `online` flag.

## Testing

pytest (`.venv\Scripts\python.exe -m pytest`):

- `test_poller`: `extract_sample` marks `stale = 1` only for an identical
  previous payload; `FIELDS`/`HEADER` end with `stale`; `migrate` appends
  the column to an older file.
- `test_history`: `_parse` reads `stale`; `load_rows` derives it for blank
  values from the previous same-unit row, including across an incremental
  tail read; `bucket_series` excludes stale rows, emits `None` buckets,
  bridges a gap from counters with the right rate, leaves an open-ended gap
  unbridged, skips a counter reset, and reports `bridged`; `summarize`,
  `hourly_profile` and `avg_daily_drain` ignore stale rows.
- `test_dashboard_history`: the payload carries `bridged` per unit.

node (`node --test tests/`): `power-presentation.test.mjs` already
syntax-checks the whole inline script; add a test that `powerRows` yields
`gap`/`bridged` flags and no longer turns `null` into `0`.

Manual: reload the History view and confirm last night (2026-09-07 19:37
to 2026-09-08 03:51 EDT) shows a dashed ~31 W load bridge and a dotted SOC
bridge instead of a zero line and cliffs.
