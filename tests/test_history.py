"""Tests for history aggregation (bucketing, energy deltas, stats)."""

from datetime import datetime, timedelta, timezone

import pytest

from ecoflow.history import (avg_daily_drain, bucket_series, coverage,
                             daily_energy, hourly_profile, load_rows, summarize)
from ecoflow.poller import HEADER


def row(dt, unit="A", **cols):
    base = dict.fromkeys(
        (c for c in HEADER if c not in ("timestamp", "unit")), None)
    base.update(cols)
    return {"timestamp": dt.isoformat(), "unit": unit, "dt": dt, **base}


@pytest.fixture
def t0():
    return datetime(2026, 7, 25, 12, 0).astimezone()


def test_bucket_series_averages_within_bucket(t0):
    rows = [
        row(t0, solar_w=100, soc=50),
        row(t0 + timedelta(minutes=2), solar_w=200, soc=52),
        row(t0 + timedelta(minutes=7), solar_w=300, soc=55),
    ]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300, ["solar_w", "soc"])
    assert out["solar_w"] == [150, 300]   # mean of first bucket, then second
    assert out["soc"] == [52, 55]         # SOC takes the last value, not the mean


def test_bucket_series_ignores_other_units(t0):
    rows = [row(t0, unit="A", solar_w=100), row(t0, unit="B", solar_w=900)]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300, ["solar_w"])
    assert out["solar_w"] == [100]


def test_daily_energy_sums_counter_deltas(t0):
    day = t0.replace(hour=8)
    rows = [
        row(day, cum_solar_wh=1000),
        row(day + timedelta(hours=1), cum_solar_wh=1300),
        row(day + timedelta(hours=2), cum_solar_wh=1500),
    ]
    out = daily_energy(rows, "A", days=7, now=t0)
    assert len(out) == 1
    assert out[0]["solar_wh"] == 500  # 1500 - 1000, not the raw counter value


def test_daily_energy_splits_across_local_days(t0):
    """Each delta is charged to the day of the later sample, so a night-time
    window doesn't leak into the previous day's harvest."""
    d1 = t0.replace(hour=22, minute=0)
    rows = [
        row(d1, cum_solar_wh=1000),                        # baseline, no delta yet
        row(d1 + timedelta(hours=1), cum_solar_wh=1050),   # +50 -> day 1
        row(d1 + timedelta(hours=3), cum_solar_wh=1100),   # +50 -> day 2
        row(d1 + timedelta(hours=4), cum_solar_wh=1250),   # +150 -> day 2
    ]
    out = daily_energy(rows, "A", days=7, now=t0 + timedelta(days=1))
    assert [(d["date"], d["solar_wh"]) for d in out] == [
        (d1.date().isoformat(), 50),
        ((d1 + timedelta(days=1)).date().isoformat(), 200),
    ]


def test_daily_energy_drops_counter_reset(t0):
    """A firmware reset zeroes the counter; that must not read as a huge day."""
    day = t0.replace(hour=8)
    rows = [
        row(day, cum_solar_wh=5000),
        row(day + timedelta(hours=1), cum_solar_wh=10),   # reset
        row(day + timedelta(hours=2), cum_solar_wh=110),
    ]
    out = daily_energy(rows, "A", days=7, now=t0)
    assert out[0]["solar_wh"] == 100  # only the post-reset climb


def test_daily_energy_ignores_rows_missing_counters(t0):
    """Pre-migration rows have blank cum_* columns."""
    day = t0.replace(hour=8)
    rows = [
        row(day, cum_solar_wh=None),
        row(day + timedelta(hours=1), cum_solar_wh=1000),
        row(day + timedelta(hours=2), cum_solar_wh=1400),
    ]
    assert daily_energy(rows, "A", days=7, now=t0)[0]["solar_wh"] == 400


def test_summarize_duty_cycle(t0):
    rows = [row(t0 + timedelta(minutes=i), ac_out_w=w, soc=50)
            for i, w in enumerate([0, 0, 800, 800])]
    stats = summarize(rows, "A", t0 - timedelta(hours=1))
    assert stats["ac_duty_pct"] == 50.0
    assert stats["ac_avg_when_on"] == 800


def test_summarize_returns_none_without_data(t0):
    assert summarize([], "A", t0) is None


def test_hourly_profile_covers_all_24_hours(t0):
    rows = [row(t0.replace(hour=13), solar_w=400, watts_out=100)]
    prof = hourly_profile(rows, "A", t0 - timedelta(days=1))
    assert len(prof) == 24
    assert prof[13]["solar_w"] == 400
    assert prof[0]["solar_w"] == 0  # hours with no samples read zero, not None


