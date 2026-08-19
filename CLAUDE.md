# EcoFlow charge balancing

Auto-balance charge between Mike's two Delta Pro units via the official EcoFlow Developer API (cloud REST + MQTT).

- Unit A: Delta Pro + Extra Battery, 1200W solar, powers the A/C (biggest load)
- Unit B: standalone Delta Pro, 1200W solar; its AC output is wired into A's AC input
- Core idea: transfer B to A only when B's solar would otherwise be curtailed (B ~full), because the double inversion costs ~10-20%

Key references:
- Project memory: `C:\Users\mikem\.claude\projects\C--Users-mikem--pod\memory\project_ecoflow_balancing.md` (confirmed API command IDs + quota fields)
- Official Delta Pro API doc: https://developer.ecoflow.com/us/document/PP?id=2058836526572933122 (JS-rendered; use a browser tool, not plain fetch)

Credentials: EcoFlow accessKey/secretKey live in `.env` here (not committed anywhere). Keys arrived and verified working 2026-07-25; both units online (SN_A=DCABZ5SH2222480 "UNIT A - A/C", SN_B=DCABF59HB301010 "UNIT B").

## Code layout (scaffolded 2026-07-20, tests pass, awaiting live creds)

- `ecoflow/signing.py` — request signing (flatten → sort → HMAC-SHA256)
- `ecoflow/client.py` — REST client (device list, quota/all, set quota cmdSet 32)
- `ecoflow/decision.py` — pure balancing logic with hysteresis + emergency mode (no I/O)
- `ecoflow/poller.py` — quota field extraction, scaling, CSV append + schema migration
- `ecoflow/history.py` — CSV load (incremental tail read) + bucketing/energy/stats aggregation
- `run_poller.py` — read-only poller entry point; logs both units to `data/samples.csv`
- `ecoflow/controls.py` — registry of all 16 settable controls (15 cmd ids) with validation; canonical command table
- `dashboard.py` + `web/index.html` — local console on :8642, four views since the 2026-08-05 near-view consolidation: **Overview / Units / Control / History**. Power+Energy+System merged into History (one range bar, charts, stat tiles, lifetime counters, provenance footer); System's per-unit diagnostics and Control's extra-battery card collapsed into the Units view's per-pack table; ring gauges replaced by the marquee's segmented bar at card size; topbar status pills removed. Old `#power`/`#energy`/`#system` hashes alias to the new views. Control view sends commands via `POST /api/control` with readback verification
- `PRODUCT.md` + `DESIGN.md` — the design system the console implements (2026-08-03 overhaul; Overview rebuilt 2026-08-04, revised 2026-08-05 to "power-path": marquee / stage reading INPUT panel → truck → OUTPUT panel (shore on input; unit colors retired on Home; IBM Plex app-wide, Oxanium retired) / 24 h ribbon — brief in `.impeccable/surfaces/web-index-html.md`). **Two viewing scenes:** Overview is the *far view* (always-on tablet read at 6–10 ft, vh-clamped type, 14px floor, must fit one screen with no scroll above 900×620); every other view is the *near view* (arm's length, 12px floor, 44px touch targets). Fonts are self-hosted in `web/assets/` because the truck is off-grid. Audit + plan: `docs/superpowers/specs/2026-08-03-ui-audit-and-overhaul.md`
- `docs/api.md` — confirmed API details, no-history finding, dual-charging caveat, MQTT notes
- `ecoflow/ble/` (vendored eflib, Apache-2.0) + `ecoflow/ble_client.py` — BLE fallback, BUILT but radio-untested; enable with `ECOFLOW_BLE=1` + `ECOFLOW_USER_ID` once an adapter is in range. See `docs/ble-plan.md` for the first-run checklist.
- `tests/` — pytest suite; run with `.venv\Scripts\python.exe -m pytest`

## Data collection (started 2026-07-25 08:44 EDT)

EcoFlow has **no historical endpoint** (see `docs/api.md`) — history exists only as long as the poller has been running, so poller uptime is the dataset. Two consequences:

- Energy totals come from differencing the five `pd.*` cumulative Wh counters, never from integrating watts, so gaps don't corrupt them.
- **The poller stops when the machine sleeps.** A 118-minute hole on day one (10:20–12:19) was Windows sleeping, not a bug. For continuous history it needs to run somewhere always-on.

CSV schema changes are handled by `poller.migrate()`, which remaps by column *name* on startup — safe to add or reorder columns without losing logged rows. Migration rewrites every row, so the file *grows* and all byte offsets shift; `history.load_rows()` therefore compares the on-disk header against its cached one and fully reparses when it changes. Without that check the incremental tail read seeks to a stale offset and re-appends rows it already has (caught 2026-08-15 — the size-only check missed it because migration never shrinks the file). The row cache is also guarded by a module lock (added 2026-08-19): `/api/history` and `/api/forecast` call `load_rows` under separate endpoint locks, and two threads full-reparsing a cold cache concurrently interleaved the file into memory twice — out-of-order counters read as ~84 kWh/day of phantom solar until the next restart.

**Per-pack columns (added 2026-08-15):** `pack_main_soc` / `pack_extra_soc` / `pack_main_mv` / `pack_extra_mv`. A's two packs run independent coulomb-counted SOC gauges that drift apart (4.8 points on 2026-07-27, 15.2 on 2026-08-15) while the packs themselves sit on one bus at the same voltage — 3 mV apart across 15 cells. Logging both gauges *and* both pack voltages is what separates "the gauges disagree" from "the charge really diverged"; `soc` alone is their capacity-weighted mean and hides it. Voltage stays in raw mV because the honest gap is ~12 mV, which volts-to-1dp rounds away. Gated on `ems.bms{slot}Online`, never on the pack fields — `bmsSlave1.*` ships on B too with plausible-looking fakes. Full finding, including why a full charge is the only fix, in `docs/api.md`.

Copy `.env.example` to `.env` when keys arrive; run `run_poller.py` without SNs set to print the device list. Manual control ships in the dashboard's Control view (2026-08-03). Gotcha: writes to an offline unit fail with the misleading `1008 check your params` while quota reads still serve stale data; see docs/api.md.

## Auto-balancer (shipped 2026-08-03, ships disabled)

`ecoflow/balancer.py` (pure hysteresis + JSON config) + a 60 s background thread in `dashboard.py` (reloader-child only): turns B's AC output on at ≥ start SOC (default 99%), off at ≤ stop SOC (default 60%) — B's outlets feed A's input, so this IS the transfer. A's draw rate stays manual via A's "AC Charge Speed". Desired action derives from B's live `inv.cfgAcEnabled` each tick (no internal state), so manual toggles are respected. Config in `data/balancer.json`, UI card at top of the Control view, API at `GET/POST /api/balancer`. Spec: `docs/superpowers/specs/2026-08-03-auto-balancer-design.md`.

**Single-instance + action log (added 2026-08-05):** three concurrently-running dashboards (dev-session launches on top of the scheduled-task keepalive) let a stale in-memory config kill a transfer at 57%. Now `ecoflow/single_instance.py` holds an exclusive localhost port as a process lock — dashboard watcher on 8643, poller on 8644 — so a second launch exits with a refusal message. Every command the balancer sends is appended to `logs/balancer.jsonl`. Note when process-hunting: venv `python.exe` is a launcher stub, so each real interpreter appears as a parent+child pair in the process list.

**A-full cutoff (added 2026-08-06, always in force):** `balancer.a_full_action()` — a third stateless layer consulted *before* the emergency one. A at/above `a_full_soc` (99%) cuts a running transfer; a new one is blocked until A falls below `a_full_resume_soc` (90%). The two thresholds are the point: a bare cutoff chatters, because A's loads pull it back under 99% within minutes while B is still above `start_soc`. Rationale — a full A can't absorb the transfer, so B's output bypasses to the loads at a double-inversion loss *while A's own 1200 W of solar is curtailed*; cutting it lets A run itself. No enable toggle (it only ever stops a transfer), so it also applies in emergency-only mode, where it closes a real gap: previously a fired rescue ran B down to its 15% floor no matter how full A got. A's SOC is therefore read every tick, not just when emergency is armed; a failed A read leaves the layer inert rather than cutting, since the units flap on and off the cloud. Status carries `a_full` + a `state` of `"a-full"`. Spec: `docs/superpowers/specs/2026-08-06-a-full-cutoff-design.md`.

**Emergency fallback (added 2026-08-04, ships enabled):** `balancer.emergency_action()` — a stateless layer the same tick consults first. A ≤ 15% forces B's AC on regardless of B's fill; the normal stop-at-60 rule is suppressed until A recovers to 30%; B is never drained below 15% (+3% re-arm margin against relay chatter). Independent `emergency_enabled` toggle; works even with the normal balancer off. decision.py's emergency mode (load-wattage condition) remains unwired — this is SOC-only, per Mike's spec.

## Solar forecast (shipped 2026-08-07, display-only)

Predicts tomorrow's harvest by matching the weather forecast against what these panels have actually produced under past weather. `ecoflow/weather.py` (Open-Meteo daily, no API key) + `ecoflow/forecast.py` (pure model), served at `GET /api/forecast`. Nothing reads the prediction — not the balancer, not the poller.

**The obvious feature is the wrong one.** Measured over the first 10 logged days, `shortwave_radiation_sum` (total irradiance) predicts *worse than a flat average*; `sunshine_duration` beats it by half (r=+0.94, LOO MAE 134 Wh vs 344 Wh baseline). That matches the shade situation — under a tree, diffuse sky brightness is eaten by the canopy and only direct sun getting through matters. Live fit: ~711 Wh + ~157 Wh per sunshine hour. So the model **selects** a feature each refit by leave-one-out CV and uses it only if it beats the rolling average by a margin, otherwise the average *is* the prediction. Falling back is a real outcome, not a safety valve.

Two day types never train: **partial days** (poller gaps — counter deltas don't describe a whole day) and **curtailed days** (a pack hit 99%+, so the MPPT throttled and the day under-reports). Dropping one curtailed day moved r from +0.65 to +0.94.

One Open-Meteo call serves both halves (`past_days` for training, `forecast_days` for prediction) — deliberately the same model for both, since ERA5 archive fits better but doesn't exist for tomorrow, and mixing them is train/serve skew. Response caches to `data/weather.json` so an off-grid dashboard still shows yesterday's forecast, honestly dated. Config in `data/forecast.json` (Butler Beach 29.7983/-81.26701, `window_days` 14) — not auto-detected, because the uplink geolocates to the carrier's POP. UI: a `Tomorrow` row under `Today` in the Overview INPUT panel, and a card in History with the day strip, winning feature, and ± accuracy. Spec: `docs/superpowers/specs/2026-08-07-solar-forecast-design.md`.

## Backup metric (shipped 2026-08-05)

The Overview's "Runtime" row (stored ÷ instantaneous draw — useless while usage swings) is now **"Backup"**: usable stored Wh ÷ 7-day average daily drain, answering "if there's no solar tomorrow, how long do I have at typical usage?". The drain comes from `history.avg_daily_drain()` by **energy balance** — solar + shore + drop in stored charge over the actual sampled span — so inverter overhead and B→A double-inversion losses are included with no assumed efficiency factor (A+B treated as one system makes the transfer self-accounting; A's ac-in counter includes the transfer, so B's ac-out is subtracted from the shore estimate). Served as the range-independent `drain` field of `/api/history`; "usable" stops at each unit's `min_dsg_soc` reserve. Under 48 h of window data → row dims to "learning usage". Spec: `docs/superpowers/specs/2026-08-05-backup-metric-design.md`.
