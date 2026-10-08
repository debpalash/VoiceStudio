"""Ring journal of recent backend errors — the "what just broke" store.

The global exception handler (main.py) records every unhandled exception
here. Unlike crash_log.txt (append-only plain text for humans), the journal
is structured and deduplicated, so the UI and the bug-report pipeline can
answer:

  - what was the most recent backend error? (auto-attach to a report)
  - is it the same error repeating? (count by fingerprint, "x14 since start")
  - what KIND of failure is it? (error_class — GPU_OOM, HF_AUTH_FAILED, …)

Everything stored is pre-scrubbed (core.scrub) because journal entries feed
the diagnostic bundle and prefilled GitHub issues. In-memory ring of
``_MAX_ENTRIES`` fingerprints, mirrored to ``DATA_DIR/error_journal.jsonl``
(rewritten on each record — entry count is small, atomicity beats append
here) so the journal survives restarts and the crash it just recorded.

``error_class`` values: the install-time classes reuse the locked taxonomy
keys from core.error_docs_map (HF_AUTH_FAILED, PYANNOTE_LICENSE_REQUIRED) so
docs deeplinks keep working; runtime classes (GPU_OOM, DISK_FULL,
NETWORK_ERROR, FFMPEG_MISSING) are journal-local and fall back to
DEFAULT_DOCS in lookup(). Don't add them to ERROR_DOCS without following
the 4-step mirror contract documented there.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict

from core.config import DATA_DIR
from core.scrub import scrub_text

logger = logging.getLogger("omnivoice.error_journal")

JOURNAL_PATH = os.path.join(DATA_DIR, "error_journal.jsonl")

_MAX_ENTRIES = 50
_MAX_TRACE_CHARS = 4000

_lock = threading.Lock()
# fingerprint -> entry, oldest first (move_to_end on repeat).
_entries: "OrderedDict[str, dict]" = OrderedDict()


# Ordered: first match wins, most specific patterns up top.
_CLASS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GPU_OOM", (
        "cuda out of memory",
        "mps backend out of memory",
        "hip out of memory",
        "out of memory on device",
    )),
    # #2462: the host ran out of system RAM. Mirrors
    # `failure._HOST_OOM_SIGNATURES` — kept as literals here so a journal
    # classification never depends on importing the taxonomy, and pinned to
    # agree with it by tests/test_host_memory_failure_2462.py. After GPU_OOM so
    # a device OOM keeps its own class, exactly as `classify` orders them.
    #
    # NOT a bare "memoryerror" needle: that is a substring of torch's
    # `OutOfMemoryError`, so it would claim every device OOM whose message the
    # GPU rule above doesn't spell out (a custom allocator's wording) and
    # advise a CPU host to free VRAM. Python's `MemoryError` is matched by TYPE
    # below instead, which is the whole reason this class exists — a bare
    # `MemoryError()` has an empty message and no signature to match on.
    ("HOST_MEMORY_EXHAUSTED", (
        "defaultcpuallocator: not enough memory",
        "not enough memory: you tried to allocate",
        "can't allocate memory",
        "cannot allocate memory",
        "std::bad_alloc",
        "not enough memory to continue the execution of the program",
        "[winerror 8]",
    )),
    ("PYANNOTE_LICENSE_REQUIRED", (
        "pyannote",  # only meaningful combined with an auth marker — see classify()
    )),
    ("HF_AUTH_FAILED", (
        "401 client error",
        "403 client error",
        "gatedrepoerror",
        "repository not found",
        "invalid user token",
        "huggingface_hub.errors",
    )),
    ("DISK_FULL", (
        "no space left on device",
        "errno 28",
        "disk quota exceeded",
    )),
    ("FFMPEG_MISSING", (
        "ffmpeg not found",
        "ffmpeg is not installed",
        "no such file or directory: 'ffmpeg'",
    )),
    ("NETWORK_ERROR", (
        "connection refused",
        "connection reset",
        "connection aborted",
        # transformers' download-failure wording ("We couldn't connect to
        # '<endpoint>' to load the files") — the #874 mirror-down class was
        # journaled as UNKNOWN without these.
        "couldn't connect to",
        "could not connect to",
        "max retries exceeded",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure in name resolution",
        "ssl",
        "proxyerror",
    )),
)

_AUTH_MARKERS = ("401", "403", "gated", "access", "token")


def _coarse_stage(route: str) -> str:
    """Reduce a route to its fixed HEAD segment ("/api/dub/start?x=1" → "dub").

    This is what the opt-in analytics event carries as `stage`: route heads are
    a closed set defined by the routers — path params (ids, filenames, names)
    live in LATER segments and never ride along."""
    try:
        path = (route or "").split("?", 1)[0].strip("/")
        parts = [p for p in path.split("/") if p]
        if parts and parts[0] == "api":
            parts = parts[1:]
        return (parts[0] if parts else "")[:40]
    except Exception:  # noqa: BLE001
        return ""


def classify_exception(exc: BaseException, trace: str = "") -> str:
    """Best-effort classification of an exception into a stable class key.

    Pattern-matching on message text is inherently fuzzy — the goal is
    triage ("which docs page / which hint"), not perfection. UNKNOWN is an
    acceptable answer.
    """
    blob = f"{type(exc).__name__}: {exc}\n{trace}".lower()
    for cls, needles in _CLASS_RULES:
        if cls == "PYANNOTE_LICENSE_REQUIRED":
            # pyannote in the trace alone is too broad (any diarization bug
            # would match); require an auth/gating marker alongside it.
            if "pyannote" in blob and any(m in blob for m in _AUTH_MARKERS):
                return cls
            continue
        if cls == "HOST_MEMORY_EXHAUSTED":
            # Matched by TYPE, not by the "memoryerror" substring that appears
            # in the blob above: `str(MemoryError())` is EMPTY, so a bare
            # `MemoryError` — Python's own name for a failed host allocation,
            # and the reason this class exists — carries no signature at all
            # and used to journal as UNKNOWN. That substring would also claim
            # torch's `OutOfMemoryError`, since it ends in the same letters,
            # telling a GPU host to free system RAM. Exact type, exact intent.
            if type(exc).__name__ == "MemoryError":
                return cls
            # WinError 8 by number: a localized OS message carries no English
            # signature, but the errno-style attribute is language-neutral.
            if getattr(exc, "winerror", None) == 8:
                return cls
        if any(n in blob for n in needles):
            return cls
    return "UNKNOWN"


def _fingerprint(error_class: str, exc: BaseException) -> str:
    import hashlib
    raw = f"{error_class}|{type(exc).__name__}|{scrub_text(str(exc))[:200]}"
    # Dedup key for the journal, not a security boundary.
    return hashlib.sha1(raw.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:16]


def _persist_locked() -> bool:
    """Rewrite the JSONL mirror from the in-memory ring. Caller holds _lock.
    Never raises — losing persistence must not break the exception handler.
    Returns whether the durable mirror was updated."""
    try:
        tmp = JOURNAL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for entry in _entries.values():
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(tmp, JOURNAL_PATH)
        return True
    except Exception:
        logger.warning("Error journal persistence failed; entries remain available in memory")
        return False


def _hydrate() -> None:
    """Load persisted entries at import so 'recent errors' survives restarts
    (and shows the error that killed the previous run)."""
    try:
        with open(JOURNAL_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    fp = entry.get("fingerprint")
                    if fp:
                        _entries[fp] = entry
                except Exception:
                    continue
        while len(_entries) > _MAX_ENTRIES:
            _entries.popitem(last=False)
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("Error journal could not be loaded; starting with an empty in-memory journal")


_hydrate()


def record(exc: BaseException, route: str = "", trace: str = "") -> dict:
    """Record an unhandled exception. Returns the (scrubbed) journal entry.

    Never raises — this runs inside the global exception handler, where a
    second failure would shadow the one being reported.
    """
    try:
        error_class = classify_exception(exc, trace)
        fp = _fingerprint(error_class, exc)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with _lock:
            existing = _entries.get(fp)
            if existing:
                existing["count"] = int(existing.get("count", 1)) + 1
                existing["last_seen"] = now
                existing["route"] = scrub_text(route) or existing.get("route", "")
                _entries.move_to_end(fp)
                entry = existing
            else:
                entry = {
                    "fingerprint": fp,
                    "error_class": error_class,
                    "type": type(exc).__name__,
                    "message": scrub_text(str(exc)),
                    "route": scrub_text(route),
                    "trace": scrub_text(trace)[:_MAX_TRACE_CHARS],
                    "first_seen": now,
                    "last_seen": now,
                    "count": 1,
                }
                _entries[fp] = entry
                while len(_entries) > _MAX_ENTRIES:
                    _entries.popitem(last=False)
            _persist_locked()
        # Opt-in analytics (core/analytics.py): a no-op unless the user opted
        # in AND the build ships a token. Carries the error CLASS and the
        # coarse route head ONLY — never the message, trace, or any path —
        # deduped by fingerprint and hard-capped per session there. Lazy
        # import + never raises: telemetry must not shadow the real error.
        try:
            from core import analytics

            analytics.record_error_event(error_class, fp, stage=_coarse_stage(route))
        except Exception:
            pass
        return entry
    except Exception:
        return {"error_class": "UNKNOWN", "type": type(exc).__name__, "count": 1}


def recent(limit: int = 20) -> list[dict]:
    """Most recent errors first."""
    with _lock:
        items = list(_entries.values())
    return list(reversed(items))[: max(1, min(limit, _MAX_ENTRIES))]


def clear() -> bool:
    """Clear the journal, retaining memory state if durable deletion fails."""
    with _lock:
        try:
            os.remove(JOURNAL_PATH)
        except FileNotFoundError:
            # Idempotent clear: an already-absent durable mirror is success.
            logger.debug("Error journal mirror already absent during clear")
        except OSError:
            logger.warning("Error journal could not be cleared; keeping entries available for retry")
            return False
        _entries.clear()
        return True
