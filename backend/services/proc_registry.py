"""In-flight subprocess registry for dub jobs.

Tracks ffmpeg/demucs subprocesses per job id so ``POST /dub/abort/{id}``
can kill long-running work. This lives in its own stdlib-only leaf module
so both ``services.dub_pipeline`` and ``services.ffmpeg_utils`` can import
it at module top: ``run_ffmpeg(job_id=...)`` previously had to lazy-import
``register_proc``/``unregister_proc`` from dub_pipeline inside the function
body to dodge the dub_pipeline → ffmpeg_utils → dub_pipeline cycle.

``dub_pipeline`` re-exports every name here (including the private state)
for backward compatibility — ``api.routers.dub_core`` and tests alias them
through that module.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("omnivoice.api")

_active_procs: dict[str, list] = {}
_active_procs_lock = threading.Lock()


def register_proc(job_id: str, proc) -> None:
    """Track an in-flight subprocess so /dub/abort can kill it."""
    with _active_procs_lock:
        _active_procs.setdefault(job_id, []).append(proc)


def unregister_proc(job_id: str, proc) -> None:
    with _active_procs_lock:
        lst = _active_procs.get(job_id)
        if lst and proc in lst:
            lst.remove(proc)
        if lst is not None and not lst:
            _active_procs.pop(job_id, None)


def kill_job_procs(job_id: str) -> None:
    """Kill every subprocess still running under a given job id. Idempotent."""
    with _active_procs_lock:
        procs = list(_active_procs.get(job_id, []))
    for proc in procs:
        try:
            if proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(
                "Failed to kill subprocess for %s: %s",
                job_id.replace("\n", " ").replace("\r", " "), e,
            )
    with _active_procs_lock:
        tracked = _active_procs.get(job_id)
        if tracked is not None:
            # A new subprocess may register while kill() is in progress.
            # Retire only the snapshot this cancellation actually owned.
            for proc in procs:
                if proc in tracked:
                    tracked.remove(proc)
            if not tracked:
                _active_procs.pop(job_id, None)


def has_active_procs(job_id: str) -> bool:
    with _active_procs_lock:
        return bool(_active_procs.get(job_id))