def test_hourly_profile_load_is_external_output_only(t0):
    # watts_out includes the internal main->Extra pack transfer while charging;
    # load must come from what leaves the box (AC + DC), with watts_out as the
    # fallback for rows logged before ac_out_w existed.
    rows = [
        row(t0.replace(hour=9), watts_out=800, ac_out_w=270, dc_out_w=30),
        row(t0.replace(hour=10), watts_out=100),  # pre-ac_out_w row
    ]
    prof = hourly_profile(rows, "A", t0 - timedelta(days=1))
    assert prof[9]["load_w"] == 300    # 270 AC + 30 DC, not 800
    assert prof[9]["ac_w"] == 270      # reported so B's AC can be read as transfer
    assert prof[10]["load_w"] == 100   # fallback


WH_FULL = {"A": 7200, "B": 3600}


def test_avg_daily_drain_energy_balance(t0):
    """Drain = solar in + stored drop, spread over the actual elapsed span."""
    rows = [
        row(t0, unit="A", soc=80, cum_solar_wh=1000),
        row(t0, unit="B", soc=100, cum_solar_wh=2000),
        row(t0 + timedelta(days=3), unit="A", soc=50, cum_solar_wh=4000),
        row(t0 + timedelta(days=3), unit="B", soc=100, cum_solar_wh=2000),
    ]
    out = avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3))
    # 3000 Wh solar + 30% of A's 7200 Wh = 5160 Wh over 3 days
    assert out["wh_per_day"] == 1720
    assert out["window_days"] == 3.0


def test_avg_daily_drain_transfer_cancels_between_units(t0):
    """B->A transfer is internal: B's drop and A's rise cancel, leaving only
    the conversion loss in the drain."""
    rows = [
        row(t0, unit="A", soc=50, cum_solar_wh=0),
        row(t0, unit="B", soc=100, cum_solar_wh=0),
        # B sent 20% of 3600 = 720 Wh; A gained only 600 Wh (losses) and
        # A's ac_in counter saw the transfer arrive — it must not read as shore.
        row(t0 + timedelta(days=3), unit="A", soc=50 + 600 / 72,
            cum_solar_wh=0, cum_ac_in_wh=720),
        row(t0 + timedelta(days=3), unit="B", soc=80, cum_solar_wh=0,
            cum_ac_out_wh=720),
    ]
    # start rows need counter baselines for the ac deltas to exist
    rows[0]["cum_ac_in_wh"] = 0.0
    rows[1]["cum_ac_out_wh"] = 0.0
    out = avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3))
    assert out["wh_per_day"] == pytest.approx(120 / 3)  # only the 120 Wh loss


def test_avg_daily_drain_counts_shore_beyond_transfer(t0):
    """A's ac_in minus B's ac_out is shore power; B's own ac_in is all shore."""
    rows = [
        row(t0, unit="A", soc=50, cum_solar_wh=0, cum_ac_in_wh=0),
        row(t0, unit="B", soc=50, cum_solar_wh=0, cum_ac_out_wh=0, cum_ac_in_wh=0),
        row(t0 + timedelta(days=3), unit="A", soc=40,
            cum_solar_wh=0, cum_ac_in_wh=1200),
        row(t0 + timedelta(days=3), unit="B", soc=50,
            cum_solar_wh=0, cum_ac_out_wh=1000, cum_ac_in_wh=300),
    ]
    out = avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3))
    # shore = (1200 - 1000) + 300 = 500; stored drop = 10% of 7200 = 720
    assert out["wh_per_day"] == pytest.approx((500 + 720) / 3)


def test_avg_daily_drain_drops_counter_reset(t0):
    rows = [
        row(t0, unit="A", soc=50, cum_solar_wh=5000),
        row(t0, unit="B", soc=50, cum_solar_wh=0),
        row(t0 + timedelta(days=2, hours=1), unit="A", soc=50, cum_solar_wh=10),
        row(t0 + timedelta(days=2, hours=1), unit="B", soc=50, cum_solar_wh=0),
        row(t0 + timedelta(days=3), unit="A", soc=35, cum_solar_wh=110),
        row(t0 + timedelta(days=3), unit="B", soc=50, cum_solar_wh=0),
    ]
    out = avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3))
    # only the post-reset 100 Wh counts, plus 15% of 7200 stored drop
    assert out["wh_per_day"] == pytest.approx((100 + 1080) / 3)


