"""Local dark-theme dashboard for both Delta Pros.

Usage:
    .venv\\Scripts\\python.exe dashboard.py   # serves http://127.0.0.1:8642

Read-only. Live state comes from quota/all (cached briefly so extra tablet tabs
don't hammer the cloud API); history comes from the poller's CSV.
"""

import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, send_from_directory

from ecoflow.client import EcoFlowClient, EcoFlowApiError
from ecoflow import balancer, controls, history, single_instance

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "data" / "samples.csv"
LIVE_TTL = 10        # seconds; quota/all is the only cloud call
HISTORY_TTL = 20     # seconds; poller writes every 30
VERIFY_TIMEOUT = 10  # seconds to wait for a command's readback to match
VERIFY_POLL = 1.5    # seconds between readback polls
BAL_TICK_S = 60      # seconds between auto-balancer checks
BAL_CFG_PATH = Path(__file__).parent / "data" / "balancer.json"
BAL_LOG_PATH = Path(__file__).parent / "logs" / "balancer.jsonl"
INSTANCE_LOCK_PORT = 8643   # held by the reloader parent; see __main__
PACK_WH = 3600       # EcoFlow's rating for one pack (Delta Pro or Extra Battery)
# Full capacity per unit for the 7-day drain estimate: A carries the Extra
# Battery, B is standalone. Static on purpose — history rows log SOC only.
DRAIN_WH_FULL = {"A": 2 * PACK_WH, "B": PACK_WH}

# range key -> (window, bucket width) in seconds
RANGES = {
    "24h": (86400, 300),
    "7d": (7 * 86400, 1800),
    "30d": (30 * 86400, 7200),
}
SERIES_METRICS = ["solar_w", "watts_out", "ac_out_w", "dc_out_w", "ac_charge_w", "soc"]

load_dotenv(ROOT / ".env")

app = Flask(__name__)
client = EcoFlowClient(os.environ["ECOFLOW_ACCESS_KEY"], os.environ["ECOFLOW_SECRET_KEY"])
UNITS = {"A": os.environ["ECOFLOW_SN_A"], "B": os.environ["ECOFLOW_SN_B"]}

# Bluetooth fallback (docs/ble-plan.md). Off unless ECOFLOW_BLE=1 — the radio
# path is unverified until a BLE adapter is within range of the truck.
BLE_STALE_S = 120    # cloud data older than this triggers a BLE read attempt
try:
    from ecoflow.ble_client import BleClient, BleError, BleUnsupported
except Exception:                                    # bleak/deps unavailable
    BleClient = None

    class BleError(Exception):
        pass

    class BleUnsupported(BleError):
        pass

ble = None
if os.environ.get("ECOFLOW_BLE") == "1" and BleClient is not None:
    try:
        ble = BleClient(os.environ.get("ECOFLOW_USER_ID"), UNITS)
    except Exception as e:
        print(f"BLE fallback disabled: {e}")

_live_cache = {"ts": 0.0, "payload": None}
# Auto-balancer (docs/superpowers/specs/2026-08-03-auto-balancer-design.md):
# config persists in balancer.json, live status is rebuilt every tick.
_bal = {"cfg": balancer.Config.load(BAL_CFG_PATH), "status": {}}
_bal_wake = threading.Event()   # POST /api/balancer sets it to apply promptly
_hist_cache = {}
# unit -> {"q": last raw quota, "ts": when it last CHANGED}. The cloud serves
# frozen snapshots for a disconnected unit, so "how long since anything in
# the payload moved" is the honest freshness signal (resets on restart).
_freshness = {}
_lock = threading.Lock()
# Writes get their own lock: a readback-verify can hold it for ~10 s, and
# sharing _lock would stall every /api/live behind a slow command.
_ctl_lock = threading.Lock()
# History too: /api/live holds _lock across its cloud fetch (up to three
# 30 s-timeout calls), and CSV aggregation must not queue behind that —
# Flask serves threaded, so these really do run concurrently.
_hist_lock = threading.Lock()


