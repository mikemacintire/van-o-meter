# UI audit + overhaul — Pod Power Console

Date: 2026-08-03 · Target: `web/index.html` (+ a static-asset route in `dashboard.py`)
Method: `/impeccable` (skill v4.0.4) — detector, audit, critique; Playwright captures at
1920×1080, 1400×full, 1024×768, 820×1180.

Artifacts written first: `PRODUCT.md`, `DESIGN.md`.

---

## Audit health score

| # | Dimension | Score | Key finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 1/4 | No `:focus-visible` style anywhere; reduced-motion nukes all feedback; 10–11px labels |
| 2 | Performance | 3/4 | Lean and fast; two layout-property transitions |
| 3 | Responsive | 1/4 | Overview only composes at 1920×1080 — dead band at 1080p, content cut off at 768p |
| 4 | Theming | 3/4 | Good token system already; chrome accent is off-brand vs the truck |
| 5 | Implementation integrity | 3/4 | Coherent, product-specific, well-commented; drift is in type scale, not structure |
| **Total** | | **11/20** | **Acceptable — significant work needed** |

**Integrity verdict: pass.** This is not a template. The schematic, the transfer logic, the
honest-history notes and the readback-verified controls are all specific to this rig. The
detector found only 3 anti-patterns in 2082 lines. The problems are legibility, fit, and
touch — not authorship.

---

## Findings

### P0 — blocks the stated job

**P0-1 · Overview does not fit the screen it is for.**
`#view-overview` is hard-built for 1920×1080. At 1080p there is a ~130px dead band below
the cards; at 1024×768 (a realistic tablet) the four stat tiles are cut off below the fold
and the "no scroll" contract breaks. An always-on tablet of unknown size is the primary
target, so this is the core defect.
→ Fluid one-screen grid; `vh`-clamped type; `minmax(0,1fr)` rows that absorb slack.

**P0-2 · The far view is written at near-view sizes.**
Overview labels are 10.5–11px uppercase with .13–.15em tracking; tile subs 11px; chart axis
10px. None of it is readable at 6–10 ft. The one number that *is* large (54%) is the one
you could infer from the bar anyway.
→ Overview type floor 14px; labels 14–19px; values 26–46px; hero 56–116px.

**P0-3 · No focus indicator anywhere.**
No `:focus-visible` rule exists. Keyboard and switch users get nothing. WCAG 2.4.7 fail.
→ 2px `--accent` ring at 2px offset on every interactive element.

### P1 — major

**P1-1 · Units gauge text collides with its own arc.**
`renderGauge` centers `2.15 kWh of 3.60 kWh` at y=91, x=75 in a r=62 ring: the string is
~126px wide and the arc crosses x≈14 and x≈136 at that height. Both units clip on both
sides. Confirmed in capture.
→ Move the capacity line out of the SVG, below the ring, in HTML.

**P1-2 · Control rows separate a label from its control by ~800px.**
In a 900px card, label sits far left and the toggle/slider far right with void between.
Association is lost and the slider is a long reach on a tablet.
→ Two-column row: text block (max 46ch) left, fixed 220px control column right.

**P1-3 · Prose is set in monospace at 11px, full card width.**
The auto-balance hint is one 1800px line; control hints run ~180ch. Mono + 11px + no
measure cap is the least readable combination available.
→ IBM Plex Sans 14px, capped at 46ch in-column / 70ch full-width.

**P1-4 · `prefers-reduced-motion` destroys feedback.**
Global `animation-duration: .01ms; transition-duration: .01ms` also kills the ok/fail
command flash and the pending spinner — the only confirmation a write landed.
→ Keep state transitions at 120ms; stop only looping motion.

**P1-5 · Touch targets below 44px.**
Slider thumbs 18px (12px on Firefox), settings toggles 25px tall, mini-seg buttons ~30px,
rail 44px only at its widest.
→ 44px floor, 52px on control rows, 26px thumbs with a 44px hit band.

**P1-6 · Chrome accent is not the truck's blue.**
`--accent` is teal `#4FD1C5`; the outline samples to `#38A2DE`/`#6CC2F0`. Nav, focus and
sliders read as a different product from the centerpiece.
→ `--accent: #4FB3E8` (sampled). Split the transfer series to its own `--xfer` token so no
data colour changes and the validated CVD separation is preserved.

**P1-7 · Fonts load from Google's CDN.**
Off-grid truck, possibly no internet: first paint falls back to system faces and the whole
type system collapses.
→ Self-host woff2 under `web/assets/fonts`, add a static route.

### P2 — minor

- **P2-1** Eight type sizes inside a 4px band (10, 10.5, 11, 11.5, 12, 12.5, 13, 14) —
  noise, not a scale. → one 8-step scale.
- **P2-2** Overview topbar duplicates the tiles (solar status / charging status) while the
  hero says nothing about direction. → fold status into a hero state line; topbar carries
  clock + freshness.
- **P2-3** Bank bar reads 54% but lights 6/12 segments (50%). Coarse and visibly
  inconsistent with its own number. → 20 segments, and tie the fill colour to the number.
- **P2-4** Zeroes and live values look identical; a screen of `0 W` has the same weight as
  a screen of real power. → de-emphasise zero, brighten live.
