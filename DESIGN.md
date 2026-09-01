# Pod Power Console — design system

The world: **a lit instrument in a dark truck.** The rig's neon blue outline is the one
bright object; the interface is dark, matte, and recedes around it. Light means energized.
Everything that glows is carrying power right now — nothing glows for decoration.

Mode: **Operate.** The user is in a task (or in a glance). Scanability and consistency
outrank expression. Character lives in the truck, the type, and the precision of the data.

## Type

One superfamily, two faces (Oxanium retired 2026-08-05 — Mike wanted the more readable
face everywhere). Self-hosted in `web/assets/fonts` — the truck may have no internet, so
no CDN link may be load-bearing.

| Token | Family | Used for |
|---|---|---|
| `--disp` | IBM Plex Sans 600/700 | Headings, micro-labels, buttons, chips, nav — a *role* token: same family as `--sans`, heavier and uppercase-tracked |
| `--sans` | IBM Plex Sans 400/500/600 | Prose: hints, notes, descriptions, empty states |
| `--mono` | IBM Plex Mono 400/500/600 | Numbers, measurements, units, axis, code |

**Mono is for measurement, not for costume.** Any sentence a human reads is `--sans`.
Any quantity is `--mono` with `font-variant-numeric: tabular-nums`.

### Near scale — every view except Overview
Fixed px, ratio ≈1.15. Nothing below 12px anywhere.

```
--t-micro 12  uppercase micro-labels (tracking .14em)
--t-xs    13  dense grid values, chart axis
--t-sm    14  secondary text, hints, subs
--t-base  15  body prose
--t-md    17  card values
--t-lg    20  section values
--t-xl    26  stat values
--t-2xl   34  page-level numbers
```

### Far scale — Overview only
Viewport-height clamps, because viewing distance and screen size both vary and this screen
must fill exactly one screen at any size. This is the deliberate exception to the
fixed-scale rule; it exists because the surface is a wall display, not a window.

```
--f-label clamp(14px, 1.6vh, 19px)
--f-sub   clamp(14px, 1.5vh, 18px)
--f-val   clamp(26px, 3.6vh, 46px)
--f-hero  clamp(56px, 9vh, 116px)
```

**Overview floor: 14px.** If a number or label cannot be read at 14px from 8 ft, it does
not belong on Overview.

Prose measure 65–75ch. Control hints are capped at `46ch` inside their column.

## Color

Dark only. The scene is a dark truck interior at night and a screen in daylight; there is
no light mode use case.

### Chrome — carries no data identity
```
--bg       #070A0F   --surface  #10151D   --surface2 #151B24   --surface3 #1C232E
--ink      #E8EDF4   --muted    #8794A5   --faint    #5A6675
--accent   #2F9AD4   the truck's own blue — the tube core of truck.png as rendered
                     (screen blend @ .95 over --bg), re-sampled 2026-08-31; the old
                     #4FB3E8 sat visibly paler than the outline it claimed to match
--accent-dim #2E7FAE
```
`--accent` is nav selection, focus rings, slider thumbs, and active state **only**. It is
the truck's blue so the chrome and the centerpiece read as one object.

### Data series — never reused as chrome
```
--a    #C9820F  --a-bright #F0A82D    Unit A and its array
--b    #2492BA  --b-bright #3FB7E0    Unit B and its array
--load #C75D5D  --load-bright #E07A7A House load
--xfer #4FD1C5                        B→A transfer (was --accent; split out so the
                                      transfer series never collides with chrome)
```
Validated on the dark surface for OKLCH lightness band, chroma floor, CVD separation
(worst pair ΔE 14.8 protan) and 3:1 contrast. **Do not retune these without re-running
that check.**

**Overview exception (2026-08-05):** unit identity colors are retired on the home
screen — no key dots, no amber. Everything there speaks the truck's blue plus status;
the 24 h ribbon tells A from B with two brightnesses of the same blue (`#6CC2F0` /
`#2E7FAE`, both ≥3:1 on `--bg`) and names each line at its end instead of a legend.
Near views keep the full series palette.

### Status — reserved, never a series
```
--good #3ED88A   --warn #E3B341   --crit #F0555A
```