def _num(q, key, scale=1):
    v = q.get(key)
    return None if v is None else (v / scale if scale != 1 else v)


def _pack(q, prefix, name, slot):
    """One battery pack; None unless the EMS reports that slot online.

    quota/all always ships a full 54-field `bmsSlave1.*` block whether or not an
    Extra Battery is attached — on a bare unit it carries stale defaults that look
    plausible (confirmed live 2026-08-02 on B: soc 32, but cycles 0, amp 0, temp
    18C, fullCap exactly 80000). So presence cannot be inferred from the pack
    fields; `ems.bms{slot}Online` is the authoritative flag. Cross-check:
    `ems.f32LcdShowSoc` equals the capacity-weighted mean of exactly the packs
    the EMS counts (A both = 25.6620; B master-only = 75.0582).
    """
    if not q.get(f"ems.bms{slot}Online"):
        return None
    remain_ah = _num(q, f"{prefix}.remainCap", 1000)
    full_ah = _num(q, f"{prefix}.fullCap", 1000)
    return {
        "name": name,
        "soc": _num(q, f"{prefix}.f32ShowSoc"),
        "cycles": q.get(f"{prefix}.cycles"),
        "soh": q.get(f"{prefix}.soh"),
        "temp": q.get(f"{prefix}.temp"),
        "cell_temp_min": q.get(f"{prefix}.minCellTemp"),
        "cell_temp_max": q.get(f"{prefix}.maxCellTemp"),
        "cell_spread_mv": q.get(f"{prefix}.maxVolDiff"),
        "vol": _num(q, f"{prefix}.vol", 1000),
        # Each pack meters itself; these are the only per-pack flow signals
        # (docs/api.md — B→A transfer charge lands in whichever pack accepts it).
        "in_w": q.get(f"{prefix}.inputWatts"),
        "out_w": q.get(f"{prefix}.outputWatts"),
        "wh_est": round(PACK_WH * remain_ah / full_ah) if full_ah else 0,
        "wh_full": PACK_WH,
    }


def normalize(q):
    """Flatten quota/all into the dashboard's display model.

    Scaling quirks confirmed live (docs/api.md): mppt.* watt/vol fields are
    0.1-unit, mppt amps 0.01A, bms/inv voltages mV. inv.*Watts are plain W.
    """
    packs = [p for p in (_pack(q, "bmsMaster", "Main", 0),
                         _pack(q, "bmsSlave1", "Extra", 1)) if p]
    usb_w = sum(q.get(k) or 0 for k in
                ("pd.usb1Watts", "pd.usb2Watts", "pd.qcUsb1Watts", "pd.qcUsb2Watts",
                 "pd.typec1Watts", "pd.typec2Watts"))
    # `pd.wattsOutSum` is NOT consumption: while charging it also counts the
    # internal main->Extra pack transfer (up to ~548 W on A; unit B, packless,
    # shows 0 residual — docs/api.md). That power never leaves the box, so
    # `load_w` — the sum of the actual output ports — is the only honest draw.
    car_w = _num(q, "mppt.carOutWatts", 10) or 0
    load_w = (q.get("inv.outputWatts") or 0) + car_w + usb_w
    return {
        "soc": _num(q, "ems.f32LcdShowSoc"),
        "chg_remain_min": q.get("ems.chgRemainTime"),
        "dsg_remain_min": q.get("ems.dsgRemainTime"),
        "solar": {
            "w": _num(q, "mppt.inWatts", 10),
            "v": _num(q, "mppt.inVol", 10),
            "a": _num(q, "mppt.inAmp", 100),
            "temp": q.get("mppt.mpptTemp"),
        },
        "ac": {
            "out_enabled": q.get("inv.cfgAcEnabled"),
            "xboost": q.get("inv.cfgAcXboost"),
            "out_w": q.get("inv.outputWatts"),
            "in_w": q.get("inv.inputWatts"),
            "slow_chg_w": q.get("inv.cfgSlowChgWatts"),
            "temp": q.get("inv.outTemp"),
            # Measured only while the inverter runs; all read 0 with AC output off,
            # so the console shows them only when non-zero and falls back to the
            # configured nameplate voltage. `inv.invOutFreq`'s units are unconfirmed
            # (it has never been read non-zero here) — the UI treats it as Hz only
            # if it lands in a plausible mains range rather than asserting a mapping.
            "out_v": _num(q, "inv.invOutVol", 1000),
            "out_a": _num(q, "inv.invOutAmp", 1000),
            "out_hz": q.get("inv.invOutFreq"),
            "cfg_v": _num(q, "inv.cfgAcOutVoltage", 1000),
            "in_v": _num(q, "inv.acInVol", 1000),
        },
        "dc": {"car_w": car_w, "usb_w": usb_w},
        "in_w": q.get("pd.wattsInSum"),
        "out_w": q.get("pd.wattsOutSum"),
        "load_w": load_w,
        "xfer_w": max(0, round((q.get("pd.wattsOutSum") or 0) - load_w, 1)),
        "limits": {"max_chg_soc": q.get("ems.maxChargeSoc"),
                   "min_dsg_soc": q.get("ems.minDsgSoc")},
        "fan_level": q.get("ems.fanLevel"),
        "lifetime_wh": {
            "solar_in": q.get("pd.chgSunPower"),
            "ac_in": q.get("pd.chgPowerAc"),
            "ac_out": q.get("pd.dsgPowerAc"),
            "dc_out": q.get("pd.dsgPowerDc"),
        },
        "packs": packs,
        "wh_est": sum(p["wh_est"] for p in packs),
        "wh_full": sum(p["wh_full"] for p in packs),
        # Current value of every settable control, keyed by controls.CONTROLS.
        "settings": controls.current_values(q),
    }


