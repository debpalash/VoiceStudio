"""Refuse browser requests that another website makes to the local API.

The API trusts the network position of loopback callers (the desktop app, the
CLI, MCP agents), and a browser on the same machine is a loopback caller too.
Two browser behaviours turn that trust into a hole unless the server checks
for them on every request, whatever the auth configuration:

* **Cross-site requests.** Any page can send a form POST, a ``no-cors`` fetch
  or a WebSocket handshake to ``http://127.0.0.1:3900``. CORS only hides the
  response; the request still runs. Browsers label such requests with an
  ``Origin`` header and, for navigations and subresources, with
  ``Sec-Fetch-Site``. State-changing requests and WebSocket handshakes that
  carry a foreign origin are refused here.
* **DNS rebinding.** A page whose hostname re-resolves to 127.0.0.1 becomes
  *same-origin* with the API under the attacker's hostname, so the origin
  check above passes. The ``Host`` header still names that hostname, so
  requests authorised only by network position must address the backend by
  an allowed host.

Clients that send neither ``Origin`` nor ``Sec-Fetch-Site`` — the Electron
main process and its ``app://`` proxy, the CLI, MCP clients, curl, scripts —
are not browsers acting for another site and pass unchanged.

Policy knobs (all optional; the defaults cover the desktop app, the dev
servers, LAN sharing by IP address and Tailscale MagicDNS names):

* ``OMNIVOICE_ALLOWED_ORIGINS`` — extra browser origins (shared with CORS).
* ``OMNIVOICE_ALLOWED_HOSTS`` — extra host names the backend may be addressed
  by (beyond the Docker/Podman host aliases), comma-separated; ``.example.com`` allows subdomains, ``*`` disables the
  host check (only behind a proxy that validates ``Host`` itself).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from starlette.requests import Request

from core.csrf import (
    DEFAULT_DESKTOP_ORIGINS,
    SAFE_HTTP_METHODS,
    _destination_origin,
    _origin_tuple,
    allowed_origin_values,
    configured_allowed_origins,
)
from core.user_env import DESKTOP_ENV_FILE_HINT

ALLOWED_HOSTS_ENV = "OMNIVOICE_ALLOWED_HOSTS"
_MCP_HOSTS_ENV = "OMNIVOICE_MCP_ALLOWED_HOSTS"
# MagicDNS names only resolve to tailnet addresses (or Tailscale's Funnel
# ingress), never to an address a third party chooses, so they cannot be used
# to rebind onto this machine. Allowed by default so `tailscale serve` works.
_DEFAULT_HOST_SUFFIXES = (".ts.net",)
# Names Docker Desktop and Podman give containers for reaching the host (the
# documented n8n / Open WebUI / SillyTavern setups). ``.internal`` is reserved
# for private use (ICANN, 2024), so no public DNS name can rebind to them.
_DEFAULT_HOSTS = frozenset({
    "host.docker.internal",
    "gateway.docker.internal",
    "host.containers.internal",
})
_CROSS_SITE_FETCH = frozenset({"cross-site", "same-site"})

CROSS_SITE_DETAIL = (
    "Request refused: it came from another website. The VoiceStudio API only "
    "accepts browser requests from its own interface. To use the API from "
    "another web app, add its origin to OMNIVOICE_ALLOWED_ORIGINS."
)
HOST_DETAIL = (
    "Request refused: VoiceStudio was addressed by an unrecognized host name. "
    "Open it via localhost or an IP address, send the API key, or add the "
    f"host name to OMNIVOICE_ALLOWED_HOSTS ({DESKTOP_ENV_FILE_HINT}) and "
    "restart VoiceStudio."
)


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _hostname(value: str) -> str | None:
    value = (value or "").strip()
    if not value or any(ch in value for ch in "/?#@\\ "):
        return None
    try:
        host = urlsplit(f"//{value}").hostname
    except ValueError:
        return None
    return host.rstrip(".").lower() if host else None


def _entry_hostname(value: str) -> str | None:
    """Host part of a config entry: ``host``, ``host:port``, ``host:*`` or a URL."""
    value = value.strip()
    if not value:
        return None
    if "://" in value:
        try:
            host = urlsplit(value).hostname
        except ValueError:
            return None
        return host.rstrip(".").lower() if host else None
    if value.endswith(":*"):
        value = value[:-2]
    return _hostname(value)


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


@lru_cache(maxsize=1)
def _machine_names() -> frozenset[str]:
    names = set()
    try:
        name = socket.gethostname().rstrip(".").lower()
    except OSError:
        name = ""
    if name:
        names.add(name)
        short = name.split(".", 1)[0]
        names.update({short, f"{short}.local"})
    return frozenset(names)


def _configured_hosts() -> tuple[frozenset[str], tuple[str, ...], bool]:
    """(exact names, allowed suffixes, wildcard) from the environment."""
    exact: set[str] = {*_machine_names(), *_DEFAULT_HOSTS}
    suffixes: list[str] = list(_DEFAULT_HOST_SUFFIXES)
    wildcard = False
    for entry in _env_list(ALLOWED_HOSTS_ENV):
        if entry == "*":
            wildcard = True
        elif entry.startswith("*.") or entry.startswith("."):
            suffix = "." + entry.lstrip("*.").rstrip(".").lower()
            if suffix != ".":
                suffixes.append(suffix)
        elif host := _entry_hostname(entry):
            exact.add(host)
    for entry in (*allowed_origin_values(), *_env_list(_MCP_HOSTS_ENV)):
        if host := _entry_hostname(entry):
            exact.add(host)
    for name in ("OMNIVOICE_API_URL", "OMNIVOICE_PUBLIC_API_BASE", "OMNIVOICE_BIND_HOST"):
        value = os.environ.get(name, "").strip()
        if value and (host := _entry_hostname(value)):
            exact.add(host)
    return frozenset(exact), tuple(suffixes), wildcard


def host_allowed(host_header: str | None) -> bool:
    """Whether a ``Host`` value is one this backend may be addressed by.

    IP literals are always allowed: a page can only be same-origin with an IP
    address if that address served it. ``localhost`` names resolve to loopback
    in every browser. Any other name must be configured.
    """
    if not host_header:
        # Browsers always send Host; a request without one cannot come from
        # a rebinding page (HTTP/1.0 tools, raw ASGI callers).
        return True
    host = _hostname(host_header)
    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost"):
        return True
    exact, suffixes, wildcard = _configured_hosts()
    return wildcard or host in exact or any(host.endswith(suffix) for suffix in suffixes)


def _origin_trusted(connection, presented) -> bool:
    """Whether an origin tuple is one this backend accepts browser requests from:
    its own origin, ``OMNIVOICE_ALLOWED_ORIGINS``, the desktop renderer and
    MCP hosts. The one allowlist for both ``Origin`` and ``Referer``."""
    if presented is None:
        return False
    if presented == _destination_origin(connection) or presented in configured_allowed_origins():
        return True
    extra = [*DEFAULT_DESKTOP_ORIGINS]
    for host in _env_list(_MCP_HOSTS_ENV):
        extra += [f"http://{host}", f"https://{host}"]
    return presented in {origin for value in extra if (origin := _origin_tuple(value))}


def _trusted_origin(connection) -> bool:
    return _origin_trusted(connection, _origin_tuple(connection.headers.get("origin")))


def _referer_origin(value: str | None):
    """Origin tuple of a ``Referer`` URL, or None when it is absent or malformed."""
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc or "@" in parsed.netloc:
        return None
    return _origin_tuple(f"{parsed.scheme}://{parsed.netloc}")


def is_cross_site(connection) -> bool:
    """Whether a browser sent this request on behalf of another site.

    An ``Origin`` header is authoritative when present (``null`` included).
    Without one, ``Sec-Fetch-Site: cross-site`` / ``same-site`` marks a
    request from another site — ``same-site`` covers other local web apps on
    a different localhost port — unless its ``Referer`` names an allowed
    origin. Browsers send no ``Origin`` on media and download GETs
    (``<video src>``, download links), so a UI served from another allowed
    origin (``OMNIVOICE_PUBLIC_API_BASE`` deployments) is recognised by its
    ``Referer``; a page cannot forge that header, only omit it, and a missing
    or foreign ``Referer`` is still refused. Requests with neither header are
    not from a browser page and are never treated as cross-site.
    """
    headers = connection.headers
    if "origin" in headers:
        return not _trusted_origin(connection)
    if headers.get("sec-fetch-site", "").strip().lower() not in _CROSS_SITE_FETCH:
        return False
    return not _origin_trusted(connection, _referer_origin(headers.get("referer")))


def reject_cross_site_get(request: Request) -> None:
    """Route dependency for GET endpoints with side effects.

    The middleware only screens unsafe methods; a GET that starts work must
    additionally refuse cross-site navigations and subresource loads.
    """
    from fastapi import HTTPException

    if is_cross_site(request):
        raise HTTPException(status_code=403, detail=CROSS_SITE_DETAIL)


def _network_authorized_only(connection) -> bool:
    """True when nothing but network position would authorise this request.

    These are the requests a page on a renamed host could make: loopback and trusted-network
    callers, and anonymous callers of a backend with no API key. Requests
    that present a valid API key or administrator session prove
    knowledge of a secret a rebinding page does not have — even from a
    loopback peer, which is how a reverse proxy on this machine (Caddy,
    cloudflared) forwards remote clients with their original ``Host``.
    Anonymous callers of a keyed backend are answered by the API-key gate (a
    401 the UI uses to prompt for the key). Neither is host-checked, so
    remote deployments behind arbitrary names keep working with their
    credentials.
    """
    from core.auth import PrincipalKind, presents_valid_credential, principal_for, remote_api_key

    kind = principal_for(connection).kind
    if kind in {PrincipalKind.LOOPBACK, PrincipalKind.TRUSTED_NETWORK}:
        return not presents_valid_credential(connection)
    return kind == PrincipalKind.ANONYMOUS and not remote_api_key()


class BrowserGuardMiddleware:
    """Pure-ASGI gate for cross-site requests and unrecognised ``Host`` names.

    Registered just inside CORS so preflights are answered by CORS and a
    refusal sent to an allowed origin still carries CORS headers.
    """

    def __init__(self, app, is_public_path=None):
        self.app = app
        # Paths the auth gates also leave open (SPA shell, assets, credential
        # exchange) skip the host check: their content is public or they
        # validate a secret themselves, and a remote UI on a custom host name
        # must load them to sign in. The cross-site check still applies.
        self.is_public_path = is_public_path or (lambda _path: False)

    def _host_check_applies(self, scope) -> bool:
        return not (scope["type"] == "http" and self.is_public_path(scope.get("path", "")))

    def _refusal(self, scope, connection, method) -> "str | None":
        """The refusal detail for this request, or None to let it through."""
        if (
            self._host_check_applies(scope)
            and not host_allowed(_header(scope, b"host"))
            and _network_authorized_only(connection)
        ):
            return HOST_DETAIL
        unsafe = scope["type"] != "http" or method not in SAFE_HTTP_METHODS
        return CROSS_SITE_DETAIL if unsafe and is_cross_site(connection) else None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        from starlette.requests import HTTPConnection

        connection = HTTPConnection(scope)
        method = str(scope.get("method", "GET")).upper()
        if scope["type"] == "http" and method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        detail = self._refusal(scope, connection, method)
        if detail is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await receive()  # consume websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": "request refused"})
            return
        from starlette.responses import JSONResponse

        await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
