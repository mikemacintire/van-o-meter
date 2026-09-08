# Stale-Data Honesty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the History charts from plotting EcoFlow's frozen cloud snapshots as live readings: flag them at log time, draw gaps, and bridge gaps with the unit's own energy counters.

**Architecture:** The poller adds a `stale` column (payload identical to last tick). `history.py` derives the flag for older rows, keeps stale rows out of every watts mean, emits `None` buckets, and bridges runs of empty buckets with counter rates plus a `bridged` flag. `web/index.html` splits chart paths at `null`, draws bridged runs dashed, and draws dotted straight SOC bridges.

**Tech Stack:** Python 3 (stdlib csv/bisect), pytest; vanilla JS in one HTML file, `node --test`.

Spec: `docs/superpowers/specs/2026-09-08-stale-data-honesty-design.md`.

Run tests with `.venv\Scripts\python.exe -m pytest -q` and `node --test tests/`.
Commit by explicit path only (`web/index.html` carries an unrelated uncommitted redesign; see Task 7).

---

### Task 1: Poller writes `stale`

**Files:**
- Modify: `ecoflow/poller.py` (FIELDS, `extract_sample`)
- Modify: `run_poller.py` (poll loop)
- Test: `tests/test_poller.py`

- [ ] **Step 1: Failing tests**

```python
def test_extract_sample_marks_stale_when_payload_repeats():
    q = {"ems.f32LcdShowSoc": 50.0, "pd.wattsOutSum": 5}
    assert extract_sample(q)["stale"] == 0
    assert extract_sample(q, prev=None)["stale"] == 0
    assert extract_sample(q, prev=dict(q))["stale"] == 1
    assert extract_sample(q, prev={**q, "pd.wattsOutSum": 6})["stale"] == 0


def test_fields_end_with_stale():
    assert FIELDS[-1] == "stale"
    assert HEADER[-1] == "stale"
```

- [ ] **Step 2: Run** `pytest tests/test_poller.py -q` → FAIL (`KeyError: 'stale'` / assertion).

- [ ] **Step 3: Implement**

`ecoflow/poller.py`: append `"stale"` to `FIELDS`; change signature to `extract_sample(quota, prev=None)` and, before `return sample`:

```python
    # 1 when the cloud handed back the exact payload it served last tick: the
    # unit has dropped off WiFi and EcoFlow is replaying a cached snapshot with
    # code 0 (A froze for 4 h twice on 2026-09-07). Same signal /api/live uses.
    sample["stale"] = int(prev is not None and quota == prev)
```

`run_poller.py`: before the loop `last = {}`; in the loop:

```python
                quota = client.get_quota_all(sn)
                sample = extract_sample(quota, last.get(unit))
                last[unit] = quota
                append_sample(CSV_PATH, now, unit, sample)
                print(f"{now} {unit}: soc=... chg_state={sample['chg_state']}"
                      f"{' STALE' if sample['stale'] else ''}")
```

- [ ] **Step 4: Run** `pytest tests/test_poller.py -q` → PASS. Also `test_fields_start_with_original_v1_columns` must still pass.

- [ ] **Step 5: Commit** `git add ecoflow/poller.py run_poller.py tests/test_poller.py`.

---

### Task 2: History derives `stale` for older rows

**Files:**
- Modify: `ecoflow/history.py` (`NUMERIC`, `_cache`, `load_rows`, `reload_if_header_changed`)
- Test: `tests/test_history.py`

- [ ] **Step 1: Failing tests**

```python
def _write(path, header, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def test_load_rows_derives_stale_from_repeated_readings(tmp_path):
    p = tmp_path / "s.csv"
    old = [c for c in HEADER if c != "stale"]
    base = ["2026-09-07T23:37:00+00:00", "A", 39.0, 0, 0, 5, 0, 1, 1, 0, 5.0, 30,
            63191, 68622, 0, 77150, 525, 31.9, 46.2, 49374, 49373]
    rows = [base, ["2026-09-07T23:37:30+00:00"] + base[1:],
            ["2026-09-07T23:38:00+00:00", "B"] + base[2:],          # other unit: fresh
            ["2026-09-08T03:37:00+00:00", "A", 32.5] + base[3:]]     # changed: fresh
    _write(p, old, rows)
    got = load_rows(p)
    assert [r["stale"] for r in got] == [0, 1, 0, 0]


def test_load_rows_derivation_survives_tail_read(tmp_path):
    p = tmp_path / "s.csv"
    old = [c for c in HEADER if c != "stale"]
    base = ["2026-09-07T23:37:00+00:00", "A", 39.0] + [0] * (len(old) - 3)
    _write(p, old, [base])
    load_rows(p)
    with p.open("a", newline="") as f:
        csv.writer(f).writerow(["2026-09-07T23:37:30+00:00"] + base[1:])
    assert [r["stale"] for r in load_rows(p)] == [0, 1]


def test_load_rows_keeps_logged_stale_flag(tmp_path):
    p = tmp_path / "s.csv"
    base = ["2026-09-07T23:37:00+00:00", "A", 39.0] + [0] * (len(HEADER) - 4)
    _write(p, HEADER, [base + [0], ["2026-09-07T23:37:30+00:00"] + base[1:] + [1]])
    assert [r["stale"] for r in load_rows(p)] == [0, 1]
```