@app.get("/api/live")
def api_live():
    now = time.time()
    with _lock:
        if _live_cache["payload"] is None or now - _live_cache["ts"] > LIVE_TTL:
            # quota/all keeps serving cached data for an offline unit, so the
            # device list is the only honest liveness signal (found 2026-08-03:
            # A looked healthy while every write to it bounced with 1008).
            try:
                online = {d.get("sn"): d.get("online") for d in client.get_device_list()}
            except Exception:
                online = {}
            units = {}
            for unit, sn in UNITS.items():
                try:
                    q = client.get_quota_all(sn)
                    fr = _freshness.get(unit)
                    if fr is None or q != fr["q"]:
                        _freshness[unit] = fr = {"q": q, "ts": now}
                    units[unit] = normalize(q)
                    units[unit]["online"] = online.get(sn)
                    units[unit]["data_age_s"] = round(now - fr["ts"])
                    units[unit]["source"] = "cloud"
                except Exception as e:
                    units[unit] = {"error": str(e)}
                # cloud gone stale or errored -> try the local radio
                if ble is not None and (
                        units[unit].get("error")
                        or units[unit].get("data_age_s", 0) >= BLE_STALE_S):
                    try:
                        u = normalize(ble.get_quota_all(sn))
                        u["online"] = online.get(sn)
                        u["data_age_s"] = 0
                        u["source"] = "ble"
                        units[unit] = u
                    except Exception:
                        pass    # keep the cloud view (stale beats nothing)
            _live_cache["payload"] = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "units": units,
            }
            _live_cache["ts"] = now
    return jsonify(_live_cache["payload"])


