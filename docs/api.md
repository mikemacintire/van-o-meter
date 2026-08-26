# EcoFlow Developer API notes (original Delta Pro)

Confirmed from the official doc (https://developer.ecoflow.com/us/document/PP?id=2058836526572933122) on 2026-07-20. The doc site is a JS-rendered SPA — plain fetch/curl returns an empty shell; use a browser tool to read it.

## REST

Base: `https://api.ecoflow.com`

- `GET /iot-open/sign/device/list` — devices + online status
- `GET /iot-open/sign/device/quota/all?sn=...` — all quota fields
- `PUT /iot-open/sign/device/quota` — send a command

Signing (all requests): flatten params (dot notation for nested, `[i]` for arrays), sort key=value pairs ASCII-ascending, append `accessKey`, `nonce` (6-digit), `timestamp` (ms), HMAC-SHA256 with secretKey, lowercase hex. Headers: `accessKey`, `nonce`, `timestamp`, `sign`. Implemented in `ecoflow/signing.py`.

## Commands (all cmdSet 32)

Full set from the official Delta Pro page (re-read 2026-08-03). One row per command; served to the dashboard by `ecoflow/controls.py`, which is the canonical registry (validation ranges live there).

| id | params | purpose | readback |
|----|--------|---------|----------|
| 66 | `enabled: 0/1` **and** `xboost: 0/1` | AC output toggle (X-Boost command) | `inv.cfgAcEnabled`, `inv.cfgAcXboost` |
| 81 | `enabled: 0/1` | 12V car port switch | `mppt.carState` |
| 69 | `slowChgPower: W` | AC charge speed | `inv.cfgSlowChgWatts` |
| 49 | `maxChgSoc` | charge limit | `ems.maxChargeSoc` |
| 51 | `minDsgSoc` | discharge floor (hardware backstop on B) | `ems.minDsgSoc` |
| 82 | `chgType: 0/1/2` | PV charge source auto/MPPT/adapter | `mppt.cfgChgType` |
| 84 | `enabled: 0/1` | bypass AC auto-start | `inv.acPassbyAutoEn` |
| 38 | `enabled: 0/1` | beep switch — **accepted but readback never moves** (see below) | `pd.beepState` |
| 39 | `lcdBrightness: 0-100` (128 = auto) | screen brightness | `pd.lcdBrightness` |
| 39 | `lcdTime: s` (0 = never) | screen timeout — **same id, different param** | `pd.lcdOffSec` |
| 33 | `standByMode: min` (0 = never) | unit standby | `pd.standByMode` |
| 153 | `standByMins: min` (0 = never) | AC standby | `inv.cfgStandbyMin` |
| 71 | `currMa: 4000/6000/8000` | car input current | `mppt.cfgDcChgCurrent` |
| 52 | `openOilSoc: %` | smart-generator auto-on threshold | `ems.minOpenOilEbSoc` |
| 53 | `closeOilSoc: %` | smart-generator auto-off threshold | `ems.maxCloseOilEbSoc` |

No per-pack (Extra Battery) settable params exist — 49/51 are EMS-level and govern the whole bank.

## Writes to an offline unit fail with 1008 (confirmed live 2026-08-03)

**`code 1008: request fail,please check your params` does not necessarily mean bad params.** The cloud returns the identical error for a structurally perfect command sent to a unit that is offline. Meanwhile `quota/all` for that unit **keeps serving stale cached data with `code 0`**, so reads look healthy while every write bounces. The only honest liveness signal is `online` in `device/list` (observed flipping 0/1 within minutes on truck WiFi — treat it as volatile).

Verified by isolation: our signature is correct (tampering with the sign string yields `8521 signature is wrong`, so 1008 is a post-signature params-level verdict), and the byte-identical no-op command succeeded on the online unit while failing on the offline one. `dashboard.py` re-checks `device/list` on any 1008 and reports "unit offline" instead.

## `quota/all` can serve `data: null` mid-flap (observed live 2026-08-25)

During an offline flap the quota endpoint sometimes answers `code 0` with `data: null` instead of stale data — a read that "succeeds" and returns nothing. Any consumer must treat a `None` quota as "no reading", not crash: it took down `/api/control`'s verify loop with a 500 the first time it happened mid-command. `controls.matches` and both verify loops now tolerate it (keep the last good quota, report `applied: false`).

## id 51 (minDsgSoc) may be capped at current SOC (unconfirmed, 2026-08-25)

With B at ~6% SOC, `minDsgSoc: 5` applied but `minDsgSoc: 15` was accepted (`code 0`) with the readback never moving off 5. Consistent with the firmware refusing a discharge floor above the current charge level, but B was also flapping offline that night, so a lost command isn't ruled out. Retry raising the floor when the unit is above the target value.

## id 38 (beep) accepted but inert (observed 2026-08-03)

On unit B (online, quota fresh): `{"cmdSet":32,"id":38,"enabled":1}` and `enabled:0` both returned `code 0`, yet `pd.beepState` stayed `1` across 20 s of polling in both directions. Tested both polarity hypotheses (doc implies enabled↔beepState direct; HA integrations suggest enabled = "beeper on" i.e. inverted) — neither produced a readback change. Unresolved: silent-no-op firmware quirk, very slow application, or a readback that doesn't track the setting. Needs an ear next to the unit to settle.

## `device/list` `online` is flappy and lags (observed 2026-08-03)

The flag flipped 0→1→0 within minutes while unit B's quota stayed fresh (solar watts moving, SOC climbing) and writes were being **accepted**. Meanwhile a genuinely disconnected unit (A that afternoon) shows `online: 0` *and* frozen quota *and* bouncing writes. So: `online: 0` + fresh quota = ignore the flag; `online: 0` + frozen quota = actually offline. The dashboard treats the flag as advisory (warning badge) and never disables controls from it.

## Command verify-by-readback

`code 0` on PUT means **queued**, not applied. The dashboard's `/api/control` polls `quota/all` (1.5 s interval, 10 s cap) until the readback field matches, and reports `applied: true/false` with the hardware's actual value. No-op writes (value already current) confirm immediately.

**id 66 requires BOTH fields** (confirmed live 2026-07-27, first real write): sending `{"enabled": 0}` alone is rejected with `code 1008: request fail,please check your params`. Sending `{"enabled": 0, "xboost": 0}` is accepted (`code 0`). The official doc calls this "Setting the X-Boost switch" and shows both keys — it is one combined command, so always pass the current `inv.cfgAcXboost` value alongside `enabled` unless you intend to change X-Boost too. Auth/signing for PUT bodies works unchanged (a 1008 means params, not signature).

`slowChgPower` accepted range on Delta Pro is TBD — app slider is ~200–1800W; verify empirically by setting values and reading back `inv.cfgSlowChgWatts`.

## Key quota fields

`bmsMaster.soc`, `mppt.inWatts` (solar, **0.1W units** — confirmed live 2026-07-25: reads 10x `pd.wattsInSum`; divide by 10), `pd.wattsInSum`, `pd.wattsOutSum`, `inv.inputWatts` (AC charge W), `mppt.chgState`.

## Verifying a B→A transfer is live (open question, 2026-07-27)

Do **not** trust `inv.inputWatts` on A as the "transfer is happening" signal. Observed 2026-07-27 09:50: B reported `inv.outputWatts` 1034 W while A reported `inv.inputWatts` 0, `inv.acInVol` 0, `inv.acInAmp` 0 and `pd.wattsInSum` 133 (≈ solar alone) — yet the cumulative counters over that window prove the transfer was real: B `pd.dsgPowerAc` +1300 Wh vs A `pd.chgPowerAc` +1245 Wh (95.8% match, consistent with the expected double-conversion loss).

Mike's read is that charge arriving at A lands in the **Extra Battery**, and the per-pack fields support it — each pack meters itself:

| field | Main pack | Extra pack |
|-------|-----------|------------|
| input | `bmsMaster.inputWatts` | `bmsSlave1.inputWatts` |
| output | `bmsMaster.outputWatts` | `bmsSlave1.outputWatts` |
| SOC | `bmsMaster.f32ShowSoc` | `bmsSlave1.f32ShowSoc` |

Snapshot 2026-07-27 09:56: `bmsMaster.inputWatts` 0 while `bmsSlave1.inputWatts` 47 — the packs report independently, and their SOCs drift apart (Main 34.27% vs Extra 29.45%, system `ems.f32LcdShowSoc` 31.83% ≈ their mean). This is a known source of confusion when reading A's state.

**Unresolved:** which instantaneous field reliably indicates an active transfer. Settling it needs samples taken *during* a transfer. Since 2026-08-15 the poller logs per-pack SOC and voltage (`pack_main_soc`/`pack_extra_soc`/`pack_main_mv`/`pack_extra_mv`) but still no per-pack `inputWatts`/`outputWatts` and no `inv.acInVol`/`inv.acInAmp`. Until then, difference `pd.chgPowerAc` / `pd.dsgPowerAc` over an interval rather than reading an instantaneous watt field.

## Per-pack SOC gauges drift apart; pack voltage is the ground truth (confirmed 2026-08-15)

The two SOCs in the table above diverging does **not** mean the packs hold different charge. Read live on A, twice 20 s apart:

| | Main | Extra |
|---|---|---|
| `f32ShowSoc` | 39.0 % | 23.9 % |
| sum of the 15 `cellVol` | 49.216 V | 49.213 V |
| `vol` | 49292 | 49304 |
| `amp` | -1080 | -1147 |
| `soh` / `fullCap` | 100 / 79902 | 100 / 79964 |
| `maxVolDiff` | 8 mV | 6 mV |

The cell stacks agree to **3 mV across 15 cells**, both slots are engaged (`ems.openBmsIdx` 3, `bms0Online`/`bms1Online` both 3), and the packs share current ~48/52. Packs bonded to one bus are at the same voltage by construction — a real 15-point charge gap would drive current until it closed. So the packs are equal and the *gauges* are not.

Each pack coulomb-counts its own SOC, and on LFP the mid-range voltage curve is too flat to correct the accumulated error. Corroboration: both packs also report `actSoc`/`targetSoc`, reading 24/24 on Main and 21/21 on Extra — Main's own alternate figure sits 15 points below its display figure and next to Extra's, so Main's gauge is the one that wandered. (Both are undocumented fields; treat the direction as a hypothesis.)

**The counters only re-anchor at full charge, and A almost never gets there** — ≥95 % on 1 logged day in 22. On that day (2026-08-07) the re-anchor is visible in `samples.csv`: 90.7 → 94.6, then 94.8 → 98.5, each in a single 5-minute step while ~125–240 W was flowing. That is ~15 Wh of real energy against steps implying ~270 Wh — the counter snapping, not charge arriving. A then held ≥99 % for ~15 minutes before the A/C pulled it down.

Consequences:

- `ems.f32LcdShowSoc` is the capacity-weighted mean of both gauges (arithmetic verified: 31.595 reproduced exactly), so **every consumer of A's SOC averages one good gauge with one drifted one** — Overview, the balancer's A-full cutoff and emergency floor, and the Backup metric.
- `remainCap` is SOC × `fullCap`, so `dashboard.py`'s `wh_est` inherits the same error.
- Remedy is a shore-power charge to 100 % held for a few hours with the A/C off. Solar can't do it here (shade-limited, and the load pulls it straight back down). The A-full cutoff doesn't interfere — it only stops B's transfer, not a wall charge.

Measured gap over time: 4.8 points on 2026-07-27, **15.2 points on 2026-08-15**. Not degradation — `soh` is 100/100 and `fullCap` differs by 0.08 %.

**Not yet ruled out:** every reading was taken at ~1.1 A per pack. A high-resistance connection to the Extra Battery would only show under real current — re-probe during an A/C compressor pull and compare `vol` and `amp` between packs.

## Transfer accounting: B→A is a transfer, not consumption (measured 2026-08-03)

Fleet-level rule the dashboard follows: energy moving between stores stays in the system. Two flows qualify, with very different tolls:

- **A main → Extra pack:** free, and invisible to the energy counters. `pd.wattsOutSum` on A *does* count it while charging (up to ~548 W observed, scaling with charge rate; unit B, packless, shows 0 residual) — but the five cumulative Wh counters exclude it, which is why A's energy books balance at ~98% charge efficiency. So `watts_out` overstates A's real load while charging; external load is `inv.outputWatts` + DC ports.
  - **The inflation is one-sided.** `pd.wattsInSum` is clean: measured live 2026-08-03 during a transfer with 440 W of pack shuffle on A, `wattsInSum` matched `mppt.inWatts + inv.inputWatts` to within 0 W on both units. So `in_w − load_w` is a trustworthy per-unit net; summing it across units gives the true bank fill rate and charges the B→A conversion loss to the system (+71 W measured, vs +113 W for a naive solar−load).
- **B → A (double conversion):** real toll. Meter-to-meter 96.9% (2931 Wh B `dsgPowerAc` → 2841 Wh A `chgPowerAc` over 9 days) — but that's just the wire between the converters. Battery-to-battery on the one cleanly-logged event (2026-07-29, 76 min): 1789 Wh out of B's pack → 1520 Wh into A's bank = **~85%**. Single event, SOC-gauge limited (a shorter sub-window read 101%), so treat as ~15% ± a few points. A clean night transfer would tighten it.

Idle overhead, measured from SOC decline in dark/no-load segments: A with AC outlets **off** ~1 W (66.8 h), B off ~5 W (84.3 h), A with AC outlets **on but unloaded ~38 W** (9.1 h). The inverter-standby 38 W is the dominant controllable loss — toggling A's AC output off when unused saves ~0.9 kWh/day.

The dashboard treats B's `ac_out` as transfer (hard-wired into A's input), matches it against A's `ac_in` per day (`min` of the two), and only calls unmatched AC-in "wall power".

