"""Tests for quota sample extraction, CSV logging, and schema migration."""

import csv

from ecoflow.poller import FIELDS, HEADER, extract_sample, append_sample, migrate

QUOTA = {
    "ems.f32LcdShowSoc": 87.4,
    "mppt.inWatts": 6340,  # 0.1W units on the wire
    "pd.wattsInSum": 634,
    "pd.wattsOutSum": 912,
    "inv.inputWatts": 0,
    "mppt.chgState": 1,
    "inv.cfgAcEnabled": 1,
    "inv.outputWatts": 890,
    "bmsMaster.temp": 27,
    "pd.chgSunPower": 13540,
    "pd.chgPowerAc": 47421,
    "pd.dsgPowerAc": 37398,
    "pd.dsgPowerDc": 414,
    "mppt.carOutWatts": 120,  # 0.1W units -> 12W
    "pd.usb1Watts": 5,
    "pd.typec1Watts": 5,
    "unrelated.field": 42,
}


def test_extract_sample_scales_and_derives():
    sample = extract_sample(QUOTA)
    assert sample["soc"] == 87.4
    assert sample["solar_w"] == 634.0
    assert sample["ac_out_w"] == 890
    assert sample["dc_out_w"] == 22.0  # 12 car + 5 usb + 5 typec
    assert sample["cum_solar_wh"] == 13540


def test_extract_sample_rounds_unscaled_floats():
    """SOC arrives as a long float; keep the CSV readable."""
    assert extract_sample({"ems.f32LcdShowSoc": 25.227953})["soc"] == 25.2


def test_extract_sample_missing_fields_are_none():
    sample = extract_sample({})
    assert sample["soc"] is None
    assert sample["dc_out_w"] == 0  # summed ports default to zero, not None


def test_append_sample_writes_header_once(tmp_path):
    path = tmp_path / "samples.csv"
    append_sample(path, "2026-07-20T12:00:00", "B", extract_sample(QUOTA))
    append_sample(path, "2026-07-20T12:00:30", "B", extract_sample({}))
    rows = list(csv.reader(path.open()))
    assert rows[0] == HEADER
    assert rows[1][:3] == ["2026-07-20T12:00:00", "B", "87.4"]
    assert len(rows) == 3


def test_migrate_appends_new_columns_preserving_rows(tmp_path):
    path = tmp_path / "samples.csv"
    old_header = ["timestamp", "unit", "soc", "solar_w", "watts_in", "watts_out",
                  "ac_charge_w", "chg_state", "ac_out_enabled"]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(old_header)
        w.writerow(["2026-07-25T08:44:00", "A", "20", "259.0", "257", "111", "0", "1", "0"])
    migrate(path)
    rows = list(csv.reader(path.open()))
    assert rows[0] == HEADER
    row = dict(zip(HEADER, rows[1]))
    assert row["soc"] == "20" and row["solar_w"] == "259.0"
    assert row["cum_solar_wh"] == ""  # unknown for pre-migration rows


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "samples.csv"
    append_sample(path, "2026-07-25T08:44:00", "A", extract_sample(QUOTA))
    migrate(path)
    migrate(path)
    rows = list(csv.reader(path.open()))
    assert rows[0] == HEADER
    assert len(rows) == 2


def test_fields_start_with_original_v1_columns():
    """Migration relies on new columns being appended, never reordered."""
    assert FIELDS[:7] == ["soc", "solar_w", "watts_in", "watts_out",
                          "ac_charge_w", "chg_state", "ac_out_enabled"]


# --- per-pack columns (added 2026-08-15) ---------------------------------
# A's two packs run their own SOC gauges and those gauges drift apart; only
# pack voltage says whether the charge really diverged. See docs/api.md.

TWO_PACK_QUOTA = {
    "ems.bms0Online": 3, "ems.bms1Online": 3,
    "bmsMaster.f32ShowSoc": 39.015244, "bmsMaster.vol": 49292,
    "bmsSlave1.f32ShowSoc": 23.881538, "bmsSlave1.vol": 49304,
}


def test_extract_sample_logs_both_pack_gauges():
    sample = extract_sample(TWO_PACK_QUOTA)
    assert sample["pack_main_soc"] == 39.0
    assert sample["pack_extra_soc"] == 23.9


def test_extract_sample_keeps_pack_voltage_in_millivolts():
    """The main/extra gap is ~12 mV; volts-to-1dp would round it away."""
    sample = extract_sample(TWO_PACK_QUOTA)
    assert sample["pack_main_mv"] == 49292
    assert sample["pack_extra_mv"] == 49304


def test_extract_sample_ignores_phantom_extra_pack_on_a_bare_unit():
    """quota/all ships a full bmsSlave1 block even on B, which has no Extra
    Battery, and its stale defaults look plausible (docs/api.md). Only
    ems.bms1Online says whether the slot is real."""
    bare = dict(TWO_PACK_QUOTA, **{"ems.bms1Online": 0,
                                   "bmsSlave1.f32ShowSoc": 31.81,
                                   "bmsSlave1.vol": 49577})
    sample = extract_sample(bare)
    assert sample["pack_extra_soc"] is None
    assert sample["pack_extra_mv"] is None
    assert sample["pack_main_soc"] == 39.0  # master unaffected


def test_extract_sample_pack_columns_absent_without_online_flags():
    """Presence is never inferred from the pack fields themselves."""
    sample = extract_sample({"bmsMaster.f32ShowSoc": 39.0, "bmsMaster.vol": 49292})
    assert sample["pack_main_soc"] is None
    assert sample["pack_main_mv"] is None