@app.get("/api/history")
def api_history():
    key = request.args.get("range", "24h")
    if key not in RANGES:
        return jsonify({"error": f"unknown range {key}"}), 400
    window, bucket = RANGES[key]
    now = time.time()

    with _hist_lock:
        cached = _hist_cache.get(key)
        if cached and now - cached["ts"] < HISTORY_TTL:
            return jsonify(cached["payload"])

        history.reload_if_header_changed(CSV_PATH)
        rows = history.load_rows(CSV_PATH)
        since = datetime.now().astimezone() - timedelta(seconds=window)
        days = max(1, round(window / 86400))

        payload = {
            "range": key,
            "bucket_s": bucket,
            "series": {u: history.bucket_series(rows, u, since, bucket, SERIES_METRICS)
                       for u in UNITS},
            "daily": {u: history.daily_energy(rows, u, days) for u in UNITS},
            "stats": {u: history.summarize(rows, u, since) for u in UNITS},
            "profile": {u: history.hourly_profile(rows, u, since) for u in UNITS},
            # history.coverage(), not len(rows): the row cache prunes to
            # RETAIN_DAYS but "logging since / N samples" describes the log.
            "coverage": history.coverage(),
            # 7-day average daily battery drain, range-independent like
            # coverage; the Overview divides stored Wh by it for "Backup".
            "drain": history.avg_daily_drain(rows, DRAIN_WH_FULL),
        }
        _hist_cache[key] = {"ts": now, "payload": payload}
    return jsonify(payload)


def _control_via_ble(sn, key, value):
    """Send over the local radio, then verify against the BLE readback."""
    ble.set_control(sn, key, value)
    applied, readback = False, None
    deadline = time.time() + VERIFY_TIMEOUT
    while not applied and time.time() < deadline:
        time.sleep(VERIFY_POLL)
        try:
            q = ble.get_quota_all(sn)
        except BleError:
            continue
        readback = q.get(controls.CONTROLS[key]["readback"])
        applied = controls.matches(key, value, q)
    with _lock:
        _live_cache["ts"] = 0.0
    return jsonify({"applied": applied, "readback": readback, "via": "ble"})


@app.get("/api/controls")
def api_controls():
    return jsonify(controls.public_registry())


@app.post("/api/control")
def api_control():
    body = request.get_json(silent=True) or {}
    unit, key = body.get("unit"), body.get("key")
    sn = UNITS.get(unit)
    if not sn:
        return jsonify({"error": f"unknown unit {unit!r}"}), 400
    try:
        value = controls.validate(key, body.get("value"))
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    ctl = controls.CONTROLS[key]
    with _ctl_lock:
        try:
            # Fresh quota first: id 66's companion field must reflect the
            # hardware's current state, not a possibly 10 s-old cache.
            quota = client.get_quota_all(sn)
            params = controls.build_params(key, value, quota)
            client.set_quota(sn, ctl["cmd_id"], **params)
        except EcoFlowApiError as e:
            msg = str(e)
            # EcoFlow answers 1008 "check your params" for writes to an
            # offline unit — check liveness so the UI can say what's wrong,
            # and hand the command to the local radio when we have one.
            if "1008" in msg:
                try:
                    dev = next((d for d in client.get_device_list()
                                if d.get("sn") == sn), None)
                    offline = dev is not None and not dev.get("online")
                except Exception:
                    offline = False
                if offline:
                    msg = (f"unit {unit} is offline to the EcoFlow cloud — "
                           "commands bounce until it reconnects")
                    if ble is not None:
                        try:
                            return _control_via_ble(sn, key, value)
                        except BleUnsupported:
                            msg += "; no BLE command exists for this control"
                        except BleError as be:
                            msg += f"; BLE fallback failed: {be}"
            return jsonify({"error": msg}), 502
        except Exception as e:
            return jsonify({"error": f"request failed: {e}"}), 502

        # code 0 means queued, not applied — poll until the readback agrees.
        applied = controls.matches(key, value, quota)
        deadline = time.time() + VERIFY_TIMEOUT
        while not applied and time.time() < deadline:
            time.sleep(VERIFY_POLL)
            try:
                quota = client.get_quota_all(sn)
            except Exception:
                continue
            applied = controls.matches(key, value, quota)

    with _lock:
        _live_cache["ts"] = 0.0   # next /api/live refetches real state
    return jsonify({
        "applied": applied,
        "readback": quota.get(ctl["readback"]),
        "sent": params,
    })


def _log_balancer_action(entry):
    """Append to logs/balancer.jsonl — the durable record of every command the
    balancer sent. In-memory status dies with the process (which is exactly how
    2026-08-05's rogue "off" nearly went undiagnosed)."""
    try:
        BAL_LOG_PATH.parent.mkdir(exist_ok=True)
        with BAL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass   # a full disk must not stop the balancer


