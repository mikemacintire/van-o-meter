# Auto-balancer: B→A transfer via B's AC output — design

Approved by Mike 2026-08-03 (chat). Plain hysteresis only; emergency mode
explicitly deferred.

## Behavior

- When Unit B's SOC reaches **≥ start** (default **99%**), turn **on** B's AC
  output. B's outlets feed A's AC input (hardwired), so A immediately draws at
  whatever A's "AC Charge Speed" slider is set to (200–1800 W — existing manual
  control on the A card; not automated here).
- When B's SOC falls to **≤ stop** (default **60%**), turn B's AC output
  **off**. Wait for ≥ start again. Repeat.
- Between the thresholds the balancer leaves B's AC output alone, whichever
  state it is in. Hysteresis emerges from that gap; there is no internal
  "transferring" flag to get out of sync — desired action is derived each tick
  from B's *actual* `inv.cfgAcEnabled` readback plus SOC. Manual toggles are
  therefore respected: turn it off mid-transfer and the balancer idles until
  the next ≥ start; turn it on manually and the balancer will still enforce
  the stop floor.

## Components

- `ecoflow/balancer.py` — pure logic + config persistence, no I/O to the
  cloud (same discipline as decision.py/controls.py):
  - `Config(enabled=False, start_soc=99, stop_soc=60)` with
    `load(path)` / `save(path)` to `data/balancer.json`. Validation:
    0 < stop < start ≤ 100.
  - `desired_action(soc, ac_on, cfg) -> "on" | "off" | None`
- `dashboard.py` — background thread (daemon), one tick per 60 s:
  1. Skip entirely unless config enabled.
  2. Fresh `quota/all` for B → SOC (`ems.f32LcdShowSoc`) and AC state
     (`inv.cfgAcEnabled`).
  3. If `desired_action` says so, send cmd 66 via the same
     `controls.build_params` path the Control view uses, then verify readback.
  4. Record status (state, last action, reason, errors) in a module dict.
  - Failures (offline 1008 flap, timeouts) are logged to status and retried
    next tick. B's hardware `minDsgSoc` is the backstop if a turn-off can't
    get through.
  - Thread starts only in the Werkzeug reloader child
    (`WERKZEUG_RUN_MAIN == "true"`), so the file-watcher parent never runs it;
    restart-safe because all state is re-derived from the device + JSON.
  - `GET /api/balancer` → config + live status; `POST /api/balancer` →
    update config (validated), persisted to JSON.
- `web/index.html` — "Auto Balance" card in the **Control view** (not
  Overview): enable toggle, start/stop sliders, status line.

## Emergency fallback (added 2026-08-04, Mike's request)

The deferred emergency mode, now wired — as a stateless layer in
`balancer.py` (`emergency_action`), consulted by the same tick *before* the
normal rules. Not decision.py's version: no load-wattage condition, SOC only.

- **Rescue:** A ≤ `emergency_a_soc` (**15%**) and B above its floor → force
  B's AC output on, regardless of B's fill.
- **Hold:** while the output is on and A < `emergency_a_clear_soc` (**30%**),
  the normal `stop_soc` cutoff is suppressed — B keeps feeding A.
- **Floor:** B ≤ `emergency_b_floor` (**15%**) → off, always (also a backstop
  for manual transfers). Re-trigger requires B ≥ floor + 3% (`EMERGENCY_REARM`)
  to prevent relay chatter as solar lifts B off the floor.
- Independent `emergency_enabled` toggle (ships **on**), works even when the
  normal balancer is disabled. The tick reads A's quota only when the layer is
  enabled; an A-read failure sets `a_error` in status and the layer goes inert
  for that tick (normal rules unaffected).
- Status state `emergency` (amber chip) while a rescue is active.
- Known caveats: a cloud-offline A serves stale SOC, so a rescue can't trigger
  on data the cloud doesn't have; and if B's hardware `minDsgSoc` is set above
  the floor, the hardware cuts discharge first.

## Non-goals / deferred

- ~~Emergency transfer when A is critical~~ — wired 2026-08-04, see above.
- Automating A's charge-speed wattage.
- Surviving machine sleep — same limitation as the poller; the loop simply
  resumes on wake.

## Safety

- Ships with `enabled: false`; nothing actuates until switched on in the
  dashboard (the dashboard is live with real credentials and auto-reloads on
  edit, so the default matters).
- Commands go through the existing validated `controls` registry (cmd 66
  sends the companion `xboost` field from fresh quota — the 1008 trap).

## Testing

- `tests/test_balancer.py`: hysteresis truth table (all SOC × ac_on
  combinations at and around thresholds), config round-trip, defaults,
  validation rejects stop ≥ start. Pure functions — no mocks.
- Tick + `/api/balancer` endpoints tested with the same FakeClient pattern
  as test_dashboard_control.py (the thread is a thin loop around a testable
  `balancer_tick()`).
