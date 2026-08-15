"""Read-only quota poller: extract key fields and append to CSV.

Two classes of column:
  * instantaneous watts/SOC — drive the power and SOC curves
  * `cum_*` lifetime Wh counters — confirmed live 2026-07-25 to advance in real
    time, so differencing two samples gives exact energy for that window. Use
    these (not integrated watts) for daily/weekly energy totals: they survive
    sampling gaps and poller restarts.
"""

import csv
from pathlib import Path

# quota key -> (csv column, divisor to reach base units)
QUOTA_MAP = {
    "ems.f32LcdShowSoc": ("soc", 1),        # system SOC; bmsMaster.soc is master-pack only
    "mppt.inWatts": ("solar_w", 10),        # 0.1W units
    "pd.wattsInSum": ("watts_in", 1),
    "pd.wattsOutSum": ("watts_out", 1),
    "inv.inputWatts": ("ac_charge_w", 1),
    "mppt.chgState": ("chg_state", 1),
    "inv.cfgAcEnabled": ("ac_out_enabled", 1),
    "inv.outputWatts": ("ac_out_w", 1),
    "bmsMaster.temp": ("batt_temp", 1),
    "pd.chgSunPower": ("cum_solar_wh", 1),
    "pd.chgPowerAc": ("cum_ac_in_wh", 1),
    "pd.chgPowerDc": ("cum_dc_in_wh", 1),
    "pd.dsgPowerAc": ("cum_ac_out_wh", 1),
    "pd.dsgPowerDc": ("cum_dc_out_wh", 1),
}
# A's two packs each run their own coulomb-counted SOC gauge, and those gauges
# drift apart — 4.8 points on 2026-07-27, 15.2 on 2026-08-15 — while the packs
# themselves stay bonded to one bus at the same voltage. Logging both gauges
# plus both pack voltages is what separates "the gauges disagree" from "the
# charge really diverged"; `soc` alone (their capacity-weighted mean) hides it.
# Voltage stays in raw mV: the honest gap is ~12 mV, which volts-to-1dp erases.
PACK_KEYS = {
    # ems.bms{slot}Online -> (quota prefix, soc column, millivolt column)
    0: ("bmsMaster", "pack_main_soc", "pack_main_mv"),
    1: ("bmsSlave1", "pack_extra_soc", "pack_extra_mv"),
}

# summed into the derived dc_out_w column
DC_OUT_KEYS = {
    "mppt.carOutWatts": 10,
    "pd.usb1Watts": 1, "pd.usb2Watts": 1,
    "pd.qcUsb1Watts": 1, "pd.qcUsb2Watts": 1,
    "pd.typec1Watts": 1, "pd.typec2Watts": 1,
}

# explicit order: original v1 columns first so migration is a pure column append
FIELDS = [
    "soc", "solar_w", "watts_in", "watts_out", "ac_charge_w", "chg_state",
    "ac_out_enabled", "ac_out_w", "dc_out_w", "batt_temp",
    "cum_solar_wh", "cum_ac_in_wh", "cum_dc_in_wh", "cum_ac_out_wh", "cum_dc_out_wh",
    "pack_main_soc", "pack_extra_soc", "pack_main_mv", "pack_extra_mv",
]
HEADER = ["timestamp", "unit"] + FIELDS


def extract_sample(quota):
    sample = {}
    for key, (col, scale) in QUOTA_MAP.items():
        v = quota.get(key)
        if v is not None:
            v = v / scale if scale != 1 else v
            v = round(v, 1) if isinstance(v, float) else v
        sample[col] = v
    sample["dc_out_w"] = round(
        sum((quota.get(k) or 0) / scale for k, scale in DC_OUT_KEYS.items()), 1
    )
    # `ems.bms{slot}Online` is the only authoritative presence flag: the
    # bmsSlave1 block ships on B too, carrying stale defaults that pass a null
    # check (docs/api.md). Blank beats a plausible-looking phantom.
    for slot, (prefix, soc_col, mv_col) in PACK_KEYS.items():
        online = quota.get(f"ems.bms{slot}Online")
        soc = quota.get(f"{prefix}.f32ShowSoc") if online else None
        sample[soc_col] = round(soc, 1) if soc is not None else None
        sample[mv_col] = quota.get(f"{prefix}.vol") if online else None
    return sample


def migrate(path):
    """Append any newly-added columns to an older CSV, preserving its rows."""
    path = Path(path)
    if not path.exists():
        return
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == HEADER:
        return
    old_header, old_rows = rows[0], rows[1:]
    idx = {name: i for i, name in enumerate(old_header)}
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for row in old_rows:
            writer.writerow([row[idx[c]] if c in idx and idx[c] < len(row) else ""
                             for c in HEADER])


def append_sample(path, timestamp, unit, sample):
    path = Path(path)
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(HEADER)
        writer.writerow([timestamp, unit] + [sample[col] for col in FIELDS])
