# Pod Power Console — product context

*Written 2026-08-03 from Mike's brief. Items marked (assumed) were inferred, not confirmed.*

## What it is

The monitoring and control console for a two-unit EcoFlow Delta Pro power system in an
off-grid Isuzu NPR box-truck conversion. It is the only interface to the rig's electrical
system: it shows where the power is, where it is going, and lets the owner change it.

Not a generic energy dashboard. It knows this specific rig: two named units wired a
specific way, one A/C that dominates the load, solar that is shade-limited by necessity.

## Audience

One primary user — the rig's owner-operator. Technical, built the system, knows what
MPPT and X-Boost mean. Wants density and truth, not reassurance. (assumed) Guests or
passengers may glance at the home screen; they should be able to read "are we OK" from it
without knowing anything.

If the project is ever productized, the audience becomes van/skoolie/overland builders —
same technical literacy, same viewing scene.

## The two viewing scenes

This is the defining constraint. The console is used at two distances and must serve both.

1. **Home / Overview — the far view.** Destined for an always-on tablet mounted in the
   truck. Read from across the box at 6–10 ft, in passing, often in bright daylight or at
   night. Must answer *how much power do I have, is it going up or down, is anything
   wrong* in under a second with no reading. Nothing on this screen may require walking
   closer.
2. **Everything else — the near view.** Same tablet, held or touched at arm's length.
   Units, Control, Power, Energy, System. Density is welcome here. Touch targets must be
   finger-sized; text must be comfortable at 18–24 in, not across a room.

Desktop browser is a secondary but real surface (development, deeper analysis).

## Jobs to be done

- Glance: is the bank charging or draining, and how long do I have?
- Decide: should I run the A/C now, or wait for sun?
- Act: flip an inverter, cap the charge rate, set the auto-balance thresholds.
- Diagnose: why did the bank drop overnight, what did solar actually make today.

## Non-negotiables

- **The glowing blue Isuzu NPR outline stays.** It is the identity of the product and the
  centerpiece of the home screen. Everything else is built around it.
- The large segmented charging bar at the top of Home stays (owner explicitly likes it).
- No functional regressions. Every control, chart, and readout that works must keep working.
- Honest data. The rig has no historical API; charts only show what the poller logged.
  Gaps and staleness are surfaced, never smoothed over.

## Constraints

- Off-grid: the tablet may have no internet. Assets must be served locally.
- Single-file frontend (`web/index.html`), no build step, no framework, vanilla JS + SVG.
- Served by a small Flask app on `127.0.0.1:8642`.