(`import csv` at top; the cache is module-global, so each test must use a fresh path — `tmp_path` differs per test, and `load_rows` resets on a path change.)

- [ ] **Step 2: Run** → FAIL (`KeyError: 'stale'`).

- [ ] **Step 3: Implement**

```python
NUMERIC = {..., "stale"}

_cache = {"path": None, "offset": 0, "rows": [], "header": None,
          "first": None, "total": 0, "last": {}}
# ... every _cache.update(...) reset also passes last={}

_NOT_A_READING = {"timestamp", "unit", "dt", "stale"}


def _same_reading(a, b):
    return all(a.get(k) == b.get(k) for k in a if k not in _NOT_A_READING)
```

In `load_rows`, right after `if row:`:

```python
                    # Rows logged before the stale column existed: a reading
                    # identical to the unit's previous one is the cloud replaying
                    # a frozen snapshot (docs/api.md). Fixes history retroactively.
                    prev = _cache["last"].get(row["unit"])
                    if row.get("stale") is None:
                        row["stale"] = int(prev is not None and _same_reading(row, prev))
                    _cache["last"][row["unit"]] = row
```

- [ ] **Step 4: Run** `pytest tests/test_history.py -q` → PASS.

- [ ] **Step 5: Commit** `git add ecoflow/history.py tests/test_history.py`.

---

### Task 3: Aggregations ignore stale rows; buckets emit `None`

**Files:**
- Modify: `ecoflow/history.py` (`bucket_series`, `summarize`, `hourly_profile`, `avg_daily_drain`)
- Test: `tests/test_history.py`

- [ ] **Step 1: Failing tests**

```python
def test_bucket_series_skips_stale_rows_and_emits_none_buckets(t0):
    rows = [
        row(t0, solar_w=100, soc=50),
        row(t0 + timedelta(minutes=1), solar_w=900, soc=99, stale=1),
        row(t0 + timedelta(minutes=6), solar_w=900, soc=99, stale=1),
    ]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300, ["solar_w", "soc"])
    assert out["solar_w"] == [100, None]
    assert out["soc"] == [50, None]
    assert out["bridged"] == [False, False]


def test_summarize_ignores_stale_rows(t0):
    rows = [row(t0, ac_out_w=10, soc=50, solar_w=100),
            row(t0 + timedelta(minutes=1), ac_out_w=900, soc=99, solar_w=999, stale=1)]
    s = summarize(rows, "A", t0 - timedelta(hours=1))
    assert s["samples"] == 2
    assert s["ac_duty_pct"] == 0 and s["soc_max"] == 50 and s["peak_solar_w"] == 100


def test_hourly_profile_ignores_stale_rows(t0):
    rows = [row(t0, solar_w=100, ac_out_w=10, dc_out_w=0),
            row(t0 + timedelta(minutes=1), solar_w=900, ac_out_w=900, dc_out_w=0, stale=1)]
    prof = hourly_profile(rows, "A", t0 - timedelta(hours=1))
    assert prof[t0.hour]["solar_w"] == 100 and prof[t0.hour]["load_w"] == 10


def test_avg_daily_drain_soc_endpoints_skip_stale_rows(t0):
    # 3 days, A and B both drop 10 % of 1000 Wh with no inflow -> 200 Wh over 3 d
    rows = []
    for u in ("A", "B"):
        rows += [row(t0, unit=u, soc=60, cum_solar_wh=0, cum_ac_in_wh=0, cum_ac_out_wh=0),
                 row(t0 + timedelta(days=3), unit=u, soc=50, cum_solar_wh=0, cum_ac_in_wh=0, cum_ac_out_wh=0),
                 row(t0 + timedelta(days=3, hours=1), unit=u, soc=1, stale=1,
                     cum_solar_wh=0, cum_ac_in_wh=0, cum_ac_out_wh=0)]
    d = avg_daily_drain(rows, {"A": 1000, "B": 1000}, now=t0 + timedelta(days=4))
    assert round(d["wh_per_day"], 1) == round(200 / 3, 1)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

```python
def _fresh(row):
    """A row the unit actually reported, not a replayed frozen snapshot."""
    return not row.get("stale")