- **P2-5** Units view: `solar in / ac in / output` labels and values sit at opposite card
  edges. → tighten to a definition row.
- **P2-6** Two layout-property transitions (`width` on `.meter i`, `height` on the pack
  bars). → transform-based.
- **P2-7** Rail uses `aria-selected` without a `tablist` role; toast has no `aria-live`.
- **P2-8** Units and Control views are ~40% empty at 1400px tall.

### P3 — polish
Detector's `dark-glow` on the nav indicator (kept deliberately — glow = energized, and the
brief pins the neon world), em-dash density in hint copy.

---

## Positive findings (keep)

- The token system, the CVD-validated series palette and the comment explaining it.
- The schematic: coordinates anchored to the photo crop, flows inert until power moves.
- Honest-data posture: "data frozen 9m", "cloud link down", no-history footnote.
- Readback-verified writes with ok/fail flash + toast.
- `poller.migrate()`-style care throughout — comments explain *why*, not *what*.

---

## Plan

Nine phases. Each ends verifiable. No functional regressions: every control, chart,
readout and endpoint behaves as before.

| # | Phase | Verify |
|---|---|---|
| 1 | Self-host fonts; `/assets/<path>` route in `dashboard.py` | Page renders with network blocked |
| 2 | Token layer: type scale, `--accent`→truck blue, `--xfer` split, elevation, focus ring | Detector clean; no colour regressions in charts |
| 3 | Component layer: tile, card, row, chip, seg, toggle, slider at touch sizes | All six views render; targets ≥44px |
| 4 | **Overview rebuild** — fluid one-screen grid, far-view type, hero state line, 20-seg bar | Fits 1024×768 → 2560×1440 with no scroll and no dead band |
| 5 | Schematic type + energized-glow discipline | Truck untouched; in-SVG values legible at distance |
| 6 | Units view — gauge fix, flow rows, fill the dead space | Gauge text clear of the arc at both units |
| 7 | Control view — row geometry, prose measure, touch sizes | Every control still sends + verifies by readback |
| 8 | Power / Energy / System — unified tiles, axis legibility, empty states | Charts identical in data, larger in chrome |
| 9 | A11y + motion pass; batched inspection round; fix; confirm | pytest green; captures at 4 viewports; detector clean |

### Explicitly out of scope
- The truck artwork itself (pinned by the brief).
- The segmented bank bar concept (owner likes it — refined, not replaced).
- Data semantics: transfer maths, house-load definition, energy differencing.
- Light mode. There is no light-mode use scene.

---

## Outcome — all nine phases shipped

Verified with a scripted sweep (`check.mjs`) over 6 views × 6 viewports —
1920×1080, 1600×1000, 1280×800, 1024×768, 820×1180, 430×900 — asserting no
horizontal scroll, no Overview vertical scroll above 900×620, no text under the
per-view floor, no touch target under 44px (measuring the toggle's real tap band,
not the visible switch), and no console errors. **36/36 clean.** `pytest` 97
passed. Detector down from 3 anti-patterns to 2, both deliberate (below).

Bugs found and fixed that were not in the original finding list:

- **Topbar was the single source of every horizontal scrollbar.** The rewrite
  dropped `flex-wrap`, so title + pills + clock + button simply grew past the
  viewport — 76px at 1024, 278px at 820, 153px at 430. Now wraps, has
  `min-width: 0` on its children, and sheds pills → freshness → clock in order.
- **The toggle's 44px tap band never existed.** `inset: -9px` on a `::before`
  with no `position: absolute` is inert. Verified after the fix with
  `elementFromPoint` probes past each edge.
- **Overview charts fed their measured height back into the layout that produced
  it**, pushing the last axis off a pinned 1080p screen. The SVG is now
  `position: absolute` inside its wrapper, breaking the loop.
- **Hash deep-links only worked on a cold load** — no `hashchange` listener, so
  back/forward and a re-tapped bookmark left the previous view on screen.
- **Segmented controls overflowed their fixed 224px column onto their own
  labels** at tablet width. Column is `minmax(220px, auto)`; control cards go
  single-column below 1250px.
- **Chart x-axis edge labels were clipped** ("08:05 PM" rendered as "08:05") —
  first and last labels now anchor inward. Tick budget raised to 170px/label for
  the larger axis type.
- **`.grid` column count** now comes from a container query on the card, not a
  viewport breakpoint — a 400px unit card at 1024 is not a phone.

### Deliberate detector findings (not defects)

- `dark-glow` on the nav's selected-view marker. Glow is the pinned visual world
  and is rationed: it appears only on things carrying power or demanding
  attention (live flows, lit battery segments, the status dot, the truck). The
  marker is the "you are here" lamp on an instrument panel. Documented in
  DESIGN.md.
- `layout-transition` on the schematic pack bars. Those are SVG `y`/`height`
  geometry attributes, not CSS box metrics — they don't trigger document reflow.
  Noted inline at the call site.

### Known, pre-existing, not addressed

`/api/live` intermittently returns `'NoneType' object has no attribute 'get'`
when EcoFlow's cloud serves a partial payload; `renderLive` throws on it and the
console shows "signal lost — retrying" until the next poll. Observed twice
during the sweep. Server-side and unrelated to this pass.
