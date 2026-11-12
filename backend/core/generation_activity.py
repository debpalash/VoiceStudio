"""In-flight /generate stage for the status poll.

While a take is running the renderer polls ``GET /model/status``. Model load
sub-stages alone leave long prep work (reference transcription, passage
ranking) looking like a stuck "Generating audio…" spinner. This module holds
the current generate stage so the poll can name what is actually happening
and why a slow take is still working.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_lock = threading.Lock()
_state: dict[str, Any] = {
    "stage": None,
    "detail": None,
    "started_at": None,
}

# Stable stage ids the UI maps to i18n keys (synthesisState.*).
STAGES = (
    "preparing",
    "transcribing_reference",
    "ranking_reference",
    "synthesizing",
    "finalizing",
)


def begin() -> None:
    """Mark a generate request as in flight (idempotent per request)."""
    with _lock:
        _state["stage"] = "preparing"
        _state["detail"] = None
        _state["started_at"] = time.monotonic()


def set_stage(stage: str, detail: Optional[str] = None) -> None:
    """Update the live stage. No-op when no generate is active."""
    if stage not in STAGES:
        return
    with _lock:
        if _state["started_at"] is None:
            return
        _state["stage"] = stage
        _state["detail"] = detail


def end() -> None:
    """Clear the in-flight marker when the generate request finishes."""
    with _lock:
        _state["stage"] = None
        _state["detail"] = None
        _state["started_at"] = None


#: A generate that raised before the stream/classic finally left a stale
#: marker; hide it after this so the next idle poll is not stuck mid-stage.
_STALE_AFTER_S = 30 * 60


def snapshot() -> Optional[dict[str, Any]]:
    """Poll payload, or ``None`` when nothing is generating."""
    with _lock:
        started = _state["started_at"]
        if started is None or _state["stage"] is None:
            return None
        elapsed = time.monotonic() - started
        if elapsed > _STALE_AFTER_S:
            _state["stage"] = None
            _state["detail"] = None
            _state["started_at"] = None
            return None
        return {
            "stage": _state["stage"],
            "detail": _state["detail"],
            "elapsed_s": round(elapsed, 1),
        }