```

`bucket_series` (full replacement; `_bridge` arrives in Task 4, so for now it is a no-op stub returning nothing):

```python
def bucket_series(rows, unit, since, bucket_s, metrics):
    """Downsample to fixed-width buckets: mean of each metric, last SOC.

    Stale rows never feed a mean. A bucket with no fresh row is still emitted,
    with None everywhere, so the chart shows a gap instead of a straight line
    across the outage; `_bridge` then fills its power metrics from the
    counters where it can and marks them in `bridged`.
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
    return  # Task 4
```

`summarize`: `scoped = [... all rows ...]`; add `fresh = [r for r in scoped if _fresh(r)]` and build `ac_rows`, `solar`, `socs` from `fresh`. `samples` stays `len(scoped)`.

`hourly_profile`: `if row["unit"] != unit or row["dt"] < since or not _fresh(row): continue`.

`avg_daily_drain`: `socs = [r for r in scoped[unit] if r["soc"] is not None and _fresh(r)]`.

- [ ] **Step 4: Run** `pytest tests/test_history.py -q` → PASS.

- [ ] **Step 5: Commit** `git add ecoflow/history.py tests/test_history.py`.

---

### Task 4: Counter bridges

**Files:**
- Modify: `ecoflow/history.py` (`_bridge`, `BRIDGE`)
- Test: `tests/test_history.py`, `tests/test_dashboard_history.py`

- [ ] **Step 1: Failing tests**

```python
def test_bucket_series_bridges_a_gap_from_counters(t0):
    # fresh at t0 and t0+20 min; two all-stale buckets between. Counter moved
    # 100 Wh over 20 min -> 300 W average, regardless of the frozen watts.
    rows = [
        row(t0, solar_w=50, ac_out_w=0, dc_out_w=0, soc=50,
            cum_solar_wh=1000, cum_ac_out_wh=500, cum_dc_out_wh=10),
        row(t0 + timedelta(minutes=6), solar_w=50, ac_out_w=0, dc_out_w=0, soc=50, stale=1,
            cum_solar_wh=1000, cum_ac_out_wh=500, cum_dc_out_wh=10),
        row(t0 + timedelta(minutes=11), solar_w=50, ac_out_w=0, dc_out_w=0, soc=50, stale=1,
            cum_solar_wh=1000, cum_ac_out_wh=500, cum_dc_out_wh=10),
        row(t0 + timedelta(minutes=20), solar_w=50, ac_out_w=0, dc_out_w=0, soc=40,
            cum_solar_wh=1000, cum_ac_out_wh=600, cum_dc_out_wh=20),
    ]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300,
                        ["solar_w", "ac_out_w", "dc_out_w", "watts_out", "soc"])
    assert out["bridged"] == [False, True, True, False]
    assert out["ac_out_w"] == [0, 300, 300, 0]
    assert out["dc_out_w"] == [0, 30, 30, 0]
    assert out["watts_out"][1] == 330
    assert out["solar_w"] == [50, 0, 0, 50]
    assert out["soc"] == [50, None, None, 40]


def test_bucket_series_leaves_open_ended_gap_unbridged(t0):
    rows = [row(t0, ac_out_w=0, cum_ac_out_wh=500),
            row(t0 + timedelta(minutes=6), ac_out_w=0, cum_ac_out_wh=500, stale=1)]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300, ["ac_out_w"])
    assert out["ac_out_w"] == [0, None] and out["bridged"] == [False, False]


def test_bucket_series_skips_bridge_across_counter_reset(t0):
    rows = [row(t0, ac_out_w=0, cum_ac_out_wh=500),
            row(t0 + timedelta(minutes=6), ac_out_w=0, cum_ac_out_wh=500, stale=1),
            row(t0 + timedelta(minutes=12), ac_out_w=0, cum_ac_out_wh=3)]
    out = bucket_series(rows, "A", t0 - timedelta(hours=1), 300, ["ac_out_w"])
    assert out["ac_out_w"][1] is None and out["bridged"][1] is False


def test_bucket_series_bridges_from_a_fresh_row_before_the_range(t0):
    rows = [row(t0 - timedelta(minutes=10), ac_out_w=0, cum_ac_out_wh=500),
            row(t0 + timedelta(minutes=1), ac_out_w=0, cum_ac_out_wh=500, stale=1),
            row(t0 + timedelta(minutes=20), ac_out_w=0, cum_ac_out_wh=550)]
    out = bucket_series(rows, "A", t0, 300, ["ac_out_w"])
    assert out["ac_out_w"][0] == 100 and out["bridged"][0] is True
```

And in `tests/test_dashboard_history.py`, extend `test_history_payload_includes_drain` (or add one) to assert `payload["series"]["A"]["bridged"]` is a list.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

```python
import bisect

# bucket metric -> the lifetime counter(s) whose delta, divided by elapsed
# hours, stands in for it across a frozen span
BRIDGE = {
    "solar_w": ("cum_solar_wh",),
    "ac_out_w": ("cum_ac_out_wh",),
    "dc_out_w": ("cum_dc_out_wh",),
    "ac_charge_w": ("cum_ac_in_wh",),
    "watts_out": ("cum_ac_out_wh", "cum_dc_out_wh"),
}


def _bridge(out, keys, empty, bucket_s, unit_rows, metrics):
    """Fill runs of empty buckets with counter rates from the bounding fresh rows.

    The counters live on the unit and keep integrating while the cloud link is
    down, so the energy across the outage is exact even though its timing is
    not. SOC has no counter and stays None. Open-ended runs (still frozen) and
    counter resets are left alone.
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
```

- [ ] **Step 4: Run** `pytest -q` → all PASS.

- [ ] **Step 5: Commit** `git add ecoflow/history.py tests/test_history.py tests/test_dashboard_history.py`.

---

### Task 5: `powerRows` keeps nulls and reports gap/bridged

**Files:**
- Modify: `web/index.html` (`powerRows`)
- Test: `tests/power-presentation.test.mjs`

- [ ] **Step 1: Failing test** (append to the .mjs file)

```js
const prStart = script.indexOf('function powerRows(');
const prEnd = script.indexOf('/* ---------- power chart', prStart);
const powerRows = vm.runInNewContext(script.slice(prStart, prEnd) + ';powerRows');
const series = (t, o) => ({ t, solar_w: [], watts_out: [], ac_out_w: [], dc_out_w: [], bridged: [], ...o });

