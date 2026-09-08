"""Load and aggregate logged samples for the dashboard's history views.

The CSV is append-only, so it is cached in memory and only the newly-appended
tail is parsed on each call. Energy totals come from differencing the `cum_*`
lifetime Wh counters rather than integrating watts — that stays correct across
sampling gaps and poller restarts.

Day boundaries use *local* time: a solar day should line up with Mike's day.
"""

import bisect
import csv
import threading
from datetime import datetime, timedelta
from pathlib import Path

NUMERIC = {
    "soc", "solar_w", "watts_in", "watts_out", "ac_charge_w", "chg_state",
    "ac_out_enabled", "ac_out_w", "dc_out_w", "batt_temp",
    "cum_solar_wh", "cum_ac_in_wh", "cum_dc_in_wh", "cum_ac_out_wh", "cum_dc_out_wh",
    "stale",
}
# cumulative counter -> per-period energy name
ENERGY = {
    "cum_solar_wh": "solar_wh",
    "cum_ac_in_wh": "ac_in_wh",
    "cum_dc_in_wh": "dc_in_wh",
    "cum_ac_out_wh": "ac_out_wh",
    "cum_dc_out_wh": "dc_out_wh",
}
AC_ON_W = 50  # inverter output above this counts as "the A/C is running"
# bucket metric -> the lifetime counter(s) whose delta, divided by elapsed
# hours, stands in for it across a span the cloud spent replaying a frozen
# snapshot. The counters live on the unit and keep integrating through the
# outage, so the energy is exact even though its timing is not.
BRIDGE = {
    "solar_w": ("cum_solar_wh",),
    "ac_out_w": ("cum_ac_out_wh",),
    "dc_out_w": ("cum_dc_out_wh",),
    "ac_charge_w": ("cum_ac_in_wh",),
    "watts_out": ("cum_ac_out_wh", "cum_dc_out_wh"),
}
# In-memory cap: the longest dashboard range is 30d, and the row cache costs
# ~1 KB/row (~65 MB after ten days of 30 s samples) in a process that runs for
# weeks. The CSV keeps everything; a full reparse restores any pruned span.
RETAIN_DAYS = 45

_cache = {"path": None, "offset": 0, "rows": [], "header": None,
          "first": None, "total": 0, "last": {}}
# columns that identify or annotate a row rather than describe the reading
_NOT_A_READING = {"timestamp", "unit", "dt", "stale", "frozen_since"}
# An unchanged reading only counts as frozen once it has sat still this long.
# A live idle unit can repeat its 19 logged columns for a few minutes (SOC
# ticks 0.1 % every ~6 min at 36 W, pack mV sits flat on LFP), and a snapshot
# under five minutes old is near enough the truth. Same threshold the
# Overview's "data frozen" badge uses.
STALE_AFTER_S = 300
# One lock for every _cache mutation: /api/history and /api/forecast each call
# load_rows under their OWN endpoint lock, so without this two threads can
# full-reparse concurrently and interleave the whole file into the cache twice.
# Out-of-order rows turn daily_energy's counter differencing into a sawtooth of
# phantom energy (the 84.6 kWh "Today" incident, 2026-08-18).
_lock = threading.Lock()


def _parse(header, values):
    row = {}
    for key, raw in zip(header, values):
        if key in NUMERIC:
            try:
                row[key] = float(raw)
            except (TypeError, ValueError):
                row[key] = None
        else:
            row[key] = raw
    try:
        row["dt"] = datetime.fromisoformat(row["timestamp"]).astimezone()
    except (TypeError, ValueError, KeyError):
        return None
    return row


def _same_reading(a, b):
    return all(a.get(k) == b.get(k) for k in a if k not in _NOT_A_READING)


def _read_header(path):
    with path.open("r", newline="") as f:
        return next(csv.reader(f), None)


