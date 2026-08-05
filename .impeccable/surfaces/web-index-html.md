---
version: 1
slug: "web-index-html"
primary_target: "web/index.html"
related_targets: []
---

# Overview (home screen) — surface brief

Scope: the `#view-overview` section of `web/index.html` — the far view, an always-on
1920×1080 wall tablet read from 4–10 ft. Mode: Operate (glance sub-mode: no touch
targets; every other view is the near view).

Audience & job: the owner (and any guest) glancing across the box: how full, which
way, how long — then sun in, draw out, last 24 h. Nothing here may require walking
closer; 14px effective minimum everywhere on this surface.

Chosen direction (2026-08-04, seed 3b4fb214: **balance-wings**, candidate 6 of 7;
revised 2026-08-05 into **power-path** per Mike's brief + reference mockup):
three bands on the dark field:

1. **Marquee** — SOC hero + 20-segment glowing bar + state line in words
   ("CHARGING +205 W · full in 21h 23m · Wed 12:10 PM"). No icons; the state's
   color carries the state. ETA counts to the configured charge cap, names the day
   when it crosses midnight.
2. **Stage** — reads left to right as the power flows: framed **INPUT panel**
   (Solar in hero-W, per-array rows, today's harvest, Shore — shore is a charge
   source, so it lives on input), the neon NPR truck height-filling and centered,
   framed **OUTPUT panel** (Power out hero-W, A/C outlets, 12 V + USB, runtime).
   Panels are hairline-framed, both left-aligned, rows label-left / value-right,
   small authored stroke icons on powered rows. The truck carries the per-bank
   charge in unit frames — Unit B one battery box, Unit A two bonded boxes (main +
   Extra, joined by bus bars) — with combined SOC/kWh centered in each frame and
   "Delta Pro" / "Delta Pro + Extra Battery" centered under it. Solar drops, B→A
   transfer, and the out-flow all speak `--accent` blue.
3. **Ribbon** — full-width 24 h SOC trace under the wheels. No legend: each line
   is named at its end, Unit A `#6CC2F0` / Unit B `#2E7FAE`.

Memorable moment: the truck as the one bright object, with live power visibly
falling in from the roof and marching out through the rear wall.

Shedding order when the screen shrinks: ribbon first (history before live state),
then schematic sub-labels, then flow watt labels; panels drop below the truck under
1100px. Below 900×620 the page scrolls instead of clipping.

Refusals specific to this surface: no unit identity colors or key dots (truck blue +
status only), no icon tiles, no legend anywhere, no legend/header on the truck ("Rig
single-line" is dead), no mono for words, no glow on idle elements. The two stage
panels and the truck's unit frames are the only card-like objects.

Unresolved: balancer/emergency state has no Overview presence yet (the transfer
arrow and marquee state imply it); revisit if emergency mode ships more states.