## `bmsSlave1` is always present, even with no Extra Battery (confirmed 2026-08-02)

`quota/all` ships a **full 54-field `bmsSlave1.*` block on both units**, including unit B, which has no Extra Battery. On B the block holds stale defaults that look plausible enough to fool a null check — `soc` 32, `f32ShowSoc` 31.81, `vol` 49577, `remainCap` 25448. Presence therefore **cannot** be inferred from the pack fields themselves, and `bmsSlave1.soc is None` is not a valid guard (it was the bug behind a phantom "Extra" pack on B's dashboard tile and a 3.8 kWh overstatement of the bank).

The tells that B's block is fake, none of which is individually conclusive:

| field | A (real pack) | B (phantom) |
|-------|---------------|-------------|
| `cycles` | 2 | **0** |
| `amp` | 810 | **0** |
| `temp` | 27 C | **18 C** (below box ambient) |
| `fullCap` | 79982 | **80000** (exactly designCap) |
| `chgDsgState` | 2 | **0** |

The block also comes and goes between fetches on B — one call returned all 54 fields, the next omitted `remainCap`, `num`, `cellSeriesNum` and others.

**Authoritative presence flag: `ems.bms{N}Online`** — `0` = slot empty, `3` = pack online. A: `bms0Online` 3, `bms1Online` 3, `bms2Online` 0. B: `bms0Online` 3, `bms1Online` **0**, `bms2Online` 0. Consistent with `bmsSlave2.*` being absent from both payloads. Three further fields agree on the count: `ems.maxAvailableNum`, `ems.bmsModel` and `pd.model` all read 2 on A and 1 on B.

Decisive cross-check — **`ems.f32LcdShowSoc` is the capacity-weighted mean of exactly the packs the EMS counts:**

- A: master+slave1 = 25.6620 vs `ems.f32LcdShowSoc` 25.6620 (diff 0.0000) — counts both.
- B: master-only = 75.0582 vs `ems.f32LcdShowSoc` 75.0582 (diff 0.0000); master+slave1 would be 53.4268 — counts master only.

So the hardware knows B has one pack; only naive client code sees two. The poller is unaffected: it logs `ems.f32LcdShowSoc`, which is already correct on both units.

Pack energy: `remainCap`/`fullCap` are mAh (`designCap` 80000 = 80 Ah). Do **not** derive Wh as Ah x nominal volts — 15S LFP nominal 48 V gives 3840 Wh/pack against EcoFlow's 3600 Wh rating, overstating the bank by 6.7 %. `dashboard.py` uses `PACK_WH * remainCap / fullCap` instead, which yields the expected 7.2 kWh (A) + 3.6 kWh (B) = 10.8 kWh.

## No historical data endpoint (confirmed 2026-07-25)

There is **no way to backfill history** for the Delta Pro. Every chart has to be built from samples we log ourselves, so the poller's uptime is the dataset.

- The Open API's full endpoint list (General Information page) is five endpoints: `device/list`, `device/quota` (PUT set / POST get-keys), `device/quota/all`, `sign/certification`. All current-state.
- `POST /iot-open/sign/device/quota/data` **does** exist but is documented only for **Power Ocean** and **STREAM**, and its `code` param is a product-specific dashboard-metric key with none published for Delta-family devices. Its response is a flat list of aggregates, not a time series. Verified absent from the Delta Pro, Delta Pro 3, Delta Pro Ultra, DELTA 3 MAX PLUS and PowerStream pages.
- Delta Pro's MQTT section states "Get & Get Reply: Not Support".
- Across the reference implementations (tolwi, tess1o, rustyy, berezhinskiy, TarasKhust) exactly one wraps `quota/data`, commented "works for PowerOcean". Nobody calls it with a Delta code.
- The app-internal API doesn't expose history either — the energy-report screens have never been reverse-engineered publicly. No unofficial backdoor to fall back on.

### Cumulative counters are the standard workaround

Five `pd.*` lifetime Wh counters advance in real time; differencing two samples gives exact energy for that window. This beats integrating instantaneous watts because it survives polling gaps. Home Assistant treats these as `total_increasing`; ecoflow_exporter feeds them to Prometheus `increase()`.

| field | meaning |
|-------|---------|
| `pd.chgSunPower` | cumulative solar charged |
| `pd.chgPowerAc` | cumulative AC charged |
| `pd.chgPowerDc` | cumulative DC/adapter charged |
| `pd.dsgPowerAc` | cumulative AC discharged |
| `pd.dsgPowerDc` | cumulative DC discharged |

Verified live 2026-07-25: unit A's `chgSunPower` rose 13540 → 14300 over ~4 h (+760 Wh ≈ 190 W average), matching observed solar. All five are logged even where unused today — uncaptured data is unrecoverable.

**Counters reset on firmware update / factory reset**, so `daily_energy()` discards negative deltas rather than charging them to the day.

Also noted: no rate limits are documented anywhere (treat as unknown, poll conservatively), regional hosts differ (`api-a` Americas / `api-e` Europe vs the doc's `api.ecoflow.com`), and `device/list` returns only *owned* devices, not shared ones.

## MQTT

Pushed quota reports on `/open/{certificateAccount}/{sn}/quota`; `set`/`set_reply` topics for commands. No on-demand GET over MQTT — reads come from pushes or the HTTP endpoint. `certificateAccount` comes from the portal's General Information / certification endpoint.

## Dual charging (AC + solar)

Doc says `mppt.chgState: 2` = "standby (DC charging stopped during AC charging)", implying solar pauses during AC charge. Mike has personally observed AC + solar charging simultaneously and cumulatively on his Delta Pros — treat dual charging as working, but confirm on day one: `mppt.inWatts` stays > 0 on unit A while `inv.inputWatts` > 0.

## Other constraints

- Original Delta Pro has NO local WiFi API (port 8055 closed by firmware). Cloud REST/MQTT only; BLE (rabits/ha-ef-ble) is the only offline path.
- Reference implementations: github.com/tolwi/hassio-ecoflow-cloud, github.com/TarasKhust/ecoflow-api-mqtt.