def balancer_tick():
    """One auto-balancer pass: read B, toggle its AC output at the thresholds.

    The emergency layer (A running dry) is consulted first and may force the
    transfer on with B nowhere near full, or suppress the normal stop_soc
    cutoff mid-rescue. Never raises — errors land in the status dict and the
    next tick retries, which is also the recovery path for the offline-unit
    1008 flap.
    """
    cfg = _bal["cfg"]
    status = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_action": _bal["status"].get("last_action"),
    }
    if not cfg.enabled and not cfg.emergency_enabled:
        status["state"] = "disabled"
        _bal["status"] = status
        return
    sn = UNITS["B"]
    try:
        # Decision reads run without _ctl_lock: a slow cloud round-trip here
        # (30 s timeout, twice) must not stall Control-view commands.
        quota = client.get_quota_all(sn)
        if quota is None:   # offline flap serves data:null
            raise RuntimeError("B quota unavailable — cloud returned no data")
        soc = quota.get("ems.f32LcdShowSoc")
        soc = None if soc is None else float(soc)
        ac_raw = quota.get("inv.cfgAcEnabled")
        if ac_raw is None:
            raise RuntimeError("no inv.cfgAcEnabled in quota — not actuating")
        ac_on = int(float(ac_raw)) == 1
        a_soc = None
        try:
            # Read unconditionally: the A-full cutoff has no toggle, so A's
            # SOC is needed on every tick, not just when emergency is armed.
            a_raw = (client.get_quota_all(UNITS["A"]) or {}).get(
                "ems.f32LcdShowSoc")
            a_soc = None if a_raw is None else float(a_raw)
        except Exception as e:
            status["a_error"] = str(e)   # both A-side layers go inert
        # A full outranks everything: pushing into a full battery curtails A's
        # own solar and burns B through a double inversion to no benefit.
        a_full = balancer.a_full_action(a_soc, ac_on, cfg)
        emg = (balancer.emergency_action(a_soc, soc, ac_on, cfg)
               if cfg.emergency_enabled else None)
        if a_full == "off":
            action = "off"
            reason = (f"A full: {a_soc}% >= {cfg.a_full_soc}% — "
                      f"transfer resumes below {cfg.a_full_resume_soc}%")
        elif a_full == "block":
            action = None
        elif emg in ("on", "off"):
            action = emg
            reason = (
                f"EMERGENCY: A at {a_soc}% <= {cfg.emergency_a_soc}% — "
                f"draining B to {cfg.emergency_b_floor}%" if emg == "on" else
                f"emergency floor: B at {soc}% <= {cfg.emergency_b_floor}%")
        elif emg == "hold" or not cfg.enabled:
            action = None
        else:
            action = balancer.desired_action(soc, ac_on, cfg)
            reason = f"B at {soc}% (start {cfg.start_soc}%, stop {cfg.stop_soc}%)"
        if action:
            value = 1 if action == "on" else 0
            with _ctl_lock:
                # Re-read under the lock: id 66's companion field must
                # reflect the hardware's state after any just-finished
                # manual command, not the decision-time snapshot.
                quota = client.get_quota_all(sn) or quota
                params = controls.build_params("ac_out", value, quota)
                client.set_quota(sn, controls.CONTROLS["ac_out"]["cmd_id"], **params)
                applied = controls.matches("ac_out", value, quota)
                deadline = time.time() + VERIFY_TIMEOUT
                while not applied and time.time() < deadline:
                    time.sleep(VERIFY_POLL)
                    try:
                        quota = client.get_quota_all(sn)
                    except Exception:
                        continue
                    applied = controls.matches("ac_out", value, quota)
            if applied:
                ac_on = value == 1
            status["last_action"] = {
                "action": action, "applied": applied, "b_soc": soc,
                "a_soc": a_soc, "at": status["checked_at"],
                "reason": reason,
            }
            _log_balancer_action(status["last_action"])
            with _lock:
                _live_cache["ts"] = 0.0
        status["b_soc"] = soc
        status["a_soc"] = a_soc
        status["ac_on"] = ac_on
        emergency_active = (cfg.emergency_enabled and ac_on and a_soc is not None
                            and a_soc < cfg.emergency_a_clear_soc)
        # Re-derived from the post-action state so a just-cut transfer reads
        # "a-full" rather than a bare "waiting" the Control view can't explain.
        status["a_full"] = balancer.a_full_action(a_soc, ac_on, cfg) == "block"
        status["state"] = ("emergency" if emergency_active
                           else "transferring" if ac_on
                           else "a-full" if status["a_full"] else "waiting")
    except Exception as e:
        status["state"] = "error"
        status["error"] = str(e)
    _bal["status"] = status


