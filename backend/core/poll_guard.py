"""Keep high-frequency status polls from starving the shared worker pool.

The renderer polls a few cheap status routes (``/model/status``,
``/model/loaded``, ``/workers/target``) every second while anything is
running, from several widgets at once. They were plain sync routes, so each
poll took a thread from the 40-thread pool shared with every other sync route
(and with the sync ``require_admin`` dependency in front of them). Their
bodies touch SQLite, psutil and the CUDA driver; when any of those stalls
behind a model load or a generation, one-per-second polls from several widgets
pile up until the pool is full and the whole API stops answering even though
the process is alive (#2594, #2601, #2247 class).

``PollGuard`` runs the snapshot on its own single-thread executor, shares one
in-flight computation between concurrent callers (per key), and answers with
the last good snapshot when the computation overruns its deadline instead of
waiting on it. A wedged probe therefore costs one private thread, never the
pool.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable

from fastapi import HTTPException

logger = logging.getLogger("omnivoice.poll_guard")

#: Longer than a healthy snapshot, shorter than the shell's own patience.
DEFAULT_DEADLINE_S = 1.5
#: Distinct keys remembered (a caller-supplied key must not grow memory).
_MAX_KEYS = 16
#: Distinct snapshots allowed outstanding on the single worker thread. Callers
#: pick the key (e.g. ``?op=``), so without a bound a wedged worker would let
#: them queue unbounded work behind it.
_MAX_INFLIGHT = 4
#: How long a last-good snapshot may be served in place of an overrunning one.
#: Past this a status is more misleading than a 503, so the client sees "busy".
STALE_MAX_AGE_S = 30.0


class PollGuard:
    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        deadline_s: float = DEFAULT_DEADLINE_S,
        stale_max_age_s: float = STALE_MAX_AGE_S,
    ):
        self.name = name
        self._fn = fn
        self._deadline_s = deadline_s
        self._stale_max_age_s = stale_max_age_s
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"poll-{name}"
        )
        self._inflight: dict[Hashable, asyncio.Future] = {}
        self._last: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def _start(self, key: Hashable, args: tuple) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, self._fn, *args)

        def _done(done: asyncio.Future) -> None:
            if self._inflight.get(key) is done:
                del self._inflight[key]
            if not done.cancelled() and done.exception() is None:
                self._last[key] = (time.monotonic(), done.result())
                self._last.move_to_end(key)
                while len(self._last) > _MAX_KEYS:
                    self._last.popitem(last=False)

        future.add_done_callback(_done)
        return future

    async def get(self, *args: Hashable) -> Any:
        """Fresh snapshot for ``fn(*args)``, or the last good one if it overruns."""
        inflight = self._inflight.get(args)
        # A future bound to another (closed) loop can never finish.
        if inflight is not None and inflight.get_loop() is not asyncio.get_running_loop():
            inflight = None
        if inflight is None:
            # Executor work cannot be cancelled, so refuse new keys rather than
            # queue more snapshots behind a wedged one.
            if len(self._inflight) >= _MAX_INFLIGHT:
                raise self._busy()
            inflight = self._inflight[args] = self._start(args, args)
        try:
            return await asyncio.wait_for(asyncio.shield(inflight), self._deadline_s)
        except asyncio.TimeoutError:
            entry = self._last.get(args)
            fresh = entry is not None and time.monotonic() - entry[0] <= self._stale_max_age_s
            logger.warning(
                "%s snapshot exceeded %.1fs; serving %s",
                self.name,
                self._deadline_s,
                "the last good snapshot" if fresh else "503",
            )
            if fresh:
                return entry[1]
            raise self._busy()

    def _busy(self) -> HTTPException:
        return HTTPException(
            status_code=503,
            detail=f"{self.name} status is busy; retry shortly",
            headers={"Retry-After": "1"},
        )
