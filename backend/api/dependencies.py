"""
Shared FastAPI dependencies.

These are intentionally tiny — one concern per dependency — so they can be
composed at the route or router level without surprises.

Currently exposed:
- `require_loopback`: 403 unless the request came from a loopback origin
  (read-only bootstrap is allowed in explicit server mode; mutations still
  require the admin API key — see `_server_mode`).
- `require_admin`: method-aware admin gate for privileged routers.
- `require_admin_action`: strict admin gate for side-effectful GET actions.
- `require_native_access`: true-loopback-only access to the host filesystem;
  unlike `require_loopback`, it is never bypassed by server mode.
- `ws_remote_authorized`: whether a WebSocket handshake from a non-loopback
  client carries the remote API key (Wave 2.3) — used by WS endpoints that
  keep their own inline loopback guards.
"""

import os

from fastapi import HTTPException, Request

from core.auth import (
    CredentialTransport,
    PrincipalKind,
    is_local_host,
    is_loopback,
    principal_for,
    remote_api_key,
)
from core.csrf import SAFE_HTTP_METHODS, cookie_csrf_allowed

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _server_mode() -> bool:
    """Whether this process is a headless server deployment (Docker image).

    In Docker the loopback gate is *unenforceable*: Docker's network NAT
    rewrites ``request.client.host`` to the bridge gateway (e.g. 172.17.0.1)
    even for a localhost-only ``-p 127.0.0.1:3900:3900`` mapping, so every
    request looks non-loopback and the gate 403s the operator out of the
    system/settings routes they need (issue #261 — incl. ``/system/info``,
    which blanks the version display).

    The Docker image sets ``OMNIVOICE_SERVER_MODE=1`` to opt out of the gate.
    Network exposure then rests on the operator's port mapping plus the
    optional share PIN (``NetworkAccessMiddleware`` still 401s unauthenticated
    non-loopback clients whenever a PIN is set). The desktop build never sets
    this, so its loopback boundary — including denying LAN share guests access
    to admin routes — is unchanged. Read at call time so it stays testable.
    """
    return os.environ.get("OMNIVOICE_SERVER_MODE", "").strip().lower() in _TRUTHY


def validate_server_admin_key() -> None:
    """Reject an explicitly blank key before a server-mode app starts."""
    raw_key = os.environ.get("OMNIVOICE_API_KEY")
    if _server_mode() and raw_key is not None and not raw_key.strip():
        raise RuntimeError(
            "OMNIVOICE_API_KEY is blank; configure a non-whitespace administrator key"
        )


def _configured_pin(request) -> str | None:
    """The active share PIN (``app.state.network_share.pin``) or None. Read via
    getattr so a bare Request stub (or a request that hit before lifespan set
    the state) never raises — a missing PIN just means 'no PIN gate'."""
    app = getattr(request, "app", None)
    state = getattr(app, "state", None) if app is not None else None
    ns = getattr(state, "network_share", None) if state is not None else None
    return getattr(ns, "pin", None) if ns is not None else None


def _admin_credential_configured(request) -> bool:
    """Whether an API key or share PIN is configured.

    The PIN cannot authorize admin access, but its presence means the operator
    opted out of bare-server discovery. Remote admin then remains closed until
    they configure and present the long API key.
    """
    if remote_api_key():
        return True
    return bool(_configured_pin(request))


def _request_presents_admin_credential(
    request,
    *,
    side_effectful_get: bool = False,
) -> bool:
    """Whether the canonical principal carries remote admin capability.

    API-key and short-lived session principals may unlock server-mode admin.
    PIN and trusted-network principals remain consumption-only.
    """
    principal = principal_for(request)
    if principal.kind not in {
        PrincipalKind.API_KEY,
        PrincipalKind.ADMIN_SESSION,
    }:
        return False
    if principal.transport not in {
        CredentialTransport.COOKIE,
        CredentialTransport.LEGACY_COOKIE,
    }:
        return True
    method = str(getattr(request, "method", "GET")).upper()
    if side_effectful_get or method not in SAFE_HTTP_METHODS:
        return cookie_csrf_allowed(
            request,
            side_effectful_get=side_effectful_get,
        )
    return True


def require_consumer(request: Request) -> None:
    """Allow metadata access to local clients and authenticated remote consumers."""
    principal = principal_for(request)
    if not principal.allows("consume"):
        # Explicit bare-server deployments delegate consumption to their port
        # mapping. Preserve browser export history without granting admin/native.
        if (_server_mode() and not _admin_credential_configured(request)
                and principal.transport == CredentialTransport.NONE):
            return
        raise HTTPException(status_code=403, detail="authenticated consumer required")
    if principal.transport in {CredentialTransport.COOKIE, CredentialTransport.LEGACY_COOKIE}:
        if request.method.upper() not in SAFE_HTTP_METHODS and not cookie_csrf_allowed(request):
            raise HTTPException(status_code=403, detail="CSRF validation failed")


