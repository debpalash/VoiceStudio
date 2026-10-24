"""Deployment-agnostic backend crash forensics (#1164).

The desktop shell already makes backend process deaths self-documenting: its
death watchers write a crash marker (``src-tauri/src/crash.rs``) that the UI
surfaces as an honest "the backend crashed (exit code X)" notice. But that
forensics lives in the SHELL — a ``bun run dev`` browser session, a Docker
deployment, or a LAN-share client has no shell, so when the backend process
dies there (OOM kill, shutdown race, native abort) the only user-visible
signal is "Can't reach the local VoiceStudio backend" with ZERO diagnostics —
exactly the shape of issue #1164.

This module is the backend-side equivalent, watcher-free by design (a dead
process can't report its own death):

  - ``write_sentinel()`` drops ``run_sentinel.json`` in DATA_DIR at startup:
    {pid, started_at, version, last_activity}. ``touch_activity()`` keeps
    ``last_activity`` fresh (throttled, exception-safe) as work starts.
  - ``clear_sentinel()`` removes it on clean lifespan shutdown.
  - ``detect_unclean_shutdown()`` runs at the NEXT startup, before the new
    sentinel is written: a leftover sentinel whose pid is no longer alive
    means the previous run died without running shutdown — an unclean death.
    It is converted into a crash record in ``last_run_crash.json`` carrying
    the window the death happened in, the last known activity, and a scrubbed
    tail of ``omnivoice.log`` — the evidence a #1164-class report needs.

The record store mirrors the shell's marker store semantics on purpose
(``crash.rs``): newest-first, capped at :data:`MAX_RECORDS`, acknowledgment
is a timestamp watermark (never deletion — bug reports still need the
evidence after the user dismissed the notice), and reads are version-gated
so an unacknowledged crash from a build the user upgraded away from can't
resurface as if the new build had crashed. Read paths never write.

A leftover sentinel whose pid IS still alive is a concurrently-running second
instance (two ``uvicorn --reload`` workers, a second container sharing the
volume) — NOT a crash. We leave its sentinel alone and skip writing ours, so
neither instance can misreport the other's normal exit as a death.

``uvicorn --reload`` restarts run the lifespan shutdown (uvicorn shuts the
app down gracefully before the reloader re-execs the worker), so the sentinel
is cleared and a file-change restart never yields a false positive.

Everything here is best-effort and exception-safe: forensics must never
break real work.
"""
from __future__ import annotations

from collections import deque

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Optional

from core.config import DATA_DIR, LOG_PATH
from core.version import APP_VERSION

logger = logging.getLogger("omnivoice.run_sentinel")

SENTINEL_PATH = os.path.join(DATA_DIR, "run_sentinel.json")
CRASH_RECORD_PATH = os.path.join(DATA_DIR, "last_run_crash.json")

#: How many unclean-shutdown records to retain (newest first) — mirrors
#: crash.rs MAX_MARKERS so the two forensics stores age identically.
MAX_RECORDS = 3
#: Log lines captured into a crash record — mirrors the shell's stderr tail.
LOG_TAIL_LINES = 40
#: Minimum seconds between last_activity disk writes. Activity touches sit on
#: hot job-start paths; the throttle keeps them at one tiny JSON write per
#: burst instead of one per request.
ACTIVITY_THROTTLE_S = 2.0

# An idle desktop process can disappear with its owning shell during an OS
# shutdown, package replacement, or a forced development relaunch. Keep that
# forensic record, but do not nag the user unless there is evidence that work
# was interrupted or the backend itself logged a fatal failure.
_ACTIONABLE_LOG_MARKERS = (
    "traceback (most recent call last)",
    "critical",
    "fatal error",
    "out of memory",
    "memoryerror",
    "segmentation fault",
    "access violation",
)

# In-memory run state. `owns` guards clear_sentinel()/touch_activity() so an
# instance that skipped writing (another live instance holds the sentinel)
# can never clobber or delete the other instance's sentinel.
_state: dict[str, Any] = {
    "owns": False,
    "started_at": None,
    "last_activity": None,
    "last_write": 0.0,
}
# touch_activity() runs from FastAPI's request threadpool AND asyncio worker
# tasks; the lock keeps the read-modify-write of _state + the sentinel file
# from interleaving.
_lock = threading.Lock()


# ── Small JSON persistence helpers ─────────────────────────────────────────


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    except Exception:
        return None


def _write_json_atomic(path: str, data: dict) -> None:
    """Atomic write (same pattern as core.prefs) — a process dying mid-flush
    must not leave a torn sentinel that the next startup misreads."""
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sentinel.", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Liveness probe ─────────────────────────────────────────────────────────


