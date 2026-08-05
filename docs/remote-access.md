# Remote access to the dashboard

Goal: reach the console from a phone when away from the truck, without putting
it on the public internet.

## Why not Vercel / a public host

`POST /api/control` and `POST /api/balancer` have no authentication — they take
a unit/key/value and send the command straight to the hardware through the
EcoFlow *cloud* API. A public deployment would therefore genuinely reach the
truck: anyone with the URL could cut the A/C or drain a battery. The app binds
`127.0.0.1` on purpose.

Serverless is also the wrong shape for it regardless: the balancer is a 60 s
background thread, the single-instance lock is a bound port, and history is a
local CSV that a read-only ephemeral filesystem can't append to.

## Approach: Tailscale + `tailscale serve`

Tailscale is a private mesh — devices reach each other, nothing is published.
`tailscale serve` proxies from the tailnet to `localhost:8642`, so **the bind
address stays `127.0.0.1`**. That matters: it keeps the console off the local
LAN too, so a coffee-shop or campground wifi never sees it.

Security posture: the tailnet is the authentication. The app stays unauthenticated,
but only devices signed into your Tailscale account can route to it. If the
tailnet ever gains a device you don't control, that assumption breaks — add app
auth before sharing the tailnet.

### Setup (both steps need a human — UAC prompt, then browser login)

```
winget install --id Tailscale.Tailscale --source winget
```

Then sign in (opens a browser):

```
tailscale up
```

Install the Tailscale app on the phone and sign into the same account.

### Remaining step

With Tailscale up, proxy the dashboard onto the tailnet. Verify current syntax
with `tailscale serve --help` before running — the serve/funnel CLI has changed
shape across releases, so don't copy a remembered invocation.

**Never use `tailscale funnel`.** Funnel publishes to the public internet, which
is exactly the outcome this document exists to avoid.

## Power

The host must stay awake or there's nothing to reach — and poller uptime is the
dataset (see CLAUDE.md). Verified 2026-08-05 on the active scheme:

- AC: sleep after = 0 (never) — correct
- Battery: sleep after = 3600 s (60 min)

The machine runs on inverter AC in the truck, so the AC setting governs.