## Depth and light

Two separate systems. They are not interchangeable.

- **Elevation** = neutral shadow with offset and blur. Cards, popovers, sheets, toasts.
  `--e1 0 1px 2px rgba(0,0,0,.4)` · `--e2 0 6px 18px rgba(0,0,0,.5)` · `--e3 0 20px 48px rgba(0,0,0,.65)`
- **Energized glow** = zero-offset colored halo. Permitted *only* on something currently
  carrying power or demanding attention: live flow paths, a lit battery segment, an active
  status dot, the truck itself. A glow on an idle element is a bug.

## Space

4 / 8 / 12 / 16 / 20 / 24 / 32 / 40. More space above a heading than below it.
Radii: `--r-sm 10px` · `--r 14px` · `--r-lg 18px`.

## Touch

Tablet-first for every view but Overview (which is look-only).
Minimum target 44×44. Control rows use 52px. Slider thumbs 26px with a 44px hit band.
Segmented buttons: min-height 44, min-width 56.

## Motion

150–220 ms, `cubic-bezier(.2,.7,.3,1)`. Motion reports state change and nothing else.
No page-load choreography — the console loads into a glance.

Energized glow breathes (2026-08-31): everything carrying power right now — live flow
paths, arrowheads, an AC-on unit frame — shares one `breathe` cycle, 4.8 s ease-in-out,
glow swelling from its resting halo to a moderately brighter one and easing back. Calm
breathing, never a strobe: the quiet phase equals the old static glow, and the loop
stops under reduced motion (the static glow remains).

`prefers-reduced-motion`: keep state transitions (shortened to 120 ms) so feedback
survives; stop the looping ones — marching flow dashes, the pulsing dot, the spinner
becomes a static ring.

## Components

One vocabulary across the three near views (Units / Control / History — the
2026-08-05 consolidation folded Power, Energy and System into History and
Units). Overview is its own composition (2026-08-04 "balance-wings", revised
2026-08-05 "power-path"): three bands — marquee, stage reading INPUT → truck →
OUTPUT, ribbon — with no icon tiles and no mono-set words. See
`.impeccable/surfaces/web-index-html.md`.

- **Tile** — the stat block (micro label, mono value, sub — no icon square).
  Near views only; Overview uses panels instead.
- **Panel** (Overview only) — hairline-framed column flanking the truck, titled
  INPUT or OUTPUT: micro title, iconed hero label, mono hero value, then rows
  with label left / value right. Both panels read left-aligned. Shore lives on
  INPUT (it charges the rig). Dims (`--muted`) when carrying nothing.
- **Value row** — label left, dotted leader, mono value right; a row with
  nothing to say dims and says it in words ("off", "no sun") set in `--sans`.
  The near-view echo of the Overview panel rows.
- **Bank bar** — the marquee's segmented charge bar, reused at arm's-length
  size (`.cellwrap.sm`) as each unit card's hero. Ring gauges are retired:
  one charge language console-wide.
- **Pack table** — per-pack charge / flow / health on the Units view; the one
  home for pack telemetry (the old packs row, System diagnostics and the
  extra-battery card all collapsed into it).
- **Card** — `--surface`, 1px `--line`, `--r`, `--e1`. Cards never nest, and
  never wear a colored edge-stripe — unit identity is the 11px swatch in the
  title. On Overview the only card-like objects are the two stage panels and
  the unit frames drawn on the truck itself.
- **Row** — a control row: label + hint on the left (max 46ch), control on the right in a
  fixed 220px column. Never label-far-left / control-far-right across a full-width card.
- **Chip** — status pill, `--disp`, uppercase, 12px.
- Every interactive element ships default / hover / focus-visible / active / disabled /
  pending. Focus is a 2px `--accent` ring at 2px offset, on everything.

## Refuse

- Text below 12px anywhere, or below 14px on Overview.
- Mono for prose.
- Glow on anything not currently energized.
- A control more than ~300px from the label that names it.
- Charts whose axis labels are smaller than the smallest UI label.
- Emoji or unicode glyphs as icons — icons are authored SVG, 1.7px stroke, round caps.