test('powerRows keeps a frozen bucket as a gap, not a zero', () => {
  const A = series([0, 1, 2], { solar_w: [10, null, 30], ac_out_w: [5, null, 7], dc_out_w: [1, null, 1],
    watts_out: [6, null, 8], bridged: [false, false, false] });
  const B = series([0, 1, 2], { solar_w: [0, 0, 0], ac_out_w: [0, 0, 0], dc_out_w: [0, 0, 0],
    watts_out: [0, 0, 0], bridged: [false, false, false] });
  const rows = powerRows({ series: { A, B } });
  assert.equal(rows[1].solA, null); assert.equal(rows[1].load, null);
  assert.equal(rows[1].gapA, true); assert.equal(rows[0].gapA, false);
  assert.equal(rows[2].load, 8);
});
test('powerRows flags counter-bridged buckets', () => {
  const A = series([0, 1], { solar_w: [10, 12], ac_out_w: [5, 31], dc_out_w: [1, 5], watts_out: [6, 36],
    bridged: [false, true] });
  const B = series([0, 1], { solar_w: [0, 0], ac_out_w: [0, 0], dc_out_w: [0, 0], watts_out: [0, 0], bridged: [false, false] });
  const rows = powerRows({ series: { A, B } });
  assert.equal(rows[1].bridgedA, true); assert.equal(rows[1].load, 36); assert.equal(rows[0].bridgedA, false);
});
```

- [ ] **Step 2: Run** `node --test tests/` → FAIL.

- [ ] **Step 3: Implement** (replace `powerRows`)

```js
/** Solar A + solar B stacked, house load as a line. Shared by several charts.
 *  null means the unit's cloud link was frozen and nothing bridged it: a gap,
 *  never a zero. bridgedA/B mean the value is an average from the unit's energy
 *  counters across such a freeze. A unit with no bucket at all at that time
 *  contributes 0, as before. */
