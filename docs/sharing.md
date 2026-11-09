# Sharing & Remote Access

VoiceStudio runs **local-only by default** — the backend binds to `127.0.0.1` and nothing is reachable from other machines. When you want to use the *same running instance* (same loaded model, same projects and jobs) from another device, you have two opt-in paths. Neither restarts the backend or interrupts work in progress.

## LAN sharing (same Wi-Fi / Ethernet)

For another device on the same network — e.g. opening the web UI on your phone or a second laptop.

1. In the footer, click the **Local** pill → confirm **Network**.
2. A panel appears listing every reachable address of this machine (`http://<ip>:<port>`), each with:
   - a **QR code** — scan it from a phone/tablet to open the UI pre-authenticated,
   - **copy** and **open-in-browser** buttons,
   - the **access PIN**.
3. On the other device, scan the QR (or open the URL and enter the PIN when prompted). Use the IP address the panel shows: a router DNS name such as `mypc.lan` is refused unless it is listed in `OMNIVOICE_ALLOWED_HOSTS` (see [host names](api-auth.md#requests-from-other-websites-and-host-names)).
4. Click **Stop sharing** (or flip back to **Local**) to close the network socket again.

You can also drive this from **Settings → Sharing & Remote Access**.

The address list is ordered best first: the address of your active Wi-Fi or
Ethernet connection (even when a VPN owns the default route), then other physical
adapters, then virtual ones (Docker,
WSL/Hyper-V, VirtualBox/VMware), then VPN and Tailscale addresses. Adapters
that are down are left out.

### "Could not connect" from the other device

While sharing is on, **Settings → Sharing & Remote Access** shows a self-check:
the address and port the share listener is bound to (`0.0.0.0:<port>`, every
interface) and, for each listed address, whether it answers from this
computer. Use the refresh button after changing networks; the URLs and QR
codes follow the new address.

- **An address shows a warning:** the listener is not answering on it. Stop
  and restart sharing; if it persists, another program holds the port, so set
  a different **LAN share port** under **Ports**.
- **Every address answers but the other device still cannot connect:** the
  connection is blocked between the two devices. The self-check runs on this
  computer, so it cannot see these:
  - **Different network.** Both devices must be on the same Wi-Fi or LAN.
    Guest networks and routers with *client* or *AP isolation* block
    device-to-device traffic. A phone on mobile data cannot reach a LAN
    address.
  - **Wrong address.** Use the first address in the list. Addresses such as
    `172.17.x.x` (Docker), `172.2x.x.x` (WSL/Hyper-V), `192.168.56.x`
    (VirtualBox) or a VPN address are only reachable from this computer.
  - **Windows Defender Firewall.** The first time sharing starts, Windows asks
    whether to allow the VoiceStudio backend (`python.exe` from the
    VoiceStudio install) on networks. Allow it on **Private networks**, and
    set your Wi-Fi to **Private** (Settings → Network & internet → Wi-Fi →
    your network → *Private network*); on a Public network, Windows blocks
    inbound connections. If you dismissed the prompt, open *Windows Security →
    Firewall & network protection → Allow an app through firewall* and tick
    **Private** for that `python.exe`.
  - **macOS firewall.** If System Settings → Network → Firewall is on, allow
    incoming connections for the VoiceStudio backend when macOS asks, or add it
    under **Options**. *Block all incoming connections* must be off.
  - **Linux firewall.** Allow the share port, for example
    `sudo ufw allow 3901/tcp` or
    `sudo firewall-cmd --add-port=3901/tcp`.
  - **VPN on either device.** A VPN with *block local network* (or kill
    switch) settings stops LAN traffic. Allow local network access or turn
    the VPN off.
- **The page opens but asks for a PIN:** enter the PIN from the panel, or scan
  the QR code, which includes it. A new PIN is made every time sharing starts.

For access from outside your network, use Tailscale (below) instead of opening
ports on your router.

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
- Requests from other devices must present the PIN (sent automatically once entered/scanned); requests from this machine never need it. In Docker the host's own browser does not count as this machine and needs the PIN unless you opt in with `OMNIVOICE_TRUST_HOST_LOOPBACK=1` (see [Docker install](install/docker.md#trust-host-loopback)).

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
   - Tailscale serve settings are shared by every app on the machine, so VoiceStudio does not replace another app's share. If another service occupies the default port (80 for HTTP or 443 for HTTPS), VoiceStudio tries port **10000** and includes it in the address, such as `http://<machine>.<tailnet>:10000`. If that port is also occupied, VoiceStudio leaves the configuration unchanged and reports an error.
4. Open the displayed URL on any device signed in to the same tailnet. **Disable** removes only serve handlers that target this VoiceStudio backend, using `tailscale serve --http=<port> off`; it preserves every other app's share and never runs `tailscale serve reset`.

Tailscale proxies the loopback backend directly, so — like LAN sharing — it never restarts the backend or drops the loaded model.

## Notes
- Both paths leave the running model and in-flight jobs **completely untouched**.
- Open shared or remote VoiceStudio by IP address, `localhost`, this machine's host name, its Tailscale name or a container host alias such as `host.docker.internal`. Any other host name (for example a reverse proxy domain) needs a valid API key or admin session, or an `OMNIVOICE_ALLOWED_HOSTS` entry; see [API authentication](api-auth.md#requests-from-other-websites-and-host-names).
- Server deployments (Docker, or a source backend started with `OMNIVOICE_BIND_HOST=0.0.0.0`) manage their own networking; the in-app toggle is for the desktop app and is unaffected by these flows.
