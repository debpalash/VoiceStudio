"""Process-bound credentials for the first-party remote administration UI.

The durable ``OMNIVOICE_API_KEY`` is an operator secret, not a browser session.
This module exchanges it for opaque, bounded-lifetime credentials without
depending on FastAPI or persisting a verifier to disk.
"""

from __future__ import annotations

import hmac
import re
import secrets
import sys
import threading
import time
from types import ModuleType
from base64 import urlsafe_b64encode
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


SESSION_TTL_SECONDS = 8 * 60 * 60
WS_TICKET_TTL_SECONDS = 30
MAX_ADMIN_SESSIONS = 256
MAX_WS_TICKETS = 512

ADMIN_SESSION_PREFIX = "ovs_admin_session_"
WS_TICKET_PREFIX = "ovs_ws_ticket_"
_TOKEN_BYTES = 32
_ENCODED_TOKEN_LENGTH = 43
_TOKEN_BODY_RE = re.compile(rf"^[A-Za-z0-9_-]{{{_ENCODED_TOKEN_LENGTH}}}$")
# Every ticketed WebSocket route. The first-party mirror is ``ALLOWED_WS_PATHS``
# in electron/src/shared/api/authSession.ts — a route missing here mints a 422 and the
# UI consumer fails silently (#1769 added /ws/tts for the live dub preview).
_ALLOWED_WS_PATHS = frozenset(
    {"/ws/events", "/ws/transcribe", "/ws/tts", "/v1/audio/transcriptions/stream"}
)
_ADMIN_CAPABILITIES = frozenset({"consume", "admin"})
_KEY_GENERATION_INFO = b"omnivoice-admin-key-generation-v1"


def _hash_token(token: str, pepper: bytes) -> str:
    # These are 256-bit random values, not user-chosen passwords. A keyed,
    # process-local index is the right primitive: there is no feasible password
    # dictionary to slow down, and a copied record is unusable without the
    # store's independently generated pepper.
    return hmac.digest(pepper, token.encode("utf-8"), "sha256").hex()


