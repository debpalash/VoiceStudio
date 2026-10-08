"""Worker identity, enrollment, and session credentials.

The goal doc said workers "must be revocable" and gave them a server-assigned
Worker ID. A server-assigned ID is a *name*, not proof of anything: if
reconnecting means "present this ID and a valid key", then a stolen key lets an
attacker impersonate an existing healthy worker, receive that user's voice
recordings, and return whatever it likes — inheriting the real worker's
standing while doing it. Revocation of a name you cannot verify is theatre.

So identity here is a **keypair the worker generates and never transmits**.
Enrollment binds its public key; every later connection proves possession by
signing a server challenge. Revoking a worker means refusing that public key,
which is a fact the server can actually check.

The enrollment token solves the other half — trusting the *server*. The OSS
control plane is a desktop app with a self-signed certificate, so the token
carries the certificate fingerprint and the worker pins it on first connect
(the join-token pattern from k3s and Tailscale). There is deliberately no
"skip verification" mode: on a coffee-shop network that flag is the whole
attack.

Tokens are single-use, expiring, and stored hashed — the plaintext exists only
in the dialog that shows it once.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from worker.clock import resolve

# Credential-class prefixes. Separate namespaces so an enrollment token can
# never be mistaken for (or used as) a client API key, and so a leaked string
# is identifiable on sight.
ENROLLMENT_PREFIX = "ovw"  # worker enrollment
SESSION_PREFIX = "ovs"     # worker session

_TOKEN_BYTES = 32
_DEFAULT_TOKEN_TTL_SECONDS = 15 * 60
_DEFAULT_SESSION_TTL_SECONDS = 60 * 60
_CHALLENGE_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_secret(secret: str) -> str:
    """Hash a credential for storage.

    Plain SHA-256 is correct here and bcrypt/argon2 would be cargo cult: these
    are 256-bit random tokens, not user-chosen passwords, so there is no
    dictionary to slow down.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ── Keypairs ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkerKeypair:
    """A worker's long-lived identity. The private half never leaves its host."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey

    @classmethod
    def generate(cls) -> "WorkerKeypair":
        private = Ed25519PrivateKey.generate()
        return cls(private_key=private, public_key=private.public_key())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "WorkerKeypair":
        private = Ed25519PrivateKey.from_private_bytes(raw)
        return cls(private_key=private, public_key=private.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return public_key_bytes(self.public_key)

    @property
    def key_id(self) -> str:
        return key_id_for(self.public_bytes())

    def sign(self, message: bytes) -> bytes:
        return self.private_key.sign(message)


def public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def key_id_for(public_bytes: bytes) -> str:
    """Short stable handle for a public key — safe to log and display."""
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def verify_signature(public_bytes: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# ── Challenge / response ───────────────────────────────────────────────────


def new_challenge() -> bytes:
    return secrets.token_bytes(_CHALLENGE_BYTES)


def challenge_message(
    *, challenge: bytes, worker_id: str, session_epoch: int, nonce: bytes
) -> bytes:
    """Bind the signature to this worker, this epoch, and this nonce.

    Signing a bare random challenge would let a captured signature be replayed
    against a different worker record or a stale epoch.
    """
    return b"|".join(
        [
            b"omnivoice.worker.v1",
            challenge,
            worker_id.encode("utf-8"),
            str(int(session_epoch)).encode("ascii"),
            nonce,
        ]
    )


# Feature a peer advertises when it signs a challenge the other side issued.
SERVER_CHALLENGE_FEATURE = "server_challenge_v1"
CHALLENGE_TTL_SECONDS = 120
_MAX_OUTSTANDING_CHALLENGES = 4096


class ChallengeBook:
    """Challenges this side issued and has not yet seen signed.

    A signature only proves freshness over a value the VERIFIER chose. Each
    challenge here is single-use and expires, so a captured Register frame
    cannot be replayed. The book is bounded because issuing is unauthenticated:
    under a flood the oldest entries go first, which costs a legitimate worker
    at most one retry.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = CHALLENGE_TTL_SECONDS,
        limit: int = _MAX_OUTSTANDING_CHALLENGES,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._limit = max(1, int(limit))
        self._issued: dict[bytes, float] = {}
        self._lock = threading.Lock()

    def issue(self, *, now: Optional[float] = None) -> bytes:
        stamp = resolve(now)
        challenge = new_challenge()
        with self._lock:
            for stale in [c for c, expiry in self._issued.items() if expiry <= stamp]:
                del self._issued[stale]
            while len(self._issued) >= self._limit:
                del self._issued[next(iter(self._issued))]
            self._issued[challenge] = stamp + self.ttl_seconds
        return challenge

    def consume(self, challenge: bytes, *, now: Optional[float] = None) -> bool:
        """Spend ``challenge``; True only for a live one this book issued."""
        if not challenge:
            return False
        with self._lock:
            expiry = self._issued.pop(bytes(challenge), None)
        return expiry is not None and resolve(now) < expiry