def load_rows(path):
    """Rows sorted by time, parsing only bytes appended since the last call.

    Returns a snapshot copy: the cached list keeps being appended to and
    pruned by later calls, and callers aggregate outside the lock.
    """
    path = Path(path)
    if not path.exists():
        return []
    with _lock:
        size = path.stat().st_size
        # A shrunken file means rotation. A changed header means poller.migrate()
        # rewrote every row to append a column, which GROWS the file — so the
        # size check alone misses it, and seeking to the stale offset lands before
        # the last row read and re-appends rows already cached. Both mean reload.
        if (_cache["path"] != str(path) or size < _cache["offset"]
                or (_cache["header"] and _read_header(path) != _cache["header"])):
            _cache.update(path=str(path), offset=0, rows=[], header=None,
                          first=None, total=0, last={})
        if size == _cache["offset"]:
            return list(_cache["rows"])

        with path.open("r", newline="") as f:
            if _cache["offset"] == 0:
                reader = csv.reader(f)
                try:
                    _cache["header"] = next(reader)
                except StopIteration:
                    return []
                _cache["rows"] = []
            else:
                f.seek(_cache["offset"])
                reader = csv.reader(f)
            header = _cache["header"]
            for values in reader:
                if len(values) < 2 or values[0] == "timestamp":
                    continue
                row = _parse(header, values)
                if row:
                    # The poller's flag says "payload identical to last tick";
                    # rows logged before it existed get the same verdict from
                    # the 19 logged columns. Either way a reading is stale only
                    # after it has sat unchanged for STALE_AFTER_S — the cloud
                    # replaying a frozen snapshot (docs/api.md), not an idle unit.
                    prev = _cache["last"].get(row["unit"])
                    same = (row["stale"] if row.get("stale") is not None
                            else prev is not None and _same_reading(row, prev))
                    row["frozen_since"] = (prev.get("frozen_since") or prev["dt"]) \
                        if same and prev is not None else None
                    row["stale"] = int(row["frozen_since"] is not None and
                        (row["dt"] - row["frozen_since"]).total_seconds() >= STALE_AFTER_S)
                    _cache["last"][row["unit"]] = row
                    _cache["rows"].append(row)
                    _cache["total"] += 1
                    if _cache["first"] is None:
                        _cache["first"] = row["dt"]
            _cache["offset"] = f.tell()
        cutoff = datetime.now().astimezone() - timedelta(days=RETAIN_DAYS)
        rows = _cache["rows"]
        keep = 0
        while keep < len(rows) and rows[keep]["dt"] < cutoff:
            keep += 1
        if keep:
            del rows[:keep]
        return list(_cache["rows"])


def coverage():
    """Logging-since + total-sample count, stable across the in-memory prune."""
    first = _cache["first"]
    return {"first": first.isoformat(timespec="seconds") if first else None,
            "samples": _cache["total"]}


def reload_if_header_changed(path):
    """Force a full reparse when the header no longer matches the cache."""
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", newline="") as f:
        header = next(csv.reader(f), None)
    with _lock:
        if header and _cache["header"] and header != _cache["header"]:
            _cache.update(offset=0, rows=[], header=None, first=None, total=0,
                          last={})


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _fresh(row):
    """A row the unit actually reported, not a replayed frozen snapshot."""
    return not row.get("stale")


