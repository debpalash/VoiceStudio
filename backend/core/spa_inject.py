"""Runtime API-base injection for the served SPA (Docker / reverse-proxy).

`VITE_*` vars are inlined at build time, so a prebuilt image cannot take an
API-base override from `docker run -e`. When `OMNIVOICE_PUBLIC_API_BASE` is set,
the backend injects it into `index.html` as `window.__OMNIVOICE_API_BASE__`,
which the SPA's API resolver reads first. These helpers are pure so they can be
unit-tested without booting the app.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
from urllib.parse import urlsplit


#: Absolute path of the web UI build to serve at "/". The packaged desktop app
#: sets it to the build inside its own resources, so the UI a LAN device gets
#: always matches the running app version (#2599). Unset → beside the backend.
FRONTEND_DIST_ENV = "OMNIVOICE_FRONTEND_DIST"


def frontend_dist_dir() -> str:
    """The built SPA served at "/" (desktop, Docker, source builds).

    ``OMNIVOICE_FRONTEND_DIST`` wins; otherwise resolved relative to the
    backend package root. One seam so tests can serve a real SPA mount
    without building it.
    """
    override = os.environ.get(FRONTEND_DIST_ENV, "").strip()
    if override:
        return override
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(backend_root, "..", "frontend", "dist")


def frontend_available(path: str) -> bool:
    """A web UI can be served only when the build has its entry document."""
    return os.path.isfile(os.path.join(path, "index.html"))


_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def dev_ui_redirect(client_host: str | None, host_header: str, ui_port: int) -> str | None:
    """Where "/" may redirect when this backend has no web UI build, or None.

    Only a browser on this machine that addressed the backend by a loopback
    name is sent to the local dev UI. Anyone else (a LAN device, or a remote
    client relayed through a loopback proxy such as ``tailscale serve``) would
    be sent to *their own* localhost, which is never VoiceStudio (#2599).
    """
    if client_host not in _LOOPBACK_NAMES:
        return None
    try:
        hostname = urlsplit(f"//{host_header}").hostname
    except ValueError:
        return None
    if hostname not in _LOOPBACK_NAMES:
        return None
    return f"http://localhost:{ui_port}"


WEB_UI_MISSING = (
    "VoiceStudio is running, but this installation has no web interface to "
    "serve to other devices. Update the VoiceStudio desktop app to the latest "
    "version; in a source checkout run `bun run build:web` and restart the "
    "backend. The API itself is available."
)


def web_ui_missing_body(accept: str) -> tuple[str, str]:
    """(media type, body) explaining the missing web UI, HTML for browsers."""
    if "text/html" in (accept or ""):
        message = html.escape(WEB_UI_MISSING)
        return "text/html", (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>VoiceStudio web interface unavailable</title></head>"
            "<body><h1>Web interface unavailable</h1>"
            f"<p>{message}</p></body></html>"
        )
    return "application/json", json.dumps({"detail": WEB_UI_MISSING})

# Operator-controlled value, but validate to a plain http(s) URL with no
# whitespace, quotes, or angle brackets so it can never break out of the
# injected <script> element.
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$")


def is_valid_public_api_base(value: str) -> bool:
    """True if `value` is a safe http(s) URL we can inject into HTML."""
    return bool(value) and bool(_URL_RE.match(value))


def inject_api_base(html_doc: str, api_base: str) -> str:
    """Insert `window.__OMNIVOICE_API_BASE__` right after the SPA's <head>.

    `api_base` is JSON-encoded (neutralising quotes); the caller is expected to
    have validated it via `is_valid_public_api_base` first. Falls back to
    prepending the snippet if the document has no <head>.
    """
    script = f"window.__OMNIVOICE_API_BASE__={json.dumps(api_base)};"
    snippet = f"<script>{script}</script>"
    # The Electron renderer ships a strict CSP. Authorize only this exact,
    # backend-generated assignment instead of weakening script-src globally.
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    html_doc = html_doc.replace(
        "script-src 'self'",
        f"script-src 'self' 'sha256-{digest}'",
        1,
    )
    parsed = urlsplit(api_base)
    http_origin = f"{parsed.scheme}://{parsed.netloc}"
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    html_doc = html_doc.replace(
        "connect-src 'self'",
        f"connect-src 'self' {http_origin} {ws_scheme}://{parsed.netloc}",
        1,
    )
    if "<head>" in html_doc:
        return html_doc.replace("<head>", "<head>" + snippet, 1)
    return snippet + html_doc