def _encode_token(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class IssuedTicket:
    token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class SessionRecord:
    credential_id: str
    capabilities: frozenset[str]
    issued_at: float
    expires_at: float


@dataclass(frozen=True)
class _StoredSession:
    credential_id: str
    issued_monotonic: float
    expires_monotonic: float
    issued_at: float
    expires_at: float

    def public(self) -> SessionRecord:
        return SessionRecord(
            credential_id=self.credential_id,
            capabilities=_ADMIN_CAPABILITIES,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True)
class _StoredTicket:
    session_hash: str
    path: str
    issued_monotonic: float
    expires_monotonic: float


class AdminSessionStore:
    """Thread-safe, process-local store for admin sessions and WS tickets."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
        pepper: bytes | None = None,
        session_ttl_seconds: int = SESSION_TTL_SECONDS,
        ws_ticket_ttl_seconds: int = WS_TICKET_TTL_SECONDS,
        max_sessions: int = MAX_ADMIN_SESSIONS,
        max_tickets: int = MAX_WS_TICKETS,
    ) -> None:
        if session_ttl_seconds <= 0 or ws_ticket_ttl_seconds <= 0:
            raise ValueError("credential TTLs must be positive")
        if max_sessions <= 0 or max_tickets <= 0:
            raise ValueError("credential store capacities must be positive")
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._token_bytes = token_bytes
        self._pepper = pepper if pepper is not None else secrets.token_bytes(32)
        if len(self._pepper) < 32:
            raise ValueError("session-store pepper must contain at least 256 bits")
        self._session_ttl = session_ttl_seconds
        self._ticket_ttl = ws_ticket_ttl_seconds
        self._max_sessions = max_sessions
        self._max_tickets = max_tickets
        self._sessions: OrderedDict[str, _StoredSession] = OrderedDict()
        self._tickets: OrderedDict[str, _StoredTicket] = OrderedDict()
        self._ticket_hashes_by_session: dict[str, set[str]] = {}
        self._key_generation: bytes | None = None
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        snapshot = self.debug_snapshot()
        return (
            "AdminSessionStore("
            f"sessions={snapshot['sessions']}, ws_tickets={snapshot['ws_tickets']})"
        )

    @staticmethod
    def _normalize_master(api_key: str | None) -> str:
        return api_key.strip() if isinstance(api_key, str) else ""

    def _generation(self, api_key: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._pepper,
            info=_KEY_GENERATION_INFO,
        ).derive(api_key.encode("utf-8", errors="surrogatepass"))

    def _sync_key_locked(self, api_key: str | None) -> bool:
        normalized = self._normalize_master(api_key)
        if not normalized:
            self._clear_credentials_locked()
            self._key_generation = None
            return False
        generation = self._generation(normalized)
        if self._key_generation is None:
            self._key_generation = generation
            return True
        if not hmac.compare_digest(self._key_generation, generation):
            self._clear_credentials_locked()
            self._key_generation = generation
        return True

    @staticmethod
    def _valid_token(token: str | None, prefix: str) -> bool:
        if not isinstance(token, str) or not token.startswith(prefix):
            return False
        return bool(_TOKEN_BODY_RE.fullmatch(token.removeprefix(prefix)))

    def _new_token_locked(self, prefix: str, existing: object) -> tuple[str, str]:
        for _attempt in range(8):
            raw = self._token_bytes(_TOKEN_BYTES)
            if not isinstance(raw, bytes) or len(raw) != _TOKEN_BYTES:
                raise RuntimeError("token source must return exactly 32 bytes")
            token = prefix + _encode_token(raw)
            token_hash = _hash_token(token, self._pepper)
            if token_hash not in existing:
                return token, token_hash
        raise RuntimeError("credential token source produced repeated collisions")

    def _clear_credentials_locked(self) -> None:
        self._sessions.clear()
        self._tickets.clear()
        self._ticket_hashes_by_session.clear()

    def _remove_ticket_locked(self, ticket_hash: str) -> _StoredTicket | None:
        ticket = self._tickets.pop(ticket_hash, None)
        if ticket is None:
            return None
        session_tickets = self._ticket_hashes_by_session.get(ticket.session_hash)
        if session_tickets is not None:
            session_tickets.discard(ticket_hash)
            if not session_tickets:
                self._ticket_hashes_by_session.pop(ticket.session_hash, None)
        return ticket

    def _remove_session_locked(self, session_hash: str) -> _StoredSession | None:
        record = self._sessions.pop(session_hash, None)
        for ticket_hash in tuple(self._ticket_hashes_by_session.get(session_hash, ())):
            self._remove_ticket_locked(ticket_hash)
        # Defensive cleanup keeps a prior partial mutation from preserving a
        # dangling reverse-index bucket even when the session was already gone.
        self._ticket_hashes_by_session.pop(session_hash, None)
        return record

    def _purge_locked(self, now: float) -> None:
        # TTLs are fixed per store and monotonic issue times never decrease, so
        # insertion order is expiry order. Only the expired prefix can require
        # work; the common request path examines at most one record per type.
        while self._sessions:
            session_hash = next(iter(self._sessions))
            if now < self._sessions[session_hash].expires_monotonic:
                break
            self._remove_session_locked(session_hash)

        while self._tickets:
            ticket_hash = next(iter(self._tickets))
            if now < self._tickets[ticket_hash].expires_monotonic:
                break
            self._remove_ticket_locked(ticket_hash)

    def _evict_sessions_locked(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            self._remove_session_locked(next(iter(self._sessions)))

    def _evict_tickets_locked(self) -> None:
        while len(self._tickets) >= self._max_tickets:
            self._remove_ticket_locked(next(iter(self._tickets)))

    def issue(self, api_key: str) -> IssuedSession:
        normalized = self._normalize_master(api_key)
        if not normalized:
            raise ValueError("configured API key required")
        with self._lock:
            self._sync_key_locked(normalized)
            now = self._monotonic()
            wall_now = self._wall_time()
            self._purge_locked(now)
            self._evict_sessions_locked()
            token, token_hash = self._new_token_locked(ADMIN_SESSION_PREFIX, self._sessions)
            expires_monotonic = now + self._session_ttl
            expires_at = wall_now + self._session_ttl
            self._sessions[token_hash] = _StoredSession(
                credential_id=token_hash,
                issued_monotonic=now,
                expires_monotonic=expires_monotonic,
                issued_at=wall_now,
                expires_at=expires_at,
            )
            return IssuedSession(token=token, expires_at=expires_at)

    def resolve(self, token: str | None, api_key: str | None) -> SessionRecord | None:
        if not self._valid_token(token, ADMIN_SESSION_PREFIX):
            return None
        assert isinstance(token, str)
        with self._lock:
            if not self._sync_key_locked(api_key):
                return None
            now = self._monotonic()
            self._purge_locked(now)
            record = self._sessions.get(_hash_token(token, self._pepper))
            if record is None or now >= record.expires_monotonic:
                return None
            return record.public()

    def revoke(self, token: str | None) -> bool:
        if not self._valid_token(token, ADMIN_SESSION_PREFIX):
            return False
        assert isinstance(token, str)
        token_hash = _hash_token(token, self._pepper)
        with self._lock:
            return self._remove_session_locked(token_hash) is not None

    def revoke_by_credential(self, credential_id: str | None) -> bool:
        if not isinstance(credential_id, str) or len(credential_id) != 64:
            return False
        with self._lock:
            return self._remove_session_locked(credential_id) is not None

    def issue_ws_ticket(
        self,
        session_token: str | None,
        path: str,
        api_key: str | None,
    ) -> IssuedTicket:
        if path not in _ALLOWED_WS_PATHS:
            raise ValueError("WebSocket path is not allowed")
        if not self._valid_token(session_token, ADMIN_SESSION_PREFIX):
            raise PermissionError("valid admin session required")
        assert isinstance(session_token, str)
        session_hash = _hash_token(session_token, self._pepper)
        return self.issue_ws_ticket_for_credential(session_hash, path, api_key)

    def issue_ws_ticket_for_credential(
        self,
        credential_id: str | None,
        path: str,
        api_key: str | None,
    ) -> IssuedTicket:
        if path not in _ALLOWED_WS_PATHS:
            raise ValueError("WebSocket path is not allowed")
        with self._lock:
            if not isinstance(credential_id, str) or len(credential_id) != 64:
                raise PermissionError("valid admin session required")
            if not self._sync_key_locked(api_key):
                raise PermissionError("valid admin session required")
            now = self._monotonic()
            self._purge_locked(now)
            session = self._sessions.get(credential_id)
            if session is None or now >= session.expires_monotonic:
                raise PermissionError("valid admin session required")
            self._evict_tickets_locked()
            token, token_hash = self._new_token_locked(WS_TICKET_PREFIX, self._tickets)
            expires_at = self._wall_time() + self._ticket_ttl
            self._tickets[token_hash] = _StoredTicket(
                session_hash=credential_id,
                path=path,
                issued_monotonic=now,
                expires_monotonic=now + self._ticket_ttl,
            )
            self._ticket_hashes_by_session.setdefault(credential_id, set()).add(
                token_hash
            )
            return IssuedTicket(token=token, expires_at=expires_at)

    def consume_ws_ticket(
        self,
        ticket_token: str | None,
        path: str,
        api_key: str | None,
    ) -> SessionRecord | None:
        if not self._valid_token(ticket_token, WS_TICKET_PREFIX):
            return None
        assert isinstance(ticket_token, str)
        with self._lock:
            if not self._sync_key_locked(api_key):
                return None
            now = self._monotonic()
            self._purge_locked(now)
            ticket = self._remove_ticket_locked(
                _hash_token(ticket_token, self._pepper)
            )
            if ticket is None or now >= ticket.expires_monotonic or ticket.path != path:
                return None
            session = self._sessions.get(ticket.session_hash)
            if session is None or now >= session.expires_monotonic:
                return None
            return session.public()

    def clear(self) -> None:
        with self._lock:
            self._clear_credentials_locked()
            self._key_generation = None

    @property
    def active_session_count(self) -> int:
        with self._lock:
            self._purge_locked(self._monotonic())
            return len(self._sessions)

    def debug_snapshot(self) -> dict[str, int]:
        with self._lock:
            self._purge_locked(self._monotonic())
            return {"sessions": len(self._sessions), "ws_tickets": len(self._tickets)}


#: Synthetic ``sys.modules`` key holding the one per-process store. A module
#: object in ``sys.modules`` is the only namespace that survives everything
#: test suites do to this package: ``importlib.reload`` re-executes module
#: code but never touches unrelated ``sys.modules`` entries, and the purges
#: that pop whole ``services.*`` / ``api.*`` trees match package prefixes this
#: underscore-prefixed top-level name is outside of.
_ANCHOR_MODULE_NAME = "_omnivoice_admin_session_store_anchor"


def _process_store() -> AdminSessionStore:
    """Return THE per-process store, however this module was (re)imported.

    Auth is process-global state: the copy of this module that issues a
    credential and the copy that later resolves it must always be looking at
    the same store. A bare module-level ``AdminSessionStore()`` breaks that
    the moment anything reloads or re-imports this module (fresh module dict →
    fresh store → freshly issued sessions vanish for holders of the old
    reference, and vice versa). Anchoring the instance outside the module's
    own namespace makes every copy of this module share one store.
    """
    anchor = sys.modules.get(_ANCHOR_MODULE_NAME)
    if not isinstance(anchor, ModuleType):
        anchor = ModuleType(_ANCHOR_MODULE_NAME)
        anchor.__doc__ = "Process-global anchor for the VoiceStudio admin-session store."
        sys.modules[_ANCHOR_MODULE_NAME] = anchor
    store = getattr(anchor, "admin_session_store", None)
    if store is None:
        store = AdminSessionStore()
        anchor.admin_session_store = store
    return store


admin_session_store = _process_store()