# ── Enrollment tokens ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnrollmentToken:
    """A single-use join token, shown to the user exactly once.

    Carries the control server's endpoint and certificate fingerprint so the
    worker can pin it — the token *is* the trust anchor, which is what makes a
    self-signed desktop control plane safe to connect to.
    """

    token_id: str
    secret: str
    endpoint: str
    cert_fingerprint: str
    expires_at: float

    def encode(self) -> str:
        """Serialize for display/copy-paste. One opaque string, prefixed."""
        payload = {
            "v": 1,
            "id": self.token_id,
            "s": self.secret,
            "e": self.endpoint,
            "f": self.cert_fingerprint,
            "x": int(self.expires_at),
        }
        blob = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return f"{ENROLLMENT_PREFIX}_{blob}"

    @classmethod
    def decode(cls, text: str) -> "EnrollmentToken":
        raw = (text or "").strip()
        if not raw.startswith(f"{ENROLLMENT_PREFIX}_"):
            raise ValueError("Not an OmniVoice worker enrollment token.")
        try:
            payload = json.loads(_unb64(raw.split("_", 1)[1]).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — any malformed token is one error
            raise ValueError("This enrollment token is malformed or truncated.") from exc
        if int(payload.get("v", 0)) != 1:
            raise ValueError("This enrollment token was made by a newer version.")
        return cls(
            token_id=str(payload["id"]),
            secret=str(payload["s"]),
            endpoint=str(payload["e"]),
            cert_fingerprint=str(payload["f"]),
            expires_at=float(payload["x"]),
        )

    def expired(self, *, now: Optional[float] = None) -> bool:
        return resolve(now) > self.expires_at

    @property
    def secret_hash(self) -> str:
        return hash_secret(self.secret)


def mint_enrollment_token(
    *,
    endpoint: str,
    cert_fingerprint: str,
    ttl_seconds: int = _DEFAULT_TOKEN_TTL_SECONDS,
    now: Optional[float] = None,
) -> EnrollmentToken:
    return EnrollmentToken(
        token_id=secrets.token_hex(8),
        secret=_b64(secrets.token_bytes(_TOKEN_BYTES)),
        endpoint=endpoint,
        cert_fingerprint=cert_fingerprint,
        expires_at=resolve(now) + ttl_seconds,
    )


def certificate_fingerprint(cert_der: bytes) -> str:
    """SHA-256 fingerprint, colon-free lowercase hex, of a DER certificate."""
    return hashlib.sha256(cert_der).hexdigest()


# ── Sessions ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Session:
    """Short-lived credential bound to one worker and one stream.

    Bound rather than bearer: a token that is only valid on the stream it was
    issued on cannot be replayed from another host if it leaks into a log.
    """

    token: str
    worker_id: str
    key_id: str
    epoch: int
    issued_at: float
    expires_at: float

    def expired(self, *, now: Optional[float] = None) -> bool:
        return resolve(now) >= self.expires_at

    @property
    def token_hash(self) -> str:
        return hash_secret(self.token)


def issue_session(
    *,
    worker_id: str,
    key_id: str,
    epoch: int,
    ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    now: Optional[float] = None,
) -> Session:
    stamp = resolve(now)
    return Session(
        token=f"{SESSION_PREFIX}_{_b64(secrets.token_bytes(_TOKEN_BYTES))}",
        worker_id=worker_id,
        key_id=key_id,
        epoch=epoch,
        issued_at=stamp,
        expires_at=stamp + ttl_seconds,
    )


# ── Worker-side credential storage ─────────────────────────────────────────


def save_worker_key(path: str, keypair: WorkerKeypair) -> None:
    """Persist a worker's private key with 0600 permissions.

    Follows the repo's existing precedent for machine-local secrets
    (``core/user_env.py``): a mode-restricted file, written atomically, never
    world-readable even briefly.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        remaining = memoryview(keypair.private_bytes())
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("could not finish writing the worker identity key")
            remaining = remaining[written:]
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_parent_directory(directory)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and some network filesystems do not honour POSIX modes; the
        # key is still in a per-user directory there.
        pass


def _fsync_parent_directory(directory: str) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        descriptor = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in unsupported:
            raise
    finally:
        os.close(descriptor)


def load_worker_key(path: str) -> Optional[WorkerKeypair]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except (FileNotFoundError, PermissionError):
        return None
    if len(raw) != 32:
        return None
    try:
        return WorkerKeypair.from_private_bytes(raw)
    except ValueError:
        return None


def load_or_create_worker_key(path: str) -> WorkerKeypair:
    existing = load_worker_key(path)
    if existing is not None:
        return existing
    keypair = WorkerKeypair.generate()
    save_worker_key(path, keypair)
    return keypair


__all__ = [
    "CHALLENGE_TTL_SECONDS",
    "ChallengeBook",
    "ENROLLMENT_PREFIX",
    "SERVER_CHALLENGE_FEATURE",
    "SESSION_PREFIX",
    "EnrollmentToken",
    "Session",
    "WorkerKeypair",
    "certificate_fingerprint",
    "challenge_message",
    "constant_time_equals",
    "hash_secret",
    "issue_session",
    "key_id_for",
    "load_or_create_worker_key",
    "load_worker_key",
    "mint_enrollment_token",
    "new_challenge",
    "public_key_bytes",
    "save_worker_key",
    "verify_signature",
]