def bucket_series(rows, unit, since, bucket_s, metrics):
    """Downsample to fixed-width buckets: mean of each metric, last SOC.

    Stale rows never feed a mean. A bucket with no fresh row is still emitted,
    with None everywhere, so the chart shows a gap instead of a straight line
    across the outage; `_bridge` then fills its power metrics from the
    counters where it can and marks those buckets in `bridged`.
    """
    unit_rows = [r for r in rows if r["unit"] == unit]
    buckets = {}
    for row in unit_rows:
        if row["dt"] < since:
            continue
        buckets.setdefault(int(row["dt"].timestamp() // bucket_s), []).append(row)
    keys = sorted(buckets)
    out = {"t": [], "bridged": [], **{m: [] for m in metrics}}
    empty = []
    for key in keys:
        group = [g for g in buckets[key] if _fresh(g)]
        out["t"].append(int(key * bucket_s * 1000))  # ms for JS
        out["bridged"].append(False)
        empty.append(not group)
        for m in metrics:
            out[m].append(None if not group
                          else group[-1][m] if m == "soc" else _mean([g[m] for g in group]))
    _bridge(out, keys, empty, bucket_s, unit_rows, metrics)
    return out


def _bridge(out, keys, empty, bucket_s, unit_rows, metrics):
    """Fill runs of empty buckets with counter rates from the bounding fresh rows.

    SOC has no counter and stays None. An open-ended run (the unit is frozen
    right now) and a counter that went backwards (reset) are left alone.
    """
    fresh = [r for r in unit_rows if _fresh(r)]
    times = [r["dt"] for r in fresh]
    i = 0
    while i < len(keys):
        if not empty[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(keys) and empty[j + 1]:
            j += 1
        start = datetime.fromtimestamp(keys[i] * bucket_s).astimezone()
        end = datetime.fromtimestamp((keys[j] + 1) * bucket_s).astimezone()
        b = bisect.bisect_left(times, start) - 1
        a = bisect.bisect_left(times, end)
        if b >= 0 and a < len(fresh):
            before, after = fresh[b], fresh[a]
            hours = (after["dt"] - before["dt"]).total_seconds() / 3600
            for m in metrics:
                if m not in BRIDGE or hours <= 0:
                    continue
                deltas = [(after.get(c), before.get(c)) for c in BRIDGE[m]]
                if any(x is None or y is None or x < y for x, y in deltas):
                    continue
                rate = round(sum(x - y for x, y in deltas) / hours, 1)
                for k in range(i, j + 1):
                    out[m][k] = rate
                    out["bridged"][k] = True
        i = j + 1


def daily_energy(rows, unit, days, now=None):
    """Per-local-day Wh, accumulated from cumulative-counter deltas.

    Deltas are attributed to the day of the *later* sample. A negative delta
    means the counter reset (firmware update / factory reset), so it is dropped
    rather than charged to the day as a huge spike.
    """
    now = now or datetime.now().astimezone()
    start_day = (now - timedelta(days=days - 1)).date()
    totals = {}
    prev = {}
    for row in rows:
        if row["unit"] != unit:
            continue
        day = row["dt"].date()
        for col, name in ENERGY.items():
            value = row[col]
            if value is None:
                continue
            last = prev.get(col)
            prev[col] = value
            if last is None or value < last or day < start_day:
                continue
            totals.setdefault(day, dict.fromkeys(ENERGY.values(), 0.0))
            totals[day][name] += value - last
    return [
        {"date": day.isoformat(), **{k: round(v) for k, v in vals.items()}}
        for day, vals in sorted(totals.items())
    ]


DRAIN_WINDOW_DAYS = 7
DRAIN_MIN_SPAN_S = 48 * 3600


def _counter_delta(rows, col):
    """Sum of positive deltas — a negative step is a counter reset, dropped."""
    total, last = 0.0, None
    for row in rows:
        value = row[col]
        if value is None:
            continue
        if last is not None and value >= last:
            total += value - last
        last = value
    return total


def avg_daily_drain(rows, wh_full, now=None):
    """System-level average daily battery drain (Wh/day) by energy balance.

    What the batteries spent = external inflow (solar + shore) plus the drop
    in stored charge over the window, so every loss — inverter overhead, the
    B->A double inversion — is included without an assumed efficiency factor.
    Counter deltas and SOC endpoints both survive poller gaps, and the average
    divides by the *actual* sampled span, not the nominal window.

    Rig-specific: assumes B's AC output feeds A's input, so A's ac-in counter
    includes the internal transfer — B's ac-out is subtracted when estimating
    shore power. `wh_full` maps unit -> full capacity Wh and must cover A and B.

    Returns None when either unit has under 48 h of samples in the window, or
    when the balance comes out non-positive (sensor noise, not negative usage).
    """
    now = now or datetime.now().astimezone()
    since = now - timedelta(days=DRAIN_WINDOW_DAYS)
    scoped = {u: [r for r in rows if r["unit"] == u and r["dt"] >= since]
              for u in wh_full}
    stored_start = stored_end = 0.0
    first_dt, last_dt = None, None
    for unit, cap in wh_full.items():
        socs = [r for r in scoped[unit] if r["soc"] is not None and _fresh(r)]
        if len(socs) < 2 or (socs[-1]["dt"] - socs[0]["dt"]).total_seconds() < DRAIN_MIN_SPAN_S:
            return None
        stored_start += socs[0]["soc"] / 100 * cap
        stored_end += socs[-1]["soc"] / 100 * cap
        first_dt = min(first_dt or socs[0]["dt"], socs[0]["dt"])
        last_dt = max(last_dt or socs[-1]["dt"], socs[-1]["dt"])
    solar = sum(_counter_delta(scoped[u], "cum_solar_wh") for u in wh_full)
    shore = max(0.0, _counter_delta(scoped["A"], "cum_ac_in_wh")
                - _counter_delta(scoped["B"], "cum_ac_out_wh"))
    shore += _counter_delta(scoped["B"], "cum_ac_in_wh")
    elapsed_days = (last_dt - first_dt).total_seconds() / 86400
    wh_per_day = (solar + shore + stored_start - stored_end) / elapsed_days
    if wh_per_day <= 0:
        return None
    return {"wh_per_day": wh_per_day,
            "window_days": round(elapsed_days, 1),
            "since": first_dt.isoformat(timespec="seconds")}


def summarize(rows, unit, since):
    """Headline stats for a range: duty cycle, peaks, SOC swing, coverage."""
    scoped = [r for r in rows if r["unit"] == unit and r["dt"] >= since]
    if not scoped:
        return None
    fresh = [r for r in scoped if _fresh(r)]   # `samples` still describes the log
    # ac_out_w was added to the log later than the other columns, so the duty
    # cycle covers a shorter window than the range — report that window too.
    ac_rows = [r for r in fresh if r["ac_out_w"] is not None]
    ac_samples = [r["ac_out_w"] for r in ac_rows]
    solar = [(r["solar_w"], r["dt"]) for r in fresh if r["solar_w"] is not None]
    socs = [r["soc"] for r in fresh if r["soc"] is not None]
    peak_solar, peak_at = max(solar, default=(None, None))
    return {
        "samples": len(scoped),
        "first": scoped[0]["dt"].isoformat(timespec="seconds"),
        "ac_duty_pct": round(100 * sum(w > AC_ON_W for w in ac_samples) / len(ac_samples), 1)
                       if ac_samples else None,
        "ac_span_min": round((ac_rows[-1]["dt"] - ac_rows[0]["dt"]).total_seconds() / 60)
                       if len(ac_rows) > 1 else 0,
        "ac_avg_when_on": _mean([w for w in ac_samples if w > AC_ON_W]),
        "peak_solar_w": peak_solar,
        "peak_solar_at": peak_at.isoformat(timespec="minutes") if peak_at else None,
        "soc_min": min(socs) if socs else None,
        "soc_max": max(socs) if socs else None,
    }


def hourly_profile(rows, unit, since):
    """Average solar and load by hour of local day — the shape of a typical day.

    Load is what actually leaves the box (AC + DC ports): `pd.wattsOutSum` also
    counts the internal main->Extra pack transfer while charging (up to ~550 W
    on A), which never leaves the unit. Rows logged before ac_out_w existed
    fall back to watts_out. `ac_w` is reported separately so the frontend can
    treat B's AC output — hard-wired into A's input — as a transfer, not load.
    """
    by_hour = {h: {"solar": [], "load": [], "ac": []} for h in range(24)}
    for row in rows:
        if row["unit"] != unit or row["dt"] < since or not _fresh(row):
            continue
        slot = by_hour[row["dt"].hour]
        slot["solar"].append(row["solar_w"])
        if row["ac_out_w"] is not None:
            slot["load"].append(row["ac_out_w"] + (row["dc_out_w"] or 0))
        else:
            slot["load"].append(row["watts_out"])
        slot["ac"].append(row["ac_out_w"])
    return [
        {"hour": h, "solar_w": _mean(v["solar"]) or 0, "load_w": _mean(v["load"]) or 0,
         "ac_w": _mean(v["ac"]) or 0}
        for h, v in sorted(by_hour.items())
    ]