def test_avg_daily_drain_requires_48h_of_data(t0):
    rows = [
        row(t0, unit="A", soc=80, cum_solar_wh=0),
        row(t0, unit="B", soc=80, cum_solar_wh=0),
        row(t0 + timedelta(hours=30), unit="A", soc=50, cum_solar_wh=0),
        row(t0 + timedelta(hours=30), unit="B", soc=50, cum_solar_wh=0),
    ]
    assert avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(hours=30)) is None


def test_avg_daily_drain_requires_both_units(t0):
    rows = [
        row(t0, unit="A", soc=80, cum_solar_wh=0),
        row(t0 + timedelta(days=3), unit="A", soc=50, cum_solar_wh=0),
    ]
    assert avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3)) is None


def test_avg_daily_drain_rejects_nonpositive_drain(t0):
    """SOC rising with no measured inflow is sensor noise, not negative usage."""
    rows = [
        row(t0, unit="A", soc=50, cum_solar_wh=0),
        row(t0, unit="B", soc=50, cum_solar_wh=0),
        row(t0 + timedelta(days=3), unit="A", soc=55, cum_solar_wh=0),
        row(t0 + timedelta(days=3), unit="B", soc=50, cum_solar_wh=0),
    ]
    assert avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3)) is None


def test_avg_daily_drain_ignores_rows_outside_window(t0):
    """Only the trailing 7 days count, whatever else the cache holds."""
    rows = [
        row(t0 - timedelta(days=20), unit="A", soc=100, cum_solar_wh=0),
        row(t0 - timedelta(days=20), unit="B", soc=100, cum_solar_wh=0),
        row(t0, unit="A", soc=60, cum_solar_wh=1000),
        row(t0, unit="B", soc=60, cum_solar_wh=1000),
        row(t0 + timedelta(days=3), unit="A", soc=50, cum_solar_wh=1500),
        row(t0 + timedelta(days=3), unit="B", soc=55, cum_solar_wh=1200),
    ]
    out = avg_daily_drain(rows, WH_FULL, now=t0 + timedelta(days=3))
    # window sees 700 Wh solar + (10% of 7200 + 5% of 3600) stored drop
    assert out["wh_per_day"] == pytest.approx((700 + 720 + 180) / 3)
    assert out["window_days"] == 3.0


def test_load_rows_reads_appended_tail_only(tmp_path):
    path = tmp_path / "s.csv"
    header = ",".join(HEADER)
    blank = "," * (len(HEADER) - 3)
    path.write_text(f"{header}\n2026-07-25T12:00:00+00:00,A,50{blank}\n")
    assert len(load_rows(path)) == 1
    with path.open("a") as f:
        f.write(f"2026-07-25T12:00:30+00:00,A,51{blank}\n")
    rows = load_rows(path)
    assert len(rows) == 2
    assert rows[1]["soc"] == 51


def test_load_rows_reparses_after_a_schema_migration(tmp_path):
    """poller.migrate() rewrites every row to add a column, so the file GROWS
    and byte offsets all shift. Seeking to the stale offset would re-read rows
    already cached and double-count them; a changed header must force a full
    reparse."""
    path = tmp_path / "s.csv"
    old_header = HEADER[:-4]                    # before the per-pack columns
    blank = "," * (len(old_header) - 3)
    rows_out = [f"2026-07-25T12:00:{s:02d}+00:00,A,50{blank}" for s in (0, 30)]
    path.write_text(",".join(old_header) + "\n" + "\n".join(rows_out) + "\n")
    assert len(load_rows(path)) == 2

    # migrate: same rows, wider header, 4 blank columns appended to each
    grown = "," * 4
    path.write_text(",".join(HEADER) + "\n"
                    + "\n".join(r + grown for r in rows_out) + "\n")
    assert path.stat().st_size > len(",".join(old_header))
    assert len(load_rows(path)) == 2


def test_load_rows_prunes_old_rows_but_coverage_stays_honest(tmp_path):
    """The in-memory cache caps at RETAIN_DAYS (the CSV keeps everything), yet
    the console's "logging since / N samples" must still describe the full log."""
    path = tmp_path / "s.csv"
    header = ",".join(HEADER)
    blank = "," * (len(HEADER) - 3)
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(f"{header}\n{old},A,40{blank}\n{new},A,50{blank}\n")
    rows = load_rows(path)
    assert [r["soc"] for r in rows] == [50]   # 60-day-old row pruned from memory
    cov = coverage()
    assert cov["samples"] == 2
    assert abs(datetime.fromisoformat(cov["first"])
               - datetime.fromisoformat(old)) < timedelta(seconds=1)
