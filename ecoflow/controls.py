"""Registry of every settable Delta Pro command (cmdSet 32), plus validation.

Pure data + logic, no I/O — same discipline as decision.py. The dashboard's
control endpoint builds requests from this table; the frontend renders from
its public form. One row per *user-facing control*: command ids 66 and 39
each carry two logical controls, so 15 command ids -> 16 rows.

Hard-won details encoded here so callers can't trip on them:
- id 66 must send BOTH `enabled` and `xboost` — a lone `enabled` is rejected
  with code 1008 (confirmed live 2026-07-27). Declared as `companion`: the
  other field is filled from the latest quota snapshot at send time.
- quota/all may return numbers as strings; readback comparison coerces.
- `pd.beepState` doc says 1 = Quiet, and the setter's `enabled` maps straight
  onto it, so the row is labelled Quiet Mode (on = silent).
"""

# choices are (value, label); min/max are inclusive; all values are ints.
CONTROLS = {
    # -------- power --------
    "ac_out": dict(
        label="AC Output", cmd_id=66, param="enabled",
        readback="inv.cfgAcEnabled", kind="toggle", group="power", risky=True,
        companion=("xboost", "inv.cfgAcXboost", 0),
        help="Inverter master switch. On B this is the B→A transfer switch.",
    ),
    "xboost": dict(
        label="X-Boost", cmd_id=66, param="xboost",
        readback="inv.cfgAcXboost", kind="toggle", group="power",
        companion=("enabled", "inv.cfgAcEnabled", 0),
        help="Voltage-sag mode for resistive loads beyond rated watts.",
    ),
    "car_out": dict(
        label="12V Car Port", cmd_id=81, param="enabled",
        readback="mppt.carState", kind="toggle", group="power", risky=True,
    ),
    # -------- charging --------
    "max_chg_soc": dict(
        label="Charge Ceiling", cmd_id=49, param="maxChgSoc",
        readback="ems.maxChargeSoc", kind="slider", group="charging",
        min=50, max=100, step=1, unit="%", risky=True,
        help="EMS-level: on A this governs the whole bank, Extra Battery included.",
    ),
    "min_dsg_soc": dict(
        label="Discharge Floor", cmd_id=51, param="minDsgSoc",
        readback="ems.minDsgSoc", kind="slider", group="charging",
        min=0, max=30, step=1, unit="%", risky=True,
        help="Hardware backstop — the unit stops discharging below this.",
    ),
    "ac_chg_watts": dict(
        label="AC Charge Speed", cmd_id=69, param="slowChgPower",
        readback="inv.cfgSlowChgWatts", kind="slider", group="charging",
        min=200, max=1800, step=100, unit="W",
        help="Delta Pro range undocumented; app slider spans 200–1800 W. "
             "Out-of-range values show as 'did not take'.",
    ),
    "pv_chg_type": dict(
        label="PV Charge Source", cmd_id=82, param="chgType",
        readback="mppt.cfgChgType", kind="choice", group="charging",
        choices=[(0, "Auto"), (1, "Solar"), (2, "Adapter")],
    ),
    "bypass_auto": dict(
        label="Bypass Auto-Start", cmd_id=84, param="enabled",
        readback="inv.acPassbyAutoEn", kind="toggle", group="charging",
        help="Pass wall AC straight through to outputs while charging.",
    ),
    # -------- device --------
    "quiet": dict(
        label="Quiet Mode", cmd_id=38, param="enabled",
        readback="pd.beepState", kind="toggle", group="device",
        help="Semantics unverified: the cloud accepts this command but "
             "pd.beepState was never observed to change (2026-08-03). "
             "A 'did not take' result here may be this quirk — test by ear.",
    ),
    "lcd_brightness": dict(
        label="Screen Brightness", cmd_id=39, param="lcdBrightness",
        readback="pd.lcdBrightness", kind="slider", group="device",
        min=0, max=100, step=5, unit="%",
    ),
    "lcd_timeout": dict(
        label="Screen Timeout", cmd_id=39, param="lcdTime",
        readback="pd.lcdOffSec", kind="choice", group="device",
        choices=[(10, "10s"), (30, "30s"), (60, "1m"), (300, "5m"),
                 (1800, "30m"), (0, "Never")],
    ),
    "unit_standby": dict(
        label="Unit Standby", cmd_id=33, param="standByMode",
        readback="pd.standByMode", kind="choice", group="device",
        choices=[(30, "30m"), (60, "1h"), (120, "2h"), (360, "6h"),
                 (720, "12h"), (0, "Never")],
        help="Whole-unit auto-off timer when idle.",
    ),
    "ac_standby": dict(
        label="AC Standby", cmd_id=153, param="standByMins",
        readback="inv.cfgStandbyMin", kind="choice", group="device",
        choices=[(60, "1h"), (120, "2h"), (240, "4h"), (720, "12h"),
                 (1440, "24h"), (0, "Never")],
        help="Inverter auto-off timer when no AC load is drawing.",
    ),
    # -------- advanced --------
    "car_in_amps": dict(
        label="Car Input Current", cmd_id=71, param="currMa",
        readback="mppt.cfgDcChgCurrent", kind="choice", group="advanced",
        choices=[(4000, "4A"), (6000, "6A"), (8000, "8A")],
        help="Only matters when charging from a vehicle alternator.",
    ),
    "gen_auto_on": dict(
        label="Generator On Below", cmd_id=52, param="openOilSoc",
        readback="ems.minOpenOilEbSoc", kind="slider", group="advanced",
        min=0, max=100, step=1, unit="%",
        help="Smart-generator threshold — unused without an EcoFlow generator.",
    ),
    "gen_auto_off": dict(
        label="Generator Off Above", cmd_id=53, param="closeOilSoc",
        readback="ems.maxCloseOilEbSoc", kind="slider", group="advanced",
        min=0, max=100, step=1, unit="%",
        help="Smart-generator threshold — unused without an EcoFlow generator.",
    ),
}