function powerRows(hist) {
  const sa = hist.series.A, sb = hist.series.B;
  const t = sa.t.length >= sb.t.length ? sa.t : sb.t;
  if (!t || t.length < 2) return null;
  const aAt = new Map(sa.t.map((tt, i) => [tt, i]));
  const bAt = new Map(sb.t.map((tt, i) => [tt, i]));
  return t.map(tt => {
    const i = aAt.has(tt) ? aAt.get(tt) : -1, j = bAt.has(tt) ? bAt.get(tt) : -1;
    const solA = i < 0 ? 0 : sa.solar_w[i];
    const solB = j < 0 ? 0 : sb.solar_w[j];
    // Load is what leaves the boxes: watts_out also counts A's internal
    // main->Extra pack transfer while charging (up to ~550 W), and B's AC
    // output is hard-wired into A's input — a transfer, not house load.
    // Old rows predate ac_out_w/dc_out_w; fall back to watts_out there.
    const loadA = i < 0 ? 0
      : sa.ac_out_w[i] != null ? sa.ac_out_w[i] + (sa.dc_out_w[i] ?? 0)
      : sa.watts_out[i];
    const dcB = j < 0 ? 0 : sb.dc_out_w[j];
    const xfer = j < 0 ? 0 : sb.ac_out_w[j];
    return {
      t: tt, solA, solB, xfer,
      load: loadA == null ? null : loadA + (dcB ?? 0),
      gapA: i >= 0 && solA == null && loadA == null,
      gapB: j >= 0 && solB == null && xfer == null,
      bridgedA: i >= 0 && !!(sa.bridged && sa.bridged[i]),
      bridgedB: j >= 0 && !!(sb.bridged && sb.bridged[j]),
    };
  });
}
```

- [ ] **Step 4: Run** `node --test tests/` → PASS (the file also syntax-checks the whole script).

- [ ] **Step 5: Commit** — see Task 7 (index.html is committed once, at the end).

---

### Task 6: Charts draw gaps, dashed bridges, dotted SOC bridges

**Files:**
- Modify: `web/index.html` (`drawPower`, its tooltip, `drawSoc`, power-chart legend)

No unit test covers SVG output; the .mjs syntax check plus the manual check in Step 3 are the verification.

- [ ] **Step 1: Add the run splitter** next to `powerRows`:

```js
/** Split rows into drawable runs: a break wherever `val` is null, and a new
 *  run (sharing its first point with the previous one, so the stroke stays
 *  continuous) wherever `dashed` flips. */
function strokeRuns(rows, val, dashed) {
  const runs = [];
  let cur = null;
  rows.forEach(r => {
    const v = val(r);
    if (v == null) { cur = null; return; }
    const d = !!dashed(r);
    if (!cur || cur.dashed !== d) {
      const prev = cur ? cur.pts[cur.pts.length - 1] : null;
      cur = { dashed: d, pts: prev ? [prev] : [] };
      runs.push(cur);
    }
    cur.pts.push({ r, v });
  });
  return runs;
}
```

- [ ] **Step 2: Rewrite the drawing part of `drawPower`**

Replace `area`, `loadLine`, `xferLine` and the SVG body with:

```js
  const P = (r, v) => `${x(r.t).toFixed(1)},${f.y(v).toFixed(1)}`;
  const baseA = r => 0, baseB = r => r.solA ?? 0;
  // fill only; the top edge is stroked separately so a bridged stretch can be
  // dashed while the stack underneath still shows the energy it carried
  const area = (key, base) => strokeRuns(rows, r => r[key], () => false).map(run => {
    const up = run.pts.map(p => P(p.r, base(p.r) + p.v));
    const down = run.pts.map(p => P(p.r, base(p.r))).reverse();
    return `<polygon points="${up.concat(down).join(" ")}"/>`;
  }).join("");
  const stroke = (val, dashed, color, width, extra = "") =>
    strokeRuns(rows, val, dashed).map(run =>
      `<path d="${run.pts.map((p, i) => `${i ? "L" : "M"}${P(p.r, p.v).replace(",", " ")}`).join(" ")}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round"${run.dashed ? ' stroke-dasharray="5 4"' : extra}/>`
    ).join("");
  const hasXfer = rows.some(r => r.xfer > 1);
