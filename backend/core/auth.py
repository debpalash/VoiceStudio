"""Canonical authentication identity for HTTP and WebSocket connections.

Transport parsing belongs here; authorization remains in FastAPI dependencies.
Each ASGI scope receives exactly one secret-free :class:`AuthPrincipal` so
middleware and route guards cannot disagree about credential precedence.
"""

from __future__ import annotations

import ipaddress
import importlib
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from services.admin_sessions import (
    AdminSessionStore,
)


_AUTH_STATE_KEY = "auth_principal"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

CONSUME_CAPABILITIES = frozenset({"consume"})
ADMIN_CAPABILITIES = frozenset({"consume", "admin"})
LOOPBACK_CAPABILITIES = frozenset({"consume", "admin", "native"})


class PrincipalKind(str, Enum):
    ANONYMOUS = "anonymous"
    LOOPBACK = "loopback"
    TRUSTED_NETWORK = "trusted_network"
    PIN = "pin"
    API_KEY = "api_key"
    ADMIN_SESSION = "admin_session"


class CredentialTransport(str, Enum):
    NONE = "none"
    HEADER = "header"
    QUERY = "query"
    COOKIE = "cookie"
    LEGACY_COOKIE = "legacy_cookie"
    WS_TICKET = "ws_ticket"


@dataclass(frozen=True)
class AuthPrincipal:
    kind: PrincipalKind
    capabilities: frozenset[str]
    credential_id: str | None = None
    transport: CredentialTransport = CredentialTransport.NONE

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class _CredentialCandidate:
    value: str = field(repr=False)
    transport: CredentialTransport
    allow_master: bool = False
    allow_session: bool = False
    allow_ticket: bool = False


def remote_api_key() -> str | None:
    """Normalized remote operator key, read dynamically for rotation support."""
    return os.environ.get("OMNIVOICE_API_KEY", "").strip() or None


def credential_matches(supplied: str | None, configured: str | None) -> bool:
    """Constant-time credential comparison that accepts the full Unicode range."""
    if not supplied or not configured:
        return False
    return secrets.compare_digest(
        supplied.encode("utf-8", errors="surrogatepass"),
        configured.encode("utf-8", errors="surrogatepass"),
    )


