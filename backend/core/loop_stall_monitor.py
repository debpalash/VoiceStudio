"""Name the code that blocks the event loop when /health stops answering.

The desktop shell polls ``/health`` with a 1.5 s deadline. When a synchronous
call (or a GIL-hungry thread) holds the loop longer than that, the shell
reports "backend busy / not responding" while the log shows only unrelated 200s
- nothing names the culprit (#2970, #2876, #2840, #2794, #2756). A daemon
thread pings the loop and, if a ping goes unanswered for the threshold, dumps
every thread's Python stack once per stall. Pure diagnostics: never raises into
startup, and ``OMNIVOICE_LOOP_STALL_S=0`` disables it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("omnivoice.loop_stall")
_DEFAULT_THRESHOLD_S = 4.0
_POLL_S = 1.0


def stall_threshold() -> float:
    try:
        return float(os.environ.get("OMNIVOICE_LOOP_STALL_S", _DEFAULT_THRESHOLD_S))
    except ValueError:
        return _DEFAULT_THRESHOLD_S


def start_loop_stall_monitor(
    loop: asyncio.AbstractEventLoop | None = None,
    *,
    threshold: float | None = None,
    poll: float = _POLL_S,
    dump=None,
) -> threading.Event | None:
    """Start the monitor; returns its stop event, or None when disabled."""
    limit = stall_threshold() if threshold is None else threshold
    if limit <= 0:
        return None
    loop = loop or asyncio.get_running_loop()
    stop = threading.Event()
    last_seen = [time.monotonic()]

    def _dump_threads() -> None:
        import faulthandler

        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

    emit = dump or _dump_threads

    def _beat() -> None:
        last_seen[0] = time.monotonic()

    def _run() -> None:
        reported = False
        while not stop.wait(poll):
            try:
                loop.call_soon_threadsafe(_beat)
            except RuntimeError:  # loop closed: the process is exiting
                return
            lag = time.monotonic() - last_seen[0]
            if lag < limit:
                reported = False
            elif not reported:
                reported = True
                logger.warning(
                    "Event loop has not run for %.1fs; /health cannot answer. "
                    "Stacks of all threads follow - the main thread's frame is "
                    "the blocking call.", lag,
                )
                try:
                    emit()
                except Exception:  # diagnostics must never take the app down
                    logger.debug("stall thread dump failed", exc_info=True)

    threading.Thread(target=_run, name="loop-stall-monitor", daemon=True).start()
    return stop
