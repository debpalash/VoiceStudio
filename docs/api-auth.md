# Authenticating the local API

For an application backend consuming a pinned, private VoiceStudio deployment,
start with the [production private API pattern](production-private-api.md).

VoiceStudio's backend is **loopback-only and unauthenticated by default** — a
script running on the same machine as `http://localhost:3900` needs no key, no
PIN, no header. Everything on this page only matters once you reach the backend
from **another device** (a phone on your LAN, a laptop over Tailscale, a client
behind a reverse proxy).

There are two independent gates, both **inert until you turn them on**, plus one
env var that exempts trusted callers:

| Gate | Turn on with | Guards | Applies to |
|---|---|---|---|
| **Share PIN** | the in-app Network share toggle | casual LAN-share guests, one session | non-loopback **HTTP** |
| **API key** | `OMNIVOICE_API_KEY` env var on the backend | direct clients and first-party session bootstrap | non-loopback **HTTP + WebSocket** |
| **Trusted networks** | `OMNIVOICE_TRUSTED_NETWORKS` env var | *exempts* the two gates above | non-loopback **consumption** routes only |

Loopback traffic (`127.0.0.1`, `::1`, `localhost`) is **never** gated — local
tools keep working unchanged whichever gate is set.

> VoiceStudio separates **consumption** (TTS, dictation, voices) from
> **administration** (`/system/*`, `/api/settings/*` — RCE-class). The PIN and
> trusted networks are *consumption* credentials; the **admin surface is only
> ever reached from loopback or with the API key**. Host-path capabilities stay
> desktop-only even with a key (see [Admin routes](#admin-routes-and-server-mode)).

> Both gates can be active at once. The PIN and the API key are independent; when
> both are set, each is checked on the paths it covers. Session exchange validates
> the master key before the PIN gate so the UI can bootstrap safely; ordinary HTTP
> requests still require the PIN afterward, and the UI prompts for it next.

---

## Share PIN

The PIN is the lightweight, in-app path: flip on **Network** sharing (footer
**Local** pill → **Network**, or **Settings → Sharing & Remote Access**) and the
app generates a fresh **6-digit PIN** for that session. It is regenerated every
time you enable sharing and is **never written to disk**. See
[docs/sharing.md](sharing.md) for the UI walkthrough.

While a PIN is set, every **non-loopback HTTP request** to an API route must
present it. Supply it any one of three ways:

| Where | How |
|---|---|
| Header | `X-OmniVoice-Pin: <pin>` |
| Query param | `?pin=<pin>` |
| Cookie | `ov_pin=<pin>` — the backend sets this automatically after the first valid PIN, so browser sessions only prove it once |

```bash
# From another device on the LAN — with the PIN
curl http://<host>:3900/v1/audio/voices \
  -H "X-OmniVoice-Pin: 123456"
```

A missing or wrong PIN returns:

```
HTTP/1.1 401 Unauthorized
{"detail": "PIN required"}
```

Notes on the PIN gate (`NetworkAccessMiddleware`, `backend/main.py`):

- It covers **HTTP only** — it does **not** gate WebSockets. The dictation
  WebSocket has its own guard (see below), and the PIN does **not** authorize
  it; use the API key or a trusted network for remote dictation.
- The SPA shell (`/`, `/index.html`, `/favicon*`, `/assets/*`, `/health`) is
  always served un-PIN'd so the PIN-prompt UI can load.
- It is a **consumption** credential: a valid PIN never unlocks the admin surface
  (it is 6 digits, brute-forceable). Admin needs loopback or the API key.
- It is completely inert when no PIN is set (the default, and every Docker
  deploy).

---

## API key

The API key is the backend's durable root credential for a GPU box, Docker
container, or reverse-proxied host. Direct API clients may send it on each
request. The first-party browser/Tauri UI instead exchanges it once for a
short-lived administrator session and never stores the master. Set it on the
**backend** process:

```bash
# Generate a strong key and start the backend with it
export OMNIVOICE_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
uv run uvicorn backend.main:app --host 0.0.0.0 --port 3900
# Docker: pass -e OMNIVOICE_API_KEY=…
```

While `OMNIVOICE_API_KEY` is set, every **non-loopback HTTP and WebSocket**
request must present an accepted credential. SPA shell paths remain public;
`POST /api/auth/session` passes through the middleware only so its route can
validate the master and perform the one-time exchange. Direct-client
compatibility accepts:

| Where | How |
|---|---|
| Header | `Authorization: Bearer <key>` — **preferred** for scripts and SDKs |
| Legacy cookie | `ov_key=<key>` — accepted only for compatibility and migrated by the first-party UI; the backend no longer creates it |
| Legacy query param | `?api_key=<key>` — compatibility only. **A key in a URL leaks into proxy/access logs and browser history** |

```bash
# Prefer an encrypted transport (Tailscale Serve / TLS) for a real key; plain
# http:// on an untrusted network exposes the Bearer token on the wire.
curl https://gpu-box:3900/v1/audio/speech \
  -H "Authorization: Bearer $OMNIVOICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","voice":"alloy","input":"Hello from a keyed backend.","response_format":"wav"}' \
  --output speech.wav
```

```python
# The OpenAI SDK sends the key as a Bearer token automatically
from openai import OpenAI

client = OpenAI(
    base_url="https://gpu-box:3900/v1",
    api_key="<your OMNIVOICE_API_KEY>",   # must match OMNIVOICE_API_KEY on the backend
)                                          # (any string works ONLY when no key is set — the loopback default)
audio = client.audio.speech.create(
    model="tts-1", voice="alloy", input="Hello from a keyed backend.",
    response_format="wav",
)
audio.write_to_file("speech.wav")
```

A missing or wrong key returns:

```
HTTP/1.1 401 Unauthorized
{"detail": "API key required"}
```

On a **WebSocket**, a missing or wrong key rejects the handshake with close
code **1008** (policy violation) instead of a JSON body.

Notes on the API-key gate (`BearerKeyMiddleware`, `backend/main.py`):

- The key is compared in **constant time** and is **never logged**.
- The backend never copies the master into a response cookie. Browser clients
  receive only `ov_session`, an opaque, HttpOnly, SameSite=Strict credential.
- The SPA shell paths bypass the gate on **HTTP** so a remote UI can load and
  show what's wrong; WebSockets have no such exemption.
- **Plain HTTP is sniffable** — a Bearer key over `http://` on a hostile
  network can be read off the wire. Use Tailscale (WireGuard) or TLS for
  anything beyond a fully trusted LAN. See
  [docs/remote-gpu.md](remote-gpu.md) for the full remote-backend setup.

### First-party administrator sessions

The bundled UI uses a narrower protocol:

1. `POST /api/auth/session` receives the master in an `Authorization` header
   exactly once and selects `{"transport":"cookie"}` for exact same-origin
   browsers or `{"transport":"bearer"}` for Tauri/cross-origin clients.
2. Cookie transport returns `204` and sets `ov_session` as HttpOnly,
   SameSite=Strict, path `/`, with an eight-hour maximum lifetime. Bearer
   transport returns an opaque `ovs_admin_session_…` value which the UI keeps
   in **sessionStorage only**, bound to the exact backend base URL. Bearer JSON
   responses include both `expires_at` and a bounded `expires_in`; the UI uses
   the relative lifetime when available so clock skew between a remote GPU host
   and the browser cannot reject a valid session. `expires_at` remains for
   backward compatibility with older clients and servers.
3. `DELETE /api/auth/session` revokes the session. Removing or rotating
   `OMNIVOICE_API_KEY`, backend restart, explicit logout, and the eight-hour
   deadline also invalidate it.

The master is never written to localStorage/sessionStorage, never returned by
the backend, and never placed in a WebSocket URL. Legacy `ov_api_key` browser
storage is deleted before migration waits on the network. All auth responses,
including errors, carry `Cache-Control: no-store`.

Failed session exchanges are limited per client to ten attempts in a rolling
60-second window and then return `429` with `Retry-After`. A correct master key
is always evaluated and clears the failure window, so an attacker cannot lock
an operator out by deliberately exhausting the limit.

Cookie-authenticated mutations require both an exact allowed `Origin` and
`X-VoiceStudio-CSRF: 1`. Side-effectful GET actions additionally require the
browser's `Sec-Fetch-Site: same-origin`. Bearer/header clients are not subject
to the ambient-cookie CSRF check.

---

## Dictation WebSocket

The live-dictation stream at **`ws://<host>:3900/ws/transcribe`** carries its
own inline guard (`backend/api/routers/capture_ws.py`) *in addition to* the
API-key middleware. A non-loopback client reaches it only if it is **either**:

- on a [trusted network](#trusted-networks) (`is_local_host` passes), **or**
- presenting a direct-client **API key** in `Authorization`, or through a
  legacy `ov_key`/`?api_key=` transport.

```
ws://gpu-box:3900/ws/transcribe?api_key=<key>
```

That URL form is retained for non-browser compatibility only. The first-party
UI never constructs it. A bearer administrator session first calls
`POST /api/auth/ws-ticket` and puts only the returned `ws_ticket` in the URL.
Tickets are scoped to one of `/ws/transcribe`, `/ws/events` or `/ws/tts` (the
live dub preview stream), expire after 30 seconds,
return the same bounded `expires_in`/`expires_at` pair, and are consumed
atomically at most once. Same-origin UI WebSockets use the
HttpOnly session cookie and must pass exact `Origin` validation; `null`, missing,
and lookalike origins are rejected.

The **share PIN does not authorize dictation** — the PIN gate is HTTP-only, and
the dictation guard checks only the API key (or trusted-network membership). A
LAN guest who has only entered a PIN can use the HTTP API but **not** live
dictation. When neither the API key nor a trusted network applies, the handshake
is closed with code **1008** and reason `loopback origin required`.

---

## Trusted networks

`OMNIVOICE_TRUSTED_NETWORKS` is a comma-separated list of **CIDR ranges** whose
clients are treated as loopback-trusted by the **consumption** gates — so a
reverse proxy or a trusted LAN/Tailnet can reach the API **without a PIN or key**
(useful when a proxy strips the `Authorization` header).

```bash
export OMNIVOICE_TRUSTED_NETWORKS="192.168.1.0/24,10.0.0.0/8"
```

What it exempts vs. what it does not:

- **Exempts** (via `is_local_host`, `backend/api/dependencies.py`): the share
  PIN gate, the API-key gate, and the dictation WebSocket guard. Clients in a
  listed range need no PIN or key for these **consumption** routes.
- **Never exempts admin.** `/system/*` and `/api/settings/*` are the RCE-class
  admin surface; trusted-network membership is a *consumption* exemption and
  does not reach them — even under `OMNIVOICE_SERVER_MODE=1`. See
  [Admin routes](#admin-routes-and-server-mode).

Details: malformed CIDR entries are silently ignored (a bad entry never wedges
the gate); IPv4-mapped IPv6 addresses (`::ffff:192.168.1.5`) from dual-stack
proxies are unwrapped so they match IPv4 CIDRs; the value is read at request
time, so in production **restart the backend** to apply a change. Default empty
— no change to the strict loopback default.

---

## Admin routes and server mode

Admin routes — `/system/*` (including `set-env`, **RCE-class**),
`/api/settings/*`, engine selection/install/uninstall, media tools, MCP
bindings, pronunciation settings, and remote-worker management — sit on a
stricter gate (`require_admin`, `backend/api/dependencies.py`) than consumption.
On the desktop build they are **true-loopback-only**: no PIN, key, or trusted
network reaches them from another machine.

In **server mode** (`OMNIVOICE_SERVER_MODE=1`, the Docker image) the loopback
origin is unenforceable — NAT rewrites the source and even a
`-p 127.0.0.1:3900:3900` mapping looks non-loopback — so the true-loopback
requirement is dropped (issue #261, else the operator is 403'd out of their own
`/system/*`). It is replaced by a **credential rule**, not removed:

- **No credential configured** (neither API key nor share PIN) → read-only
  admin discovery remains available for the bare Docker bootstrap flow, but
  `POST`/`PUT`/`PATCH`/`DELETE` requests are denied. Side-effectful GET actions
  are denied too: engine health may start a sidecar, deep diagnostics may load
  a model, and LLM provider discovery makes a request with the saved provider
  credential. Set `OMNIVOICE_API_KEY` before changing settings or triggering
  those actions remotely.
- **An API key is configured** → admin requires that **API key** (direct-client
  `Authorization` / legacy query or cookie), a valid short-lived administrator
  session, or genuine loopback. The **6-digit share PIN does not gate admin**
  (it is brute-forceable), and trusted-network membership never does either. A
  **PIN-only** server-mode deployment therefore keeps admin routes loopback-only;
  remote admin starts from the long API key.

Managed sidecar installation remains true-loopback-only even with an API key.
Its installer fetches mutable source and creates an editable environment, so it
must be run directly on that machine until the source supply chain is pinned.

Host paths are never selected through HTTP. The native Tauri process validates
model-cache and export destinations plus custom FFmpeg/FFprobe binaries, writes
a private one-shot capability, and only that opaque authorization reaches the
backend. `/export` therefore accepts an `authorization` token, never a
`destination_path`; revealing an arbitrary exported path runs in the native
process, while the HTTP fallback is limited to the server-owned data root.
`/system/set-env` does not accept executable-path keys at all. Server mode and
an API key do not weaken that native boundary.

This is the fix for a real escalation (#1213): before it, server mode made the
admin gate a no-op, so with an API key set *and* a trusted CIDR configured, a LAN
client in that CIDR could `POST /system/set-env` — RCE-class — with **no
credential at all**, because the API-key middleware waved it through as
`is_local_host`. Now the admin gate is independent of the consumption exemptions.

---

## Browsers from another origin (CORS)

Everything above gates *authentication*. A **browser** frontend served from a
different origin than the backend hits a separate wall first: CORS. The
backend's allow-list defaults to loopback + Tauri origins only
(`http://localhost:<ui-port>`, `http://127.0.0.1:<ui-port>`,
`tauri://localhost`, `http://tauri.localhost`), so opening a dev/source UI via
a LAN IP (e.g. `http://192.168.1.159:3901` talking to `…:3900`) blocks every
request with *"Missing Header: Access-Control-Allow-Origin"* — regardless of
`OMNIVOICE_SERVER_MODE` or `OMNIVOICE_TRUSTED_NETWORKS`, neither of which
touches CORS (#1348).

Add the exact origin the browser shows in its address bar:

```bash
export OMNIVOICE_ALLOWED_ORIGINS="http://192.168.1.159:3901,http://localhost:3901,http://127.0.0.1:3901,tauri://localhost,http://tauri.localhost"
```

Each entry must be a bare origin — `scheme://host:port`, exactly what the
browser sends in its `Origin` header — with no path and no trailing slash
(`http://192.168.1.159:3901/` would never match). The variable **replaces**
the default list, so restate the loopback/Tauri origins alongside your own. (The in-app LAN share and Tailscale flows in
[docs/sharing.md](sharing.md) don't need this — they serve UI and API from the
same origin.) If you only moved the Vite dev server's port, set
`OMNIVOICE_UI_PORT` instead and the default list follows it.

CORS wraps both authentication gates: credentialless browser preflights are
answered before PIN/API-key enforcement, and gate-generated `401` responses
retain CORS headers so the UI can read the actual failure and prompt for the
right credential.

TLS-terminating proxies must establish the effective scheme at the ASGI server
boundary. Uvicorn's proxy-header handling trusts loopback by default, which
covers Tailscale Serve; a custom proxy on another address must be listed with
`--forwarded-allow-ips=<proxy-ip>` (and proxy headers must remain enabled).
VoiceStudio deliberately does not trust a raw `X-Forwarded-Proto` header inside
the application: once Uvicorn accepts a trusted proxy, the resolved ASGI scheme
drives exact-Origin checks and the session cookie's `Secure` attribute.
For a public path prefix such as `/studio`, either strip that prefix before
forwarding or configure the ASGI `root_path` to the same value. WebSocket ticket
validation removes only that trusted, configured prefix; it never accepts an
arbitrary path merely because it ends in `/ws/events`, `/ws/transcribe` or
`/ws/tts`.

## Status codes

| Code | Meaning | What to do |
|---|---|---|
| **401** | Consumption auth failed — `{"detail": "PIN required"}` or `{"detail": "API key required"}`. | Supply the PIN / key (header, cookie, or query param above). A WebSocket surfaces this as close code **1008**. |
| **403** | Authorization failed: loopback/native access was required, cookie Origin/CSRF validation failed, a server-mode mutation lacked an admin credential, or a native path capability was invalid/expired. | A PIN cannot grant admin or filesystem access. Re-authenticate the UI; scripts should use the API-key header; run native operations from the desktop app. The admin gate names the key only when one can satisfy it: server mode with `OMNIVOICE_API_KEY` configured answers `{"detail": "loopback origin or admin API key required"}` (the bundled UI routes it to the API-key login form); PIN-only/no-key server mode and the desktop build answer `{"detail": "loopback origin required"}` (only loopback can satisfy the gate). |
| **429** | A failed administrator-session exchange exceeded its per-client limit, the GPU pool is saturated, or a model download is rate-limited. Ships with `Retry-After`; workload throttles also carry `X-VoiceStudio-Retryable: true`. | Back off for `Retry-After` seconds. For authentication, verify the master before retrying; a correct master is never locked out. |

---

## See also

- [docs/remote-gpu.md](remote-gpu.md) — end-to-end remote-backend setup over
  Tailscale, with the API key.
- [docs/sharing.md](sharing.md) — the in-app LAN share + PIN flow.
- [docs/agentic-voice.md](agentic-voice.md) — pointing OpenAI-compatible agent
  frameworks at VoiceStudio.
- [docs/mcp.md](mcp.md) — the MCP server for AI agents.