def _active_admin_session_store() -> AdminSessionStore:
    """Resolve mutable process state at call time so app reloads cannot split it."""
    module = importlib.import_module("services.admin_sessions")
    return module.admin_session_store


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for value in os.environ.get("OMNIVOICE_TRUSTED_NETWORKS", "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Invalid configuration never makes the gate fail open or wedge the
            # backend. It simply contributes no trusted range.
            continue
    return tuple(networks)


def is_loopback(host: str | None) -> bool:
    return host in _LOOPBACK_HOSTS


def is_local_host(host: str | None) -> bool:
    if is_loopback(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except (TypeError, ValueError):
        return False
    if getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    return any(address in network for network in _trusted_networks())


def _mapping_get(mapping: Mapping[str, str] | object, name: str) -> str:
    if not mapping:
        return ""
    getter = getattr(mapping, "get", None)
    if callable(getter):
        value = getter(name, "")
        if value:
            return str(value)
    # Real Starlette Headers are case-insensitive. This small fallback keeps
    # minimal request stubs and non-Starlette callers correct too.
    items = getattr(mapping, "items", None)
    if callable(items):
        for key, value in items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _scope_type(connection) -> str:
    scope = getattr(connection, "scope", None)
    return str(scope.get("type", "http")) if isinstance(scope, dict) else "http"


def _path(connection) -> str:
    scope = getattr(connection, "scope", None)
    if isinstance(scope, dict):
        return str(scope.get("path", ""))
    return str(getattr(connection, "url", "") or "")


def _canonical_websocket_path(connection) -> str:
    """Remove only the ASGI-configured deployment prefix from a WS path."""
    path = _path(connection)
    scope = getattr(connection, "scope", None)
    if not isinstance(scope, dict):
        return path
    root_path = str(scope.get("root_path", "") or "").rstrip("/")
    if not root_path or root_path == "/":
        return path
    root_path = "/" + root_path.lstrip("/")
    if path.startswith(root_path + "/"):
        return path[len(root_path) :]
    return path


def _client_host(connection) -> str | None:
    client = getattr(connection, "client", None)
    if client is not None:
        return getattr(client, "host", None)
    scope = getattr(connection, "scope", None)
    if isinstance(scope, dict) and scope.get("client"):
        return scope["client"][0]
    return None


def _credential_candidate(connection) -> _CredentialCandidate | None:
    query = getattr(connection, "query_params", None) or {}
    cookies = getattr(connection, "cookies", None) or {}

    raw_authorization = authorization_header(connection)
    authorization = raw_authorization.strip()
    if raw_authorization.lower().startswith("bearer "):
        value = raw_authorization[7:].strip()
        if value:
            return _CredentialCandidate(
                value=value,
                transport=CredentialTransport.HEADER,
                allow_master=True,
                allow_session=True,
            )
        # Preserve the legacy normalization contract: ``Bearer`` followed
        # only by whitespace is equivalent to an empty credential channel.
    elif authorization:
        # Any non-empty explicit Authorization value is authoritative, even
        # when its scheme is unsupported or its Bearer payload is missing.
        # It must never fall through to a stale ambient cookie.
        return _CredentialCandidate(
            value=authorization,
            transport=CredentialTransport.HEADER,
        )

    if _scope_type(connection) == "websocket":
        ticket = _mapping_get(query, "ws_ticket").strip()
        if ticket:
            return _CredentialCandidate(
                value=ticket,
                transport=CredentialTransport.WS_TICKET,
                allow_ticket=True,
            )

    query_key = _mapping_get(query, "api_key").strip()
    if query_key:
        return _CredentialCandidate(
            value=query_key,
            transport=CredentialTransport.QUERY,
            allow_master=True,
        )

    session = _mapping_get(cookies, "ov_session").strip()
    if session:
        return _CredentialCandidate(
            value=session,
            transport=CredentialTransport.COOKIE,
            allow_session=True,
        )

    legacy_key = _mapping_get(cookies, "ov_key").strip()
    if legacy_key:
        return _CredentialCandidate(
            value=legacy_key,
            transport=CredentialTransport.LEGACY_COOKIE,
            allow_master=True,
        )
    return None


def presented_api_key(connection) -> str:
    """Compatibility extractor for the durable API-key transports only."""
    candidate = _credential_candidate(connection)
    if candidate is None or not candidate.allow_master:
        return ""
    return candidate.value


def authorization_header(connection) -> str:
    headers = getattr(connection, "headers", None) or {}
    return _mapping_get(headers, "authorization")


def authorization_credential_present(connection) -> bool:
    """Whether Authorization contains an authoritative credential channel.

    This deliberately mirrors :func:`_credential_candidate`: whitespace and
    ``Bearer`` followed only by spaces are empty channels that may fall back to
    legacy migration state. Unsupported schemes and ``Bearer`` without the
    required separating space remain explicit invalid credentials.
    """
    authorization = authorization_header(connection)
    if authorization.lower().startswith("bearer ") and not authorization[7:].strip():
        return False
    return bool(authorization.strip())


def bearer_header_value(connection) -> str:
    authorization = authorization_header(connection)
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def legacy_master_cookie_valid(connection) -> bool:
    configured = remote_api_key()
    cookies = getattr(connection, "cookies", None) or {}
    supplied = _mapping_get(cookies, "ov_key").strip()
    return credential_matches(supplied, configured)


def master_header_valid(connection) -> bool:
    configured = remote_api_key()
    supplied = bearer_header_value(connection)
    return credential_matches(supplied, configured)


def _configured_pin(connection) -> str | None:
    app = getattr(connection, "app", None)
    state = getattr(app, "state", None) if app is not None else None
    network_share = getattr(state, "network_share", None) if state is not None else None
    pin = getattr(network_share, "pin", None) if network_share is not None else None
    return str(pin) if pin else None


def _valid_pin(connection) -> bool:
    configured = _configured_pin(connection)
    if not configured:
        return False
    headers = getattr(connection, "headers", None) or {}
    query = getattr(connection, "query_params", None) or {}
    cookies = getattr(connection, "cookies", None) or {}
    supplied = (
        _mapping_get(headers, "x-omnivoice-pin").strip()
        or _mapping_get(query, "pin").strip()
        or _mapping_get(cookies, "ov_pin").strip()
    )
    return credential_matches(supplied, configured)


def _attached_principal(connection) -> AuthPrincipal | None:
    scope = getattr(connection, "scope", None)
    if not isinstance(scope, dict):
        return None
    state = scope.get("state")
    if isinstance(state, dict):
        principal = state.get(_AUTH_STATE_KEY)
        return principal if isinstance(principal, AuthPrincipal) else None
    return None


def _attach_principal(connection, principal: AuthPrincipal) -> AuthPrincipal:
    scope = getattr(connection, "scope", None)
    if isinstance(scope, dict):
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[_AUTH_STATE_KEY] = principal
    return principal


def _candidate_principal(
    connection,
    candidate: _CredentialCandidate,
    configured_key: str | None,
    store: AdminSessionStore,
) -> AuthPrincipal | None:
    """The principal a presented credential proves, or None if it is invalid.

    A WebSocket ticket is single-use: validating one consumes it.
    """
    principal: AuthPrincipal | None = None
    if (
        candidate.allow_master
        and credential_matches(candidate.value, configured_key)
    ):
        principal = AuthPrincipal(
            PrincipalKind.API_KEY,
            ADMIN_CAPABILITIES,
            credential_id="api-key",
            transport=candidate.transport,
        )
    elif candidate.allow_session:
        session = store.resolve(candidate.value, configured_key)
        if session is not None:
            principal = AuthPrincipal(
                PrincipalKind.ADMIN_SESSION,
                session.capabilities,
                credential_id=session.credential_id,
                transport=candidate.transport,
            )
    elif candidate.allow_ticket:
        session = store.consume_ws_ticket(
            candidate.value,
            _canonical_websocket_path(connection),
            configured_key,
        )
        if session is not None:
            principal = AuthPrincipal(
                PrincipalKind.ADMIN_SESSION,
                session.capabilities,
                credential_id=session.credential_id,
                transport=candidate.transport,
            )
    return principal


def resolve_principal(
    connection,
    *,
    store: AdminSessionStore | None = None,
) -> AuthPrincipal:
    """Resolve and attach the single authentication decision for one scope."""
    attached = _attached_principal(connection)
    if attached is not None:
        return attached
    if store is None:
        store = _active_admin_session_store()

    host = _client_host(connection)
    if is_loopback(host):
        return _attach_principal(
            connection,
            AuthPrincipal(PrincipalKind.LOOPBACK, LOOPBACK_CAPABILITIES),
        )

    candidate = _credential_candidate(connection)
    configured_key = remote_api_key()
    if candidate is not None:
        principal = _candidate_principal(connection, candidate, configured_key, store)
        if principal is not None:
            return _attach_principal(connection, principal)
        # An explicit, non-empty credential is authoritative. Do not silently
        # fall back to network or PIN trust after an invalid higher-priority
        # credential was presented.
        return _attach_principal(
            connection,
            AuthPrincipal(
                PrincipalKind.ANONYMOUS,
                frozenset(),
                transport=candidate.transport,
            ),
        )

    if is_local_host(host):
        return _attach_principal(
            connection,
            AuthPrincipal(PrincipalKind.TRUSTED_NETWORK, CONSUME_CAPABILITIES),
        )
    if _valid_pin(connection):
        return _attach_principal(
            connection,
            AuthPrincipal(
                PrincipalKind.PIN,
                CONSUME_CAPABILITIES,
                transport=CredentialTransport.HEADER,
            ),
        )
    return _attach_principal(
        connection,
        AuthPrincipal(PrincipalKind.ANONYMOUS, frozenset()),
    )


def presents_valid_credential(
    connection,
    *,
    store: AdminSessionStore | None = None,
) -> bool:
    """Whether the request carries a valid API key or admin session.

    Independent of network position: a loopback peer is resolved as LOOPBACK
    before credentials are looked at, but a reverse proxy on this machine
    forwards remote clients that authenticate themselves. The share PIN does
    not count: it is short enough to guess, and a loopback request keeps
    loopback rights, so a page re-pointing its domain at 127.0.0.1 could
    otherwise try PINs until one passes. A WebSocket ticket is consumed by
    this check, so call it at most once per handshake.
    """
    if store is None:
        store = _active_admin_session_store()
    candidate = _credential_candidate(connection)
    if candidate is not None:
        return _candidate_principal(connection, candidate, remote_api_key(), store) is not None
    return False


def principal_for(
    connection,
    *,
    store: AdminSessionStore | None = None,
) -> AuthPrincipal:
    return _attached_principal(connection) or resolve_principal(connection, store=store)
