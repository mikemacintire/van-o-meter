# AC-outlets-on indicator on the Overview truck (2026-08-18)

## What

Each unit frame on the Overview's truck schematic shows whether that unit's AC
outlets are energized. When a unit's AC output switch is on, its frame glows in
the truck's accent blue and the sub-line under the frame appends "· AC ON"
(e.g. "Delta Pro · AC ON"). When off — or when the switch state is unknown —
the frame stays exactly as it is today.

Chosen over two alternatives mocked in the visual companion (frame glow alone;
a small lit "AC" pill on the frame edge): the glow carries the signal at the
far view's 6–10 ft, the words explain it at arm's length, matching the
surface's "color carries the state, words say it" ethos. Mike picked the
semantics (switch state) and delegated the treatment choice.

## Semantics

- Driven by the AC output **switch** (`inv.cfgAcEnabled`, already served as
  `ac.out_enabled` per unit in `/api/status`), not by watts. The flow arrows
  already animate actual power movement; what was invisible was the switch —
  e.g. the balancer arming B's outlets before A starts pulling.
- Applies to both units. B's AC on effectively means "transfer armed"; A's AC
  on means the house outlets are live.
- `out_enabled` of `null`/missing renders as off: no glow, no word. Never show
  an indicator from data we don't have (the units flap on and off the cloud).

## Implementation (web/index.html only — no backend change)

- `bankBox()`: give the frame rect an id (`${id}box`) and append a
  `<tspan id="${id}ac" class="sch-ac">` to the `.sch-tiny` sub text.
- CSS: `.sch-box.ac-on { stroke: var(--accent); filter: drop-shadow(...) }`
  with a transition; `.sch-ac { fill: var(--accent) }`.
- `renderSchematic()`: per unit, `on = Number(u.ac.out_enabled) === 1` (only
  when `out_enabled != null`); toggle the rect class and set the tspan text to
  `" · AC ON"` or `""`.

## Surface-brief conformance

- Not a "glow on an idle element" (refusal list): the glow marks an energized
  output, the same rule that lights the flow arrows.
- Below 1100px `.sch-tiny` is already shed; the glow alone carries the signal
  there, consistent with the shedding order (words yield before state).
- Partially resolves the brief's open item on balancer visibility: a transfer
  being armed is now visible on B's frame before watts move.

## Testing

No JS test harness exists for the web console; verification is live: toggle a
unit's AC output from the Control view (or observe current live state) and
confirm the frame glow + word track `ac.out_enabled` on the Overview, and that
an offline unit (`data: null`) shows no indicator.