def require_loopback(request: Request) -> None:
    """Reject any request whose `client.host` is not a loopback address.

    Use as a router-level dependency to protect every route on the router
    in one place:

        router = APIRouter(dependencies=[Depends(require_loopback)])

    Or as a per-route dependency for narrower scope:

        @router.post("/foo", dependencies=[Depends(require_loopback)])

    Returns None on success (FastAPI dependency convention). Raises 403
    on rejection — the response body is `{"detail": "loopback origin required"}`
    so existing tests for `/system/set-env` keep passing without modification.

    In server mode (Docker, see `_server_mode`) the loopback origin is
    unenforceable, so the gate can't require true loopback. It then applies the
    admin-credential rule instead:

    - No credential configured (no API key, no PIN) → read-only requests are
      open, matching the #261 Docker bootstrap flow. State-changing requests
      fail closed even if a route accidentally kept this legacy dependency.
    - A credential IS configured → the request must present the **API key**.
      This keeps the two-tier privilege model intact under server mode:
      ``OMNIVOICE_TRUSTED_NETWORKS`` is a *consumption* exemption
      (``is_local_host``) that bypasses the PIN / API-key middleware, and it must
      NEVER by itself unlock the admin surface (``/system/set-env`` — RCE-class —
      and ``/api/settings/*``). The 6-digit share PIN is a consumption credential
      too and does not gate admin, so a PIN-only deployment keeps admin
      loopback-only; remote admin requires the long API key. See
      docs/api-auth.md (#1213).
    """
    host = request.client.host if request.client else None
    if is_loopback(host):
        return
    if _server_mode():
        method = str(getattr(request, "method", "GET")).upper()
        if method not in SAFE_HTTP_METHODS:
            # Defense in depth. Privileged routers should declare
            # ``require_admin`` directly, but a missed migration must not turn
            # into an unauthenticated Docker write primitive.
            require_admin(request)
            return
        if not _admin_credential_configured(request):
            return
        if _request_presents_admin_credential(request):
            return
    raise HTTPException(status_code=403, detail="loopback origin required")


def _admin_gate_403() -> None:
    """Raise the admin-gate 403 with a detail that states what would ACTUALLY
    satisfy the gate. The bundled UI routes any 403 whose detail mentions
    "admin api key" to the API-key login form (frontend ``client.ts``; the
    literal contract is locked by ``tests/test_auth_gate_detail_lockstep.py``),
    so the wording must not name a key where presenting one cannot help.

    The detail names the key only when the gate would accept one: server mode
    WITH an API key configured. Every other rejection — desktop mode (the
    credential checks in the callers only run under server mode) and a
    server-mode deployment with only a share PIN or nothing configured — keeps
    the plain loopback detail, because only loopback can use admin there.
    Naming the key in those cases would trap a LAN-share guest in a login
    form that can never succeed (#1213, #1525; PR #1569 review).
    """
    raise HTTPException(
        status_code=403,
        detail=(
            "loopback origin or admin API key required"
            if _server_mode() and remote_api_key()
            else "loopback origin required"
        ),
    )


def require_admin(request: Request) -> None:
    """Gate RCE/filesystem-capable admin routers.

    Desktop callers keep the loopback-only contract. Docker cannot reliably
    observe the host operator as loopback, so authenticated remote admin stays
    available there, but every state-changing request must present the long API
    key. An unconfigured server must never expose executable-path or filesystem
    settings to every client that can reach its published port.

    Read-only requests retain the bare-Docker bootstrap behaviour until an API
    key is configured. Share PINs and trusted CIDRs are consumption credentials;
    neither authorizes this gate.
    """
    host = request.client.host if request.client else None
    if is_loopback(host):
        return
    if _server_mode():
        method = str(getattr(request, "method", "GET")).upper()
        read_only = method in SAFE_HTTP_METHODS
        if read_only and not _admin_credential_configured(request):
            return
        if _request_presents_admin_credential(request):
            return
    _admin_gate_403()


def require_admin_action(request: Request) -> None:
    """Gate an administrative action even when its HTTP method is read-only.

    A small number of legacy GET endpoints have real side effects. For example,
    an engine health check may spawn a sidecar process. Such routes cannot use
    :func:`require_admin`'s bare-server discovery exception.
    """
    host = request.client.host if request.client else None
    if is_loopback(host):
        return
    if _server_mode() and _request_presents_admin_credential(
        request,
        side_effectful_get=True,
    ):
        return
    _admin_gate_403()


def require_desktop(request: Request) -> None:
    """Gate capabilities that may select or execute host filesystem paths.

    An API key authorizes remote administration, not access to the desktop
    shell's native file-picker boundary.  These capabilities therefore remain
    strictly loopback-only even when server mode is enabled.
    """
    host = request.client.host if request.client else None
    if is_loopback(host):
        return
    raise HTTPException(status_code=403, detail="desktop origin required")


def require_local(request: Request) -> None:
    """Reject any request whose client.host is not loopback OR on a configured
    trusted network. The consumption-tier companion to :func:`require_loopback`:
    use on routes a trusted-network client (LAN/proxy) should reach without a PIN
    or API key — e.g. the dictation model/prefs endpoints that pair with the
    dictation WebSocket. Admin routes stay on :func:`require_admin`.

    In server mode this consumption gate is a no-op. Admin dependencies remain
    method-aware and independent from this exemption."""
    host = request.client.host if request.client else None
    if is_local_host(host):
        return
    if _server_mode():
        return
    raise HTTPException(status_code=403, detail="loopback origin required")


def require_native_access(request: Request) -> None:
    """Protect capabilities that read or write operator-chosen host paths.

    Docker server mode deliberately relaxes the ordinary admin gate because a
    bridge makes even local traffic appear remote. That exception is unsafe for
    native file pickers: a remote API caller must never probe or overwrite an
    arbitrary path on the backend host, even with the server API key.
    """
    host = request.client.host if request.client else None
    if not is_loopback(host):
        raise HTTPException(status_code=403, detail="native filesystem access requires loopback origin")


def ws_remote_authorized(websocket) -> bool:
    """Whether the canonical WS principal has a remote admin credential."""
    return principal_for(websocket).kind in {
        PrincipalKind.API_KEY,
        PrincipalKind.ADMIN_SESSION,
    }