def _previous_run_alive(pid: int, started_at: Optional[float]) -> bool:
    """Whether `pid` is a live process that plausibly IS the run that wrote
    the sentinel (i.e. a concurrently-running second instance).

    psutil (already a hard backend dependency) is the primary probe on every
    platform; its create_time defeats pid reuse — a process born meaningfully
    after the sentinel's own started_at cannot be the run that wrote it. The
    POSIX fallback is os.kill(pid, 0). On Windows without psutil there is no
    safe signal-0 probe (os.kill with an arbitrary sig TERMINATES there), so
    we err on "alive": the cost of a wrong "alive" is one missed crash
    record; a wrong "dead" would misreport a healthy instance as crashed.
    """
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return False
        try:
            create = psutil.Process(pid).create_time()
            if started_at and create > float(started_at) + 5.0:
                return False  # pid reused by a younger process — original is dead
        except Exception:
            pass
        return True
    except Exception:
        if os.name == "nt":
            return True
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, owned by someone else
        except Exception:
            return True


# ── Version gating (mirrors crash.rs) ──────────────────────────────────────


def _base_version(version: str) -> str:
    """`"0.3.22-7"` (preview stamp) → `"0.3.22"`."""
    for sep in ("-", "+"):
        version = version.split(sep, 1)[0]
    return version


def _same_release(record_version: str, current_version: str) -> bool:
    """A record with no recorded version never matches — with unknown
    provenance it may predate the running build, and a stale post-upgrade
    crash notice is exactly what the gate prevents (crash.rs semantics)."""
    return bool(record_version) and _base_version(record_version) == _base_version(
        current_version
    )


# ── Sentinel lifecycle ─────────────────────────────────────────────────────


def _sentinel_payload() -> dict:
    return {
        "pid": os.getpid(),
        "started_at": _state["started_at"],
        "version": APP_VERSION,
        "last_activity": _state["last_activity"],
    }


def write_sentinel() -> bool:
    """Mark this run as live. Returns False (and writes nothing) when
    detect_unclean_shutdown() found another live instance holding the
    sentinel. Never raises."""
    with _lock:
        if _state.get("foreign_live"):
            return False
        try:
            _state["started_at"] = time.time()
            _state["last_activity"] = None
            _write_json_atomic(SENTINEL_PATH, _sentinel_payload())
            _state["owns"] = True
            _state["last_write"] = time.time()
            return True
        except Exception:
            logger.debug("run-sentinel write failed (non-fatal)", exc_info=True)
            _state["owns"] = False
            return False


def touch_activity(kind: str, detail: str | None = None) -> None:
    """Record that meaningful work (a generate, a transcribe, a model load)
    just started, so an unclean death can be attributed to it.

    Privacy: `kind`/`detail` must be short closed-set identifiers (task type,
    engine/model name) — NEVER user text or file paths. Cheap (one dict
    update; at most one small JSON write per ACTIVITY_THROTTLE_S) and
    exception-safe: forensics must never break the work it describes.
    """
    try:
        now = time.time()
        with _lock:
            _state["last_activity"] = {
                "ts": now,
                "kind": str(kind)[:40],
                "detail": (str(detail)[:80] if detail else None),
            }
            if not _state["owns"]:
                return
            if now - float(_state["last_write"] or 0) < ACTIVITY_THROTTLE_S:
                return
            _write_json_atomic(SENTINEL_PATH, _sentinel_payload())
            _state["last_write"] = now
    except Exception:
        logger.debug("run-sentinel activity touch failed (non-fatal)", exc_info=True)


def clear_sentinel() -> bool:
    """Clean shutdown — remove the sentinel so the next startup knows this
    run ended on purpose. Only removes a sentinel this run wrote."""
    with _lock:
        if not _state["owns"]:
            return True
        try:
            os.remove(SENTINEL_PATH)
        except FileNotFoundError:
            # Idempotent success: there is no stale marker to misread.
            logger.debug("run sentinel already absent during clear")
        except Exception:
            logger.warning("run-sentinel clear failed; retaining ownership for retry")
            return False
        _state["owns"] = False
        return True


# ── Unclean-shutdown detection + crash records ─────────────────────────────


def _scrubbed_log_tail(lines: int = LOG_TAIL_LINES) -> list[str]:
    """Last `lines` of omnivoice.log, scrubbed — the record can end up in a
    prefilled GitHub issue, so it must never carry secrets or home paths."""
    try:
        from core.scrub import scrub_text

        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=lines)
        return [scrub_text(line.rstrip("\n")) for line in tail]
    except Exception:
        return []


def _build_crash_record(sentinel: dict, now: float) -> dict:
    from core.scrub import scrub_text

    started_at = float(sentinel.get("started_at") or 0) or None
    last_activity = sentinel.get("last_activity") or None
    if isinstance(last_activity, dict):
        last_activity = {
            "ts": last_activity.get("ts"),
            "kind": scrub_text(str(last_activity.get("kind") or ""))[:40],
            "detail": scrub_text(str(last_activity.get("detail") or ""))[:80] or None,
        }
    else:
        last_activity = None
    lower = (last_activity or {}).get("ts") or started_at or now
    return {
        "detected_at": now,
        "started_at": started_at,
        # The death happened somewhere in [last thing we know it did, now].
        "ended_between": [lower, now],
        # Seconds the run was demonstrably alive — a lower bound, not the
        # true uptime (the process may have lived on quietly past `lower`).
        "uptime_hint_s": max(0.0, lower - started_at) if started_at else None,
        "version": str(sentinel.get("version") or ""),
        "last_activity": last_activity,
        "log_tail": _scrubbed_log_tail(),
    }


