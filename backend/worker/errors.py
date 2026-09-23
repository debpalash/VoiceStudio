"""Worker-protocol error taxonomy.

Why this exists: ``§22`` of the original goal doc listed "retryable errors" and
"non-retryable errors" without saying who decides. Without a single classifier
every worker invents its own strings and the scheduler retries deterministic
failures around the whole fleet — the "poison task" scenario, where one bad
input quarantines every machine that touches it.

The rule the scheduler needs is not "did it fail" but "would trying somewhere
else help":

    TRANSIENT   → yes, retry elsewhere; the worker is charged for it
    CAPABILITY  → yes, retry elsewhere; the worker is NOT charged (it simply
                  cannot run this model — a 4 GB card refusing a 6 GB engine
                  is correct behaviour, not flakiness)
    CAPACITY    → yes, immediately; never charged (the worker is doing its job)
    TIMEOUT     → maybe, per attempt budget; charged only if the worker was
                  otherwise healthy
    TERMINAL    → no. Fail the task now.
    PROTOCOL    → no retry of the task; the *session* is what is broken.

This module maps the app's existing docs taxonomy (``core.failure.classify``)
onto those classes rather than inventing a second vocabulary, so a failure that
already has a user-facing hint keeps it when it crosses the wire.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from core import failure


class ErrorClass(str, enum.Enum):
    """Mirrors ``ErrorClass`` in worker_v1.proto."""

    TRANSIENT = "transient"
    CAPABILITY = "capability"
    TERMINAL = "terminal"
    CAPACITY = "capacity"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"

    @property
    def retryable(self) -> bool:
        """Would assigning this task to another worker plausibly help?"""
        return self in _RETRYABLE

    @property
    def charges_worker(self) -> bool:
        """Does this failure count against the worker's circuit breaker?

        Capability and capacity failures must not: penalising a worker for
        correctly declining work it cannot do is how a healthy fleet
        quarantines itself (docs/remote-workers.md).
        """
        return self in _CHARGEABLE


_RETRYABLE = frozenset(
    {ErrorClass.TRANSIENT, ErrorClass.CAPABILITY, ErrorClass.CAPACITY, ErrorClass.TIMEOUT}
)
_CHARGEABLE = frozenset({ErrorClass.TRANSIENT, ErrorClass.TIMEOUT})


# Docs-taxonomy key → protocol class. Keys come from core.failure.classify();
# anything unmapped falls back to TRANSIENT, which is the safe default: one
# wasted retry beats permanently failing work that would have succeeded.
_TAXONOMY: dict[str, ErrorClass] = {
    # Environment is broken on THIS worker — another machine may be fine.
    "BROKEN_VENV": ErrorClass.CAPABILITY,
    "PKG_RESOURCES_MISSING": ErrorClass.CAPABILITY,
    "TRANSFORMERS_IMPORT": ErrorClass.CAPABILITY,
    "MEDIA_TOOL_MISSING": ErrorClass.CAPABILITY,
    "COMPUTE_TYPE_UNSUPPORTED": ErrorClass.CAPABILITY,
    "GATEKEEPER_QUARANTINE": ErrorClass.CAPABILITY,
    "APPIMAGE_WEBKIT_WHITESCREEN": ErrorClass.CAPABILITY,
    "WINDOWS_APP_CONTROL_BLOCKED": ErrorClass.CAPABILITY,
    "WINDOWS_PAGING_FILE_TOO_SMALL": ErrorClass.CAPABILITY,
    "SOCKS_PROXY_SUPPORT_MISSING": ErrorClass.CAPABILITY,
    # Network / cache — retry, possibly on the same worker later.
    "HF_MIRROR_UNREACHABLE": ErrorClass.TRANSIENT,
    "MODEL_DOWNLOAD_INTERRUPTED": ErrorClass.TRANSIENT,
    "MODEL_CACHE_CORRUPT": ErrorClass.TRANSIENT,
    "SSL_HANDSHAKE_FAILURE": ErrorClass.TRANSIENT,
    "TLS_CONNECTION_DROPPED": ErrorClass.TRANSIENT,
    "VIDEO_DOWNLOAD_NETWORK": ErrorClass.TRANSIENT,
    "AUDIO_IO_FAILED": ErrorClass.TRANSIENT,
    "VIDEO_DOWNLOAD_OS_ERROR": ErrorClass.TRANSIENT,
    "OS_INVALID_ARGUMENT": ErrorClass.TRANSIENT,
    # Needs a human; no worker will do better.
    "HF_AUTH_FAILED": ErrorClass.TERMINAL,
    "PYANNOTE_LICENSE_REQUIRED": ErrorClass.TERMINAL,
    "UNSUPPORTED_VIDEO_URL": ErrorClass.TERMINAL,
    "VIDEO_DRM_PROTECTED": ErrorClass.TERMINAL,
    "VIDEO_DOWNLOAD_BOT_CHECK": ErrorClass.TERMINAL,
    "NO_AUDIO_TRACK": ErrorClass.TERMINAL,
}

# Protocol-level codes raised by the worker layer itself (no docs taxonomy).
_PROTOCOL_CODES: dict[str, ErrorClass] = {
    "WORKER_AT_CAPACITY": ErrorClass.CAPACITY,
    "MODEL_NOT_INSTALLED": ErrorClass.CAPABILITY,
    "MODEL_NOT_DOWNLOADED": ErrorClass.CAPABILITY,
    "INSUFFICIENT_MEMORY": ErrorClass.CAPABILITY,
    "OPERATION_UNSUPPORTED": ErrorClass.CAPABILITY,
    "ACCEPT_TIMEOUT": ErrorClass.TIMEOUT,
    "MODEL_LOAD_TIMEOUT": ErrorClass.TIMEOUT,
    "EXECUTION_TIMEOUT": ErrorClass.TIMEOUT,
    "PROGRESS_LEASE_EXPIRED": ErrorClass.TIMEOUT,
    "RESULT_DELIVERY_TIMEOUT": ErrorClass.TIMEOUT,
    "INPUT_FETCH_TIMEOUT": ErrorClass.TIMEOUT,
    "INPUT_FETCH_FAILED": ErrorClass.TRANSIENT,
    "RESULT_UPLOAD_FAILED": ErrorClass.TRANSIENT,
    "WORKER_FAILED": ErrorClass.TRANSIENT,
    "SESSION_EXPIRED": ErrorClass.PROTOCOL,
    "STALE_EPOCH": ErrorClass.PROTOCOL,
    "STALE_ATTEMPT": ErrorClass.PROTOCOL,
    "UPGRADE_REQUIRED": ErrorClass.PROTOCOL,
    "WORKER_REVOKED": ErrorClass.PROTOCOL,
    "AUTH_FAILED": ErrorClass.PROTOCOL,
    "INVALID_TASK_PARAMS": ErrorClass.TERMINAL,
    "MODEL_REF_REJECTED": ErrorClass.TERMINAL,
    # Terminal, not transient: the render succeeded but is bigger than the
    # stream can carry, so retrying re-renders the same oversized audio. Left
    # unclassified it fell through to TRANSIENT and the task retried until it
    # ran out of attempts, each one paying the full generation again.
    "RESULT_TOO_LARGE": ErrorClass.TERMINAL,
    "ARTIFACT_TOO_LARGE": ErrorClass.TERMINAL,
    "OFFSET_MISMATCH": ErrorClass.TRANSIENT,
    "SIZE_MISMATCH": ErrorClass.TRANSIENT,
    "DIGEST_MISMATCH": ErrorClass.TRANSIENT,
    "UPLOAD_INCOMPLETE": ErrorClass.TRANSIENT,
}


@dataclass(frozen=True)
class WorkerError:
    """A failure as it crosses the wire — already scrubbed, always actionable."""

    error_class: ErrorClass
    code: str
    message: str
    hint: str = ""

    @property
    def retryable(self) -> bool:
        return self.error_class.retryable

    @property
    def charges_worker(self) -> bool:
        return self.error_class.charges_worker

    def to_dict(self) -> dict:
        return {
            "error_class": self.error_class.value,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


def classify_code(code: str) -> ErrorClass:
    """Classify a protocol-level code, then fall back to the docs taxonomy."""
    if code in _PROTOCOL_CODES:
        return _PROTOCOL_CODES[code]
    return _TAXONOMY.get(code, ErrorClass.TRANSIENT)


def from_reason(reason: str, *, code: Optional[str] = None) -> WorkerError:
    """Build a wire error from a raw failure string.

    ``reason`` is sanitized through ``core.failure`` before it leaves the
    machine — HF tokens, ``*KEY*``/``*SECRET*`` env values and home paths must
    never ride the wire (docs/remote-workers.md), and the worker is a remote machine
    whose logs the user may never see.
    """
    safe = failure.sanitize(reason) or reason.__class__.__name__
    resolved = code or failure.classify(reason) or ""
    cls = classify_code(resolved) if resolved else ErrorClass.TRANSIENT
    return WorkerError(
        error_class=cls,
        code=resolved or "UNKNOWN",
        message=safe,
        hint=_hint_for(resolved),
    )


def from_exception(exc: BaseException, *, code: Optional[str] = None) -> WorkerError:
    reason = failure.describe_exception(exc)
    if code is None and _is_invalid_generation_input(reason):
        # OmniVoice validates its closed voice-direction vocabulary inside
        # inference.  The local route already turns these signatures into a
        # 400; a worker used to call the same deterministic ValueError
        # UNKNOWN/TRANSIENT and spend the task's retry budget on identical
        # renders.  Keep the message (it contains the accepted vocabulary),
        # but stop the fleet after the first attempt.
        code = "INVALID_TASK_PARAMS"
    return from_reason(reason, code=code)


def _is_invalid_generation_input(reason: str) -> bool:
    low = (reason or "").lower()
    return any(
        signature in low
        for signature in (
            "unsupported instruct items",
            "conflicting instruct items",
            "in a single instruct",
        )
    )


def _hint_for(taxonomy_key: str) -> str:
    """Reuse the app's existing one-line remediation for a taxonomy key."""
    if not taxonomy_key:
        return ""
    hints = getattr(failure, "_HINTS", {})
    return hints.get(taxonomy_key, "")


__all__ = [
    "ErrorClass",
    "WorkerError",
    "classify_code",
    "from_reason",
    "from_exception",
]