```

and in the template:

```js
    <g fill="url(#${gid}A)">${area("solA", baseA)}</g>
    <g fill="url(#${gid}B)">${area("solB", baseB)}</g>
    ${stroke(r => r.solA, r => r.bridgedA, C.A, 1)}
    ${stroke(r => r.solB == null ? null : baseB(r) + r.solB, r => r.bridgedB, C.B, 1)}
    ${hasXfer ? stroke(r => r.xfer, r => r.bridgedB, C.xfer, 2, ' stroke-dasharray="5 4"') : ""}
    ${stroke(r => r.load, r => r.bridgedA, C.load, 2)}
```

(The transfer line was already dashed; it stays dashed either way.) `vMax` must skip nulls: `Math.max(...rows.map(r => Math.max((r.solA ?? 0) + (r.solB ?? 0), r.load ?? 0, r.xfer ?? 0)))`.

Tooltip rows: define `const W = (v, br) => v == null ? "<b>no data</b>" : \`<b>${Math.round(v)} W</b>${br ? ' <span style="color:var(--faint)">avg</span>' : ""}\`;` and use `W(best.solA, best.bridgedA)`, `W(best.solB, best.bridgedB)`, `W(best.load, best.bridgedA)`; show the transfer row only when `best.xfer > 1`; the Net row only when none of the three is null, otherwise `<b>—</b>`.

- [ ] **Step 3: `drawSoc` gaps and dotted bridges**

Build points with nulls kept, then runs:

```js
    const s = hist.series[u];
    const all = s.t.map((t, i) => ({ t, v: s.soc[i] }));
    pts[u] = all.filter(p => p.v != null);               // tooltip + extents
    runs[u] = strokeRuns(all, p => p.v, () => false).map(r => r.pts.map(p => p.r));
```

(`const runs = {};` beside `pts`.) In the per-unit loop replace the single `d`/fill with one fill and one line per run, plus a dotted segment between consecutive runs:

```js
    const path = ps => ps.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${f.y(p.v).toFixed(1)}`).join(" ");
    runs[u].forEach((ps, k) => {
      const a = ps[0], z = ps[ps.length - 1];
      fills += `<path d="${path(ps)} L${x(z.t).toFixed(1)} ${y0.toFixed(1)} L${x(a.t).toFixed(1)} ${y0.toFixed(1)} Z" fill="url(#${gid}${u})"/>`;
      lines += `<path d="${path(ps)}" fill="none" stroke="${col[u]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      const next = runs[u][k + 1];
      // across a frozen span a straight line is the honest guess: far closer
      // to the truth than the plateau-and-cliff the raw log draws
      if (next) lines += `<path d="${path([z, next[0]])}" fill="none" stroke="${col[u]}" stroke-width="2" stroke-dasharray="2 4" stroke-linecap="round"/>`;
    });
```

`first`/`last` for the end dot come from `pts[u]` as before.

- [ ] **Step 4: Legend note** — in the History power chart legend (the one with "House load"), append:

```html
<span class="legend-note">dashed = counter average while the cloud link was frozen</span>
```

with CSS `.legend-note{color:var(--faint);margin-left:auto}` next to the existing `.legend` rule.

- [ ] **Step 5: Verify** — `node --test tests/` (syntax), then in the Browser pane open `http://localhost:8642/#history`, range 24h, and confirm: load line dashed at ~31 W across 7:37 pm–3:51 am on 2026-09-07/08, SOC dotted across the same span, tooltip says "avg" there. Screenshot for the user.

---

### Task 7: Commit index.html without the unrelated redesign

`web/index.html` already had a large uncommitted diff (the 2026-09-05 redesign) before this work. Commit only this plan's hunks:

- [ ] Before Task 5, snapshot: `cp web/index.html <scratchpad>/index.pending.html`.
- [ ] After Task 6: `git diff --no-index <scratchpad>/index.pending.html web/index.html > <scratchpad>/mine.patch`, then `git show HEAD:web/index.html > <scratchpad>/base.html`, `patch <scratchpad>/base.html <scratchpad>/mine.patch`, and stage that content with `git update-index --cacheinfo 100644,$(git hash-object -w <scratchpad>/base.html),web/index.html`. Commit with the .mjs test. The working copy keeps both sets of changes.
- [ ] If the patch does not apply cleanly, commit the backend only and tell Mike the frontend change is in the working copy alongside the redesign.

---

### Task 8: Docs

- [ ] `CLAUDE.md` data-collection section: one paragraph on the `stale` column, the derivation for old rows, and the counter bridges. `docs/api.md`: add the 2026-09-07 freeze durations under the existing offline notes. Commit by path.
