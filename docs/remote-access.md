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

### Live setup (completed 2026-08-05)

Installed 1.102.2 via winget; this PC is `mikelaptop01` on the tailnet. The
dashboard is proxied with:

```
tailscale serve --bg 8642
```

which serves **https://mikelaptop01.tail3f48d1.ts.net/** (tailnet only) as a
proxy to `http://127.0.0.1:8642`. Required a one-time "enable Serve" approval
in the admin console (the CLI prints the link and waits). Verified end to end:
`/api/live` and the console page both return 200 over the tailnet URL.

Unattended mode is on (`tailscale set --unattended`) so the proxy survives
logout/reboot. To turn the proxy off: `tailscale serve --https=443 off`.

**Never use `tailscale funnel`.** Funnel publishes to the public internet, which
is exactly the outcome this document exists to avoid.

## Power

The host must stay awake or there's nothing to reach — and poller uptime is the
dataset (see CLAUDE.md). Verified 2026-08-05 on the active scheme:

- AC: sleep after = 0 (never) — correct
- Battery: sleep after = 3600 s (60 min)

The machine runs on inverter AC in the truck, so the AC setting governs.