def warrants_user_notice(record: dict) -> bool:
    """Whether an unclean record is actionable enough to interrupt the user.

    The record remains available to diagnostics either way. A meaningful
    activity marker means a generation/transcription/task may have been lost;
    a strict fatal-log marker catches startup/native crashes that happened
    before an activity could be recorded. Idle shell-owned exits stay quiet.
    """
    activity = record.get("last_activity")
    if isinstance(activity, dict) and str(activity.get("kind") or "").strip():
        return True
    tail = record.get("log_tail")
    if not isinstance(tail, list):
        return False
    joined = "\n".join(str(line).lower() for line in tail[-LOG_TAIL_LINES:])
    return any(marker in joined for marker in _ACTIONABLE_LOG_MARKERS)


def _load_store() -> dict:
    store = _read_json(CRASH_RECORD_PATH) or {}
    records = store.get("records")
    return {
        "acked_ts": float(store.get("acked_ts") or 0),
        "records": records if isinstance(records, list) else [],
    }


def _prune_stale_versions(store: dict, current_version: str) -> bool:
    before = len(store["records"])
    store["records"] = [
        r
        for r in store["records"]
        if isinstance(r, dict) and _same_release(str(r.get("version") or ""), current_version)
    ]
    return len(store["records"]) != before


def detect_unclean_shutdown(now: float | None = None) -> Optional[dict]:
    """Startup check — call BEFORE write_sentinel().

    Returns the crash record written for an uncleanly-ended previous run, or
    None (no sentinel / clean previous exit / another instance is live).
    Never raises.
    """
    now = now or time.time()
    try:
        with _lock:
            _state["foreign_live"] = False
            sentinel = _read_json(SENTINEL_PATH)
            if not sentinel:
                return None
            pid = sentinel.get("pid")
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _previous_run_alive(
                pid, sentinel.get("started_at")
            ):
                # Another VoiceStudio backend is live against this DATA_DIR
                # (second --reload worker, second container on a shared
                # volume). Its sentinel is not evidence of anything — leave
                # it alone and don't write ours over it.
                logger.info(
                    "run_sentinel.json belongs to a live process (pid %s) — "
                    "another instance shares this data dir; skipping crash "
                    "detection and sentinel ownership for this run.",
                    pid,
                )
                _state["foreign_live"] = True
                return None

            record = _build_crash_record(sentinel, now)
            store = _load_store()
            # A fresh record retires other-release records (the version gate
            # would never surface them; don't let them hold rotation slots).
            _prune_stale_versions(store, APP_VERSION)
            store["records"].insert(0, record)
            store["records"] = store["records"][:MAX_RECORDS]
            try:
                _write_json_atomic(CRASH_RECORD_PATH, store)
            except Exception:
                logger.debug("last_run_crash write failed (non-fatal)", exc_info=True)
            try:
                os.remove(SENTINEL_PATH)  # consumed — write_sentinel() re-creates
            except OSError:
                pass
            logger.warning(
                "Previous backend run (pid %s, version %s) ended uncleanly — "
                "crash record written to last_run_crash.json (last activity: %s).",
                pid or "?",
                record["version"] or "?",
                (record["last_activity"] or {}).get("kind") or "none recorded",
            )
            return record
    except Exception:
        logger.debug("unclean-shutdown detection failed (non-fatal)", exc_info=True)
        return None


# ── Read/ack API (consumed by api/routers/system.py) ───────────────────────


def newest_record(current_version: str = APP_VERSION) -> Optional[tuple[dict, bool]]:
    """Newest crash record from the running release + whether the user
    already acknowledged it. STRICTLY READ-ONLY: stale-version records are
    filtered in memory, never pruned to disk here (crash.rs read-path
    contract — a read racing a concurrent write must not clobber it)."""
    try:
        store = _load_store()
        _prune_stale_versions(store, current_version)
        if not store["records"]:
            return None
        record = store["records"][0]
        acked = float(record.get("detected_at") or 0) <= store["acked_ts"]
        return record, acked
    except Exception:
        return None


def acknowledge(current_version: str = APP_VERSION) -> None:
    """Watermark the newest record as seen. Records are retained — the
    bug-report prefill still needs the evidence after the user viewed it."""
    try:
        store = _load_store()
        dirty = _prune_stale_versions(store, current_version)
        if store["records"]:
            newest_ts = float(store["records"][0].get("detected_at") or 0)
            if store["acked_ts"] < newest_ts:
                store["acked_ts"] = newest_ts
                dirty = True
        if dirty:
            _write_json_atomic(CRASH_RECORD_PATH, store)
    except Exception:
        logger.debug("last_run_crash ack failed (non-fatal)", exc_info=True)


def _reset_for_tests() -> None:
    """Reset module state between tests (module state is process-global)."""
    with _lock:
        _state.update(
            {
                "owns": False,
                "started_at": None,
                "last_activity": None,
                "last_write": 0.0,
                "foreign_live": False,
            }
        )
