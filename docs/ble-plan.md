# Bluetooth (BLE) backup path

**Status 2026-08-03 (evening): BUILT, radio-untested.** Everything below the
range section is implemented and unit-tested against fakes; the only missing
piece is a BLE adapter within range of the truck. First-run checklist:

1. Get a radio in range (options below).
2. `.venv\Scripts\python.exe scripts\ble_login.py` → add `ECOFLOW_USER_ID=...` to `.env` (one time).
3. `.venv\Scripts\python.exe scripts\ble_poc.py` → read-only side-by-side BLE vs cloud snapshot.
4. If sane, add `ECOFLOW_BLE=1` to `.env` and restart the dashboard. Cloud
   stays primary; BLE kicks in per-unit when cloud data goes stale (>120 s)
   or a write bounces off an offline unit. The UI chip shows "via bluetooth".

What's built:
- `ecoflow/ble/eflib/` — vendored protocol lib (Apache-2.0, see VENDORED.md;
  unmodified, pinned commit). Delta Pro incl. extra-battery kit info + controls.
- `ecoflow/ble_client.py` — sync facade over an asyncio loop thread;
  `get_quota_all()` returns a **cloud-quota-shaped dict** (same names and
  0.1 W/mV scalings) so `normalize()`/readback logic are unchanged;
  `set_control(sn, key, value)` takes `controls.py` keys. 12 of 16 controls
  map to verified eflib packets; `pv_chg_type`, `bypass_auto`, `gen_auto_*`
  raise BleUnsupported (candidate packets exist but semantics unconfirmed).
- `dashboard.py` — env-gated fallback on both paths (stale reads, 1008-offline
  writes) with BLE readback verification; per-unit `source: cloud|ble`.
- `tests/test_ble.py` — mapping, adapters (quiet inversion, mA→A), fallback.

Note: eflib's own source confirms the beep inversion the cloud probe hit:
`beep_mode: 0 = buzzer on, 1 = buzzer off (inverted in protocol)`.

Known BLE-snapshot gaps (degrade gracefully): no pack mAh capacities (Wh
estimates read 0), no lifetime Wh counters, no X-Boost readback. Cloud
remains the primary source partly for this reason.

---

## Original plan (groundwork record)

Goal: keep state **and control** working when the units' cloud uplink drops
(2026-08-03 showed 34–63% of solar-active hours serving frozen cloud snapshots).
The original Delta Pro has no local Wi-Fi API — BLE is the only cloud-free path.

## Groundwork verified 2026-08-03

| Question | Answer |
|---|---|
| Does the dashboard PC have a BLE radio? | Yes — Intel Wireless Bluetooth, working (heard 15 ambient devices) |
| Is the protocol available for original Delta Pro? | Yes — [rabits/ha-ef-ble](https://github.com/rabits/ha-ef-ble) supports original Delta Pro incl. up to 2 extra batteries, with **controls** (switches/sliders/selects), not just telemetry |
| Auth requirements | EcoFlow **User ID** (retrievable via app login flow) + units already bound to the account (they are) |
| Python stack | `bleak` (installed in .venv) + ha-ef-ble's `eflib/` protocol layer — **vendored inside the HA repo, not on PyPI**; we vendor it too (check repo LICENSE when cloning) |
| **Are the units in range of the PC?** | **NO** — 20 s scans hear neither SN while hearing 15 other devices. This is the blocking constraint. |

## The range problem (blocker, needs Mike)

The PC's radio never hears the trucks' advertisements. Before any code:

1. **Confirm the units advertise at all**: stand next to the truck with the
   nRF Connect phone app (or a laptop running `scratchpad ble_scan.py`).
   Expect BLE names containing the SN tails `2222480` / `B301010`.
2. **Pick a radio placement** (in preference order):
   - USB BLE dongle (BT 5.x long-range class) on an extension to a window
     facing the truck — cheapest, keeps everything on this PC.
   - Small always-on node in/near the truck (Pi Zero 2 W ~ $20) running a
     tiny BLE→HTTP bridge; the dashboard talks to it over Wi-Fi/LAN. Also
     solves the "poller dies when the PC sleeps" problem for good.
   - ESPHome BLE proxy on an ESP32 (~$5) — works for HA, but we'd have to
     speak its proxy protocol; more glue than it's worth here.

## Implementation phases (after range is solved)

1. **Vendor `eflib`** from ha-ef-ble into `ecoflow/ble/` (keep upstream
   attribution + license file; pin the commit in a comment).
2. **PoC read** (`scripts/ble_poc.py`): connect to one unit by SN, complete
   the auth handshake (User ID), stream quota-equivalent state, print SOC and
   watts next to the cloud values for sanity.
3. **`ecoflow/ble_client.py`**: same surface as `EcoFlowClient` for the calls
   the dashboard uses (`get_quota_all`-shaped dict, `set_quota`), mapping
   eflib's typed fields back onto the cloud field names so `normalize()`,
   `controls.py` readbacks, and the poller CSV keep working unchanged.
4. **Fallback wiring in `dashboard.py`**: cloud stays primary; when a unit's
   `data_age_s` crosses ~120 s or a write bounces 1008-offline, try BLE for
   that unit. Payload gains `source: "cloud" | "ble"` per unit; the UI's
   freshness chip shows "via bluetooth" instead of "data frozen".
5. **Controls over BLE**: route `POST /api/control` through the BLE client
   when that unit is cloud-offline; readback-verify works the same way.
6. **Poller**: optionally sample over BLE during cloud gaps so the history
   CSV stops inheriting cloud outages.

## Risks / notes

- eflib is reverse-engineered; an EcoFlow firmware update could break it.
  The cloud path stays primary partly for this reason.
- The phone app may hold the unit's single BLE connection when Mike is
  nearby with the app open — the bridge must reconnect gracefully.
- Windows BLE (WinRT) pairing quirks are the usual bleak pain point; the
  Pi-bridge option sidesteps them entirely.
- `bleak` is already installed in `.venv`; scan scripts live in the session
  scratchpad and are trivially recreatable (`BleakScanner`, 20 s, match SNs).
