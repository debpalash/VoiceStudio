# Sharing & Remote Access

VoiceStudio runs **local-only by default** — the backend binds to `127.0.0.1` and nothing is reachable from other machines. When you want to use the *same running instance* (same loaded model, same projects and jobs) from another device, you have two opt-in paths. Neither restarts the backend or interrupts work in progress.

## LAN sharing (same Wi-Fi / Ethernet)

For another device on the same network — e.g. opening the web UI on your phone or a second laptop.

1. In the footer, click the **Local** pill → confirm **Network**.
2. A panel appears listing every reachable address of this machine (`http://<ip>:<port>`), each with:
   - a **QR code** — scan it from a phone/tablet to open the UI pre-authenticated,
   - **copy** and **open-in-browser** buttons,
   - the **access PIN**.
3. On the other device, scan the QR (or open the URL and enter the PIN when prompted).
4. Click **Stop sharing** (or flip back to **Local**) to close the network socket again.

You can also drive this from **Settings → Sharing & Remote Access**.

Desktop installers include the web interface used by the LAN address; another
device does not need VoiceStudio installed and the host does not need a source
checkout or a separate frontend development server. The backend serves the web
build shipped inside the running app (`OMNIVOICE_FRONTEND_DIST`), so after an
update LAN devices get the matching interface with no extra step.

**"Web interface unavailable" on the other device:** the backend is running
but has no web build to serve. Desktop builds before the fix for #2599 had this
problem on every OS; update to the latest release. In a source checkout run
`bun run build:web` and restart the backend. Another device is never redirected
to `localhost`, because on that device it is not VoiceStudio.

### How the PIN works
- A fresh 6-digit PIN is generated each time you enable sharing; it is never written to disk.
- The QR encodes the PIN (`…/?pin=######`) so scanning connects in one step. Typing the bare URL instead prompts for the PIN.
- Requests from other devices must present the PIN (sent automatically once entered/scanned); requests from this machine never need it.

### Security model
- **Loopback-only is the default on every launch** — you must explicitly enable sharing each session; it never auto-exposes.
- When sharing is off, **nothing is bound** to the network interface (the port is closed, not merely firewalled).
- The control surface and all `/system/*` endpoints are **loopback-only** — a device on the LAN cannot enable sharing, read the PIN, or change settings, even while sharing is on.
- The LAN path is plain **HTTP**. For encryption / access from outside your LAN, use Tailscale (below).

## Tailscale (private remote access, from anywhere)

If you have [Tailscale](https://tailscale.com/download) installed and signed in, you can reach VoiceStudio from any of your devices over your private tailnet — identity-gated, with **no open ports and no PIN** (Tailscale handles identity, and the WireGuard tunnel encrypts the transport).

1. **Settings → Sharing & Remote Access → Tailscale.**
2. If Tailscale isn't detected, an **Install Tailscale** link is shown.
3. Otherwise, **Enable** publishes this backend over your tailnet via `tailscale serve`. The panel shows your `<machine>.<tailnet>` URL with copy / open / QR.
   - By default this serves over **HTTP** on the tailnet (`tailscale serve --http=80`) — which works on any tailnet, because the WireGuard tunnel already encrypts the traffic. No certificate needed.
   - If your tailnet has **HTTPS Certificates** enabled (admin console → DNS → *HTTPS Certificates*), it serves over **HTTPS** instead and you get an `https://…ts.net` URL. (Forcing HTTPS without that feature is what produces the `error enabling https feature: 404` — so we detect it and fall back to HTTP automatically.)
4. Open that URL from any device signed in to the same tailnet. **Disable** runs `tailscale serve reset`.

Tailscale proxies the loopback backend directly, so — like LAN sharing — it never restarts the backend or drops the loaded model.

## Notes
- Both paths leave the running model and in-flight jobs **completely untouched**.
- Server deployments (Docker, or a source backend started with `OMNIVOICE_BIND_HOST=0.0.0.0`) manage their own networking; the in-app toggle is for the desktop app and is unaffected by these flows.
