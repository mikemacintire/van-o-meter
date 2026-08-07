# Solar forecast — predicting tomorrow's harvest from the weather

Date: 2026-08-07
Status: approved, display-only

## Problem

The rig has no idea what tomorrow looks like. The Backup metric answers "if
there is no solar tomorrow, how long do I have?" — the pessimistic bound. The
useful question next to it is "how much solar is actually coming?", which turns
a guess about shore power or A/C use into a decision.

EcoFlow serves no weather and no history (`docs/api.md`), so both halves have
to come from outside: a weather source for the forecast, and the poller's own
CSV for what our panels did under past weather.

## Scope

**Display only.** Nothing reads the prediction — not the balancer, not the
poller. A new module, a new endpoint, two new pieces of UI. Existing control
logic is untouched, which is the point: this feature cannot brown out the truck.

## What the data actually said

Measured before designing, against 10 complete logged days (2026-07-28 →
08-06), combined A+B daily solar Wh, leave-one-out cross-validated, versus the
baseline of predicting the window's flat average:

| Feature (Open-Meteo daily) | Pearson r | LOO MAE | vs. flat average |
|---|---|---|---|
| `sunshine_duration` | +0.94 | 134 Wh (7%) | **52% better** |
| `cloud_cover_mean` | −0.86 | 177 Wh (9%) | 36% better |
| `shortwave_radiation_sum` | +0.64 | 310 Wh (15%) | 11% *worse* |
| `precipitation_sum` | −0.23 | 340 Wh (16%) | 22% worse |
| `temperature_2m_max` | +0.47 | 365 Wh (18%) | 31% worse |

Two findings shaped the design.

**Total irradiance is the wrong feature.** The obvious choice —
`shortwave_radiation_sum`, energy per m² — scores *worse than not modelling at
all*. Sunshine hours win decisively. That matches the rig's known situation:
the truck parks under a tree by necessity, so diffuse sky brightness is eaten
by the canopy no matter how bright the day. What survives is direct sun
punching through, which is exactly what `sunshine_duration` measures (hours
with direct normal irradiance above 120 W/m²). The fitted shape is roughly
`335 Wh baseline + 183 Wh per sunshine hour`.

**Full batteries censor the training data.** On 2026-08-03 unit B reached 100%
SOC; the MPPT throttles once a pack is full, so that day's logged Wh understates
what was available. Dropping it moved `sunshine_duration` from r=+0.65 to
r=+0.94. Curtailed days must not train the model.

## Architecture

Two modules, splitting I/O from logic the way `client.py` and `balancer.py`
already do.

### `ecoflow/weather.py` — the only thing that touches the network

One call to `https://api.open-meteo.com/v1/forecast` with `past_days` and
`forecast_days` returns the training history *and* the forecast in a single
response. No API key; free for non-commercial use.

Using that one endpoint for both halves is deliberate. Open-Meteo also serves an
ERA5 archive, and ERA5 fits our measurements *better* (r=+0.78 on irradiance vs
+0.48 from the forecast model) — but ERA5 is reanalysis and does not exist for
tomorrow. Training on ERA5 and predicting from the forecast model would be
train/serve skew: the two sources disagreed by up to 6 MJ/m² on the same day
during testing. Train on what you predict with.

Responses persist to `data/weather.json`. The truck is off-grid; a failed fetch
serves the last good payload with its age attached rather than nothing.

### `ecoflow/forecast.py` — pure, no network, no clock of its own

Joins measured daily energy (from `history.daily_energy`) to weather days, then:

1. **Gate the training set.**
   - Complete poller days only — a day needs enough samples that its counter
     deltas describe a whole day. Partial days (the first day of logging, days
     around a machine sleep) are dropped.
   - Uncurtailed days only — if either unit's max SOC reached the curtailment
     threshold, the day is censored and dropped.
2. **Select a model.** Fit a two-parameter linear regression per candidate
   feature; score each by leave-one-out cross-validated MAE. Compare against
   the flat-average baseline over the same days.
3. **Make the winner earn it.** A feature is used only if it beats the baseline
   by a margin. Otherwise the prediction *is* the rolling average. Given that
   irradiance currently scores worse than the baseline, this rule is load-bearing
   rather than theoretical, and it damps feature-flapping between refits while
   the sample count is small.
4. **Clamp.** Predictions are bounded so a freak forecast cannot extrapolate to
   an implausible number.

Below a minimum number of usable days the model reports `learning` and no
prediction, mirroring the Backup metric's "learning usage" state.

Config is a `Config` dataclass with `save`/`load`, following `balancer.Config`
exactly, persisted to `data/forecast.json`: latitude, longitude, a label for
display, and `window_days` (default 14).

### Location

Config file, pre-set to Butler Beach, FL 32080 — 29.7983, −81.26701, from
Open-Meteo's geocoder. Editable when the truck moves. Not auto-detected: the
rig's uplink geolocates to the carrier's POP, which can be hundreds of miles
off and would quietly poison the model with someone else's weather.

### `/api/forecast`

A new endpoint, deliberately not folded into `/api/history`. Two reasons:
different cadence (weather changes over hours, the CSV over seconds), and
failure isolation — if Open-Meteo is unreachable, `/api/history` must be
unaffected.

Fetching is lazy behind a TTL on request, not a background thread. The
dashboard already runs a balancer thread, and duplicate dashboard instances
each running their own copy of one has already caused a real incident
(2026-08-05). A request-driven cache adds no new thread to get wrong.

## UI

**Overview (far view).** One `.wrow` reading `Tomorrow` under the existing
`Today` row in the INPUT panel, mirroring `Backup` at the foot of the OUTPUT
panel. The Overview is `height: 100vh; overflow: hidden` and must not scroll —
one more row in an existing `.prows` list is the whole change. Dims to an
em-dash while learning or when the data is stale.

**History (near view).** A card carrying what the far view cannot: the next
several days as a strip, which feature is currently winning and by how much,
the model's ± accuracy, the training-day count, and the age of the weather data.

## Testing

`tests/test_forecast.py` and `tests/test_weather.py`, both offline — fixture
payloads, no live calls. Coverage: the training gates drop partial and curtailed
days; feature selection picks the better feature; selection falls back to the
flat average when nothing clears the margin; `learning` below the minimum day
count; predictions clamp; config round-trips and tolerates a missing or
malformed file the way `balancer.Config` does.

## Honest limitations

- **n is small.** Nine usable days at design time. r=+0.94 is encouraging, not
  established. Early error bars will be wide and the selected feature may change
  between refits. The baseline-margin rule means the failure mode is "reverts to
  a rolling average", not "confidently wrong".
- **A 30-day window does not exist yet.** Logging began 2026-07-25, so 30 days
  of history first becomes available around 2026-08-24. The window setting is
  an upper bound on what gets used, not a promise that it exists.
- **Curtailment will get worse, not better.** As the model becomes useful it
  will be because the batteries are filling up more often — and those are exactly
  the days it must discard. If curtailed days ever dominate, the training set
  shrinks toward nothing. Worth revisiting if it starts happening regularly;
  the day count in the History card is the early warning.
- **The tree is not in the model.** The fit absorbs current shading into its
  coefficients. Park somewhere else and the coefficients are wrong until enough
  days at the new spot age into the window.
