"""Exact-origin CSRF checks for ambient browser authentication."""

from __future__ import annotations

import os
from urllib.parse import SplitResult, urlsplit


CSRF_HEADER = "x-voicestudio-csrf"
CSRF_VALUE = "1"
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_FORWARDED_PROTO_HEADER = "x-forwarded-proto"


def effective_scheme(connection) -> str:
    """Scheme of the client-facing hop: the resolved scope, TLS-upgraded by proxy evidence.

    Behind a TLS-terminating proxy (Tailscale Serve — the flagship remote-GPU
    deployment in docs/remote-gpu.md — nginx, Caddy, ...) the browser talks
    ``https`` while the backend hop is plain ``http``. uvicorn's
    ProxyHeadersMiddleware (on by default in both launch paths: ``uvicorn.run``
    in backend/main.py and the Docker ``python -m uvicorn`` entrypoint) already
    rewrites the ASGI scope from ``X-Forwarded-Proto``, but only when the peer
    is in ``--forwarded-allow-ips`` (default: loopback). That covers Serve on
    bare metal, and we prefer that signal — the scope is consulted first — but
    it misses Docker (the proxy connects from the bridge gateway) and any other
    non-loopback proxy topology, so the header is honored here as well.

    Spoofing analysis — why honoring it never weakens a check: the upgrade is
    one-way. ``https``/``wss`` as the first forwarded value promotes ``http``
    to ``https``; every other value is ignored, so a forged header can never
    downgrade a genuine TLS hop. For the exact-origin comparison the host:port
    half of the tuple is untouched, a browser cannot attach X-Forwarded-Proto
    cross-site without a CORS preflight this API never grants, and a
    non-browser client able to forge the header can already forge Origin
    itself — it gains nothing. For cookies the upgrade can only ADD the Secure
    flag (a Secure cookie set over plain http is simply dropped by the
    browser — the spoofer only breaks their own session), never strip it.
    """
    url = getattr(connection, "url", None)
    scheme = getattr(url, "scheme", None)
    if not scheme:
        scope = getattr(connection, "scope", None)
        scheme = scope.get("scheme", "http") if isinstance(scope, dict) else "http"
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    if scheme != "https":
        headers = getattr(connection, "headers", None) or {}
        forwarded = (
            headers.get(_FORWARDED_PROTO_HEADER, "") if hasattr(headers, "get") else ""
        )
        if forwarded.split(",")[0].strip().lower() in {"https", "wss"}:
            scheme = "https"
    return scheme


def _origin_tuple(value: str | None) -> tuple[str, str, int | None] | None:
    if not value or value == "null":
        return None
    try:
        parsed: SplitResult = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "app"}:
        return None
    if port is None:
        if scheme == "http":
            port = 80
        elif scheme == "https":
            port = 443
    return scheme, parsed.hostname.lower(), port


# Electron serves the packaged renderer from app://voicestudio. The retired
# Tauri shell's origins are deliberately absent: final Tauri installs spawn the
# backend bundled with them, never this one.
DEFAULT_DESKTOP_ORIGINS = ("app://voicestudio",)

# The browser UI dev-server port. OMNIVOICE_UI_PORT is canonical, matching
# OMNIVOICE_PORT; VOICESTUDIO_UI_PORT is the name the Vite configs read before
# the two were unified and stays accepted as an alias. Mirrored in
# electron/electron.vite.config.ts and electron/vite.web.config.ts.
UI_PORT_ENV = "OMNIVOICE_UI_PORT"
UI_PORT_ENV_ALIASES = ("VOICESTUDIO_UI_PORT",)
DEFAULT_UI_PORT = 3901


def configured_ui_port() -> int | None:
    """The UI port named by the environment, or None when none is set.

    The first non-empty name wins; a malformed or out-of-range value falls
    back to the default port rather than disabling the allow-list.
    """
    for name in (UI_PORT_ENV, *UI_PORT_ENV_ALIASES):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            return DEFAULT_UI_PORT
        return port if 0 < port <= 65535 else DEFAULT_UI_PORT
    return None


def ui_port() -> int:
    """The UI dev-server port the default origin allow-list trusts."""
    return configured_ui_port() or DEFAULT_UI_PORT


def allowed_origin_values() -> list[str]:
    """Raw allow-list entries shared by CORS (main.py) and the CSRF checks."""
    port = ui_port()
    values = os.environ.get(
        "OMNIVOICE_ALLOWED_ORIGINS",
        f"http://localhost:{port},http://127.0.0.1:{port}," + ",".join(DEFAULT_DESKTOP_ORIGINS),
    ).split(",")
    return [value.strip() for value in values if value.strip()]


def configured_allowed_origins() -> frozenset[tuple[str, str, int | None]]:
    return frozenset(
        origin
        for value in allowed_origin_values()
        if (origin := _origin_tuple(value)) is not None
    )


# Response headers the browser client reads cross-origin. A header missing here
# is silently null to a renderer on another origin (the web UI, a remote
# backend), so every non-safelisted header the client reads must be listed.
# tests/test_cors_exposed_headers.py scans the renderer to keep this complete.
CORS_EXPOSED_HEADERS = (
    "Content-Disposition",
    "X-Audio-Id",
    "X-Audio-Path",
    "X-Audio-Duration",
    "X-Gen-Time",
    "X-Seed",
    "X-OmniVoice-Routing",
    "X-OmniVoice-Routing-Reason",
    "X-OmniVoice-Dropped-Chunks",
    "X-OmniVoice-Dropped-Text",
    "X-Clean-Filename",
)


def _destination_origin(connection) -> tuple[str, str, int | None] | None:
    scheme = effective_scheme(connection)
    url = getattr(connection, "url", None)
    netloc = getattr(url, "netloc", None)
    if netloc:
        return _origin_tuple(f"{scheme}://{netloc}")
    scope = getattr(connection, "scope", None)
    headers = getattr(connection, "headers", None) or {}
    if not isinstance(scope, dict):
        return None
    host = headers.get("host", "") if hasattr(headers, "get") else ""
    return _origin_tuple(f"{scheme}://{host}")


def origin_allowed(connection) -> bool:
    headers = getattr(connection, "headers", None) or {}
    origin_value = headers.get("origin", "") if hasattr(headers, "get") else ""
    presented = _origin_tuple(origin_value)
    if presented is None:
        return False
    return presented == _destination_origin(connection) or presented in configured_allowed_origins()


def cookie_csrf_allowed(connection, *, side_effectful_get: bool = False) -> bool:
    headers = getattr(connection, "headers", None) or {}
    marker = headers.get(CSRF_HEADER, "") if hasattr(headers, "get") else ""
    if marker != CSRF_VALUE or not origin_allowed(connection):
        return False
    method = getattr(connection, "method", None)
    if method is None:
        scope = getattr(connection, "scope", None)
        method = scope.get("method", "GET") if isinstance(scope, dict) else "GET"
    method = str(method).upper()
    if side_effectful_get or method in SAFE_HTTP_METHODS:
        fetch_site = headers.get("sec-fetch-site", "") if hasattr(headers, "get") else ""
        return fetch_site == "same-origin"
    return True