GROUPS = [("power", "Power"), ("charging", "Charging"),
          ("device", "Device"), ("advanced", "Advanced")]


def validate(key, value):
    """Return the value as an int, or raise KeyError/ValueError."""
    if key not in CONTROLS:
        raise KeyError(f"unknown control {key!r}")
    c = CONTROLS[key]
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: value {value!r} is not an integer")
    if c["kind"] == "toggle":
        if v not in (0, 1):
            raise ValueError(f"{key}: toggle value must be 0 or 1, got {v}")
    elif c["kind"] == "slider":
        if not c["min"] <= v <= c["max"]:
            raise ValueError(
                f"{key}: {v} outside range {c['min']}–{c['max']}")
    elif c["kind"] == "choice":
        allowed = [cv for cv, _ in c["choices"]]
        if v not in allowed:
            raise ValueError(f"{key}: {v} not one of {allowed}")
    return v


def build_params(key, value, quota=None):
    """Params dict for set_quota, filling any companion field from quota."""
    c = CONTROLS[key]
    params = {c["param"]: value}
    if "companion" in c:
        pname, source, default = c["companion"]
        cur = (quota or {}).get(source)
        try:
            cur = int(float(cur))
        except (TypeError, ValueError):
            cur = default
        params[pname] = cur
    return params


def matches(key, value, quota):
    """True when the readback field reflects the value (string-tolerant)."""
    rb = quota.get(CONTROLS[key]["readback"])
    try:
        return int(float(rb)) == int(value)
    except (TypeError, ValueError):
        return False


def current_values(quota):
    """{control key: raw readback value} for a quota snapshot."""
    return {k: quota.get(c["readback"]) for k, c in CONTROLS.items()}


def public_registry():
    """JSON-safe registry for the frontend, in display order."""
    out = []
    for key, c in CONTROLS.items():
        entry = {
            "key": key, "label": c["label"], "kind": c["kind"],
            "group": c["group"], "risky": c.get("risky", False),
        }
        for f in ("min", "max", "step", "unit", "help"):
            if f in c:
                entry[f] = c[f]
        if "choices" in c:
            entry["choices"] = [[v, lbl] for v, lbl in c["choices"]]
        out.append(entry)
    return out