def _balancer_loop():
    while True:
        balancer_tick()
        _bal_wake.wait(BAL_TICK_S)
        _bal_wake.clear()


@app.get("/api/balancer")
def api_balancer_get():
    return jsonify({"config": asdict(_bal["cfg"]), "status": _bal["status"],
                    "tick_s": BAL_TICK_S})


@app.post("/api/balancer")
def api_balancer_set():
    body = request.get_json(silent=True) or {}
    cur = _bal["cfg"]
    try:
        cfg = balancer.Config(
            enabled=bool(body.get("enabled", cur.enabled)),
            start_soc=int(body.get("start_soc", cur.start_soc)),
            stop_soc=int(body.get("stop_soc", cur.stop_soc)),
            emergency_enabled=bool(
                body.get("emergency_enabled", cur.emergency_enabled)),
            emergency_a_soc=int(
                body.get("emergency_a_soc", cur.emergency_a_soc)),
            emergency_a_clear_soc=int(
                body.get("emergency_a_clear_soc", cur.emergency_a_clear_soc)),
            emergency_b_floor=int(
                body.get("emergency_b_floor", cur.emergency_b_floor)),
            a_full_soc=int(body.get("a_full_soc", cur.a_full_soc)),
            a_full_resume_soc=int(
                body.get("a_full_resume_soc", cur.a_full_resume_soc)))
        cfg.validate()
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    _bal["cfg"] = cfg
    BAL_CFG_PATH.parent.mkdir(exist_ok=True)
    cfg.save(BAL_CFG_PATH)
    _bal_wake.set()
    return jsonify({"config": asdict(cfg)})


@app.get("/")
def index():
    return send_file(ROOT / "web" / "index.html")


@app.get("/truck.png")
def truck():
    return send_file(ROOT / "web" / "truck.png")


@app.get("/assets/<path:name>")
def assets(name: str):
    """Fonts and other local assets. Self-hosted because the truck is off-grid — a
    CDN link would leave the console typeset in system fallbacks with no internet."""
    return send_from_directory(ROOT / "web" / "assets", name, max_age=31536000)


if __name__ == "__main__":
    # Auto-reload on edit. Without this the scheduled task's PT5M keepalive happily
    # serves a stale module for days — it restarts the process only if it *dies*,
    # and an edited file doesn't kill anything (cost us a full debugging session
    # on 2026-08-02). Deliberately not debug=True: that would also expose the
    # interactive Werkzeug debugger, which is remote code execution on the port.
    #
    # The balancer thread runs only in the reloader CHILD (the process that
    # actually serves) — the watcher parent re-executes this file too, and two
    # balancers would double-send every command.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=_balancer_loop, daemon=True,
                         name="balancer").start()
    else:
        # Single-instance guard, held by the reloader parent (it survives
        # child restarts). Without it a second launch runs a rival balancer
        # with its own stale in-memory config — on 2026-08-05 exactly that
        # killed a B->A transfer at 57% (old stop threshold said 60).
        _instance_lock = single_instance.acquire(INSTANCE_LOCK_PORT)
        if _instance_lock is None:
            raise SystemExit(
                "another dashboard instance is already running — refusing to "
                "start a second balancer (kill it or use the running one)")
    app.run(host="127.0.0.1", port=8642, use_reloader=True)
