"""Keep long-lived HTTP streams visible to proxies while their work is quiet."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncIterator
from typing import TypeVar


T = TypeVar("T")

SILENCE_INTERVAL_SECONDS = 15.0

NDJSON_KEEPALIVE = b"\n"
SSE_KEEPALIVE = ": keepalive\n\n"
NO_BUFFER_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

# A shielded cleanup can outlive the cancelled task that initiated it. Holding
# it here ensures the event loop gets a chance to finish closing the source.
_cleanup_tasks: set[asyncio.Task[None]] = set()


async def _next_item(iterator: AsyncIterator[T]) -> T:
    return await iterator.__anext__()


async def _clean_up_source(
    iterator: AsyncIterator[T], pending: asyncio.Task[T] | None
) -> None:
    if pending is not None:
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except (asyncio.CancelledError, Exception):
            # Cancellation and failures are deliberately consumed here: the
            # former is teardown, and the latter was either delivered by the
            # main loop or became irrelevant when its consumer disconnected.
            pass

    close = getattr(iterator, "aclose", None)
    if close is not None:
        try:
            await close()
        except (asyncio.CancelledError, Exception):
            # Closing is best-effort and must not replace the source exception
            # or cancellation that brought the wrapper here.
            pass


async def _shielded_cleanup(
    iterator: AsyncIterator[T],
    pending: asyncio.Task[T] | None,
    source_context: contextvars.Context,
) -> None:
    cleanup = asyncio.create_task(
        _clean_up_source(iterator, pending), context=source_context
    )
    _cleanup_tasks.add(cleanup)
    cleanup.add_done_callback(_cleanup_tasks.discard)
    await asyncio.shield(cleanup)


def with_keepalive(
    source: AsyncIterator[T], ping: T, interval: float | None = None
) -> AsyncIterator[T]:
    """Yield source items unchanged, inserting *ping* during silent waits."""
    silence_interval = SILENCE_INTERVAL_SECONDS if interval is None else interval

    async def _iterate() -> AsyncIterator[T]:
        source_context = contextvars.copy_context()
        iterator = source_context.run(source.__aiter__)
        pending: asyncio.Task[T] | None = None

        try:
            while True:
                pending = asyncio.create_task(
                    _next_item(iterator), context=source_context
                )
                while True:
                    done, _ = await asyncio.wait(
                        (pending,), timeout=silence_interval
                    )
                    if done:
                        break
                    yield ping

                try:
                    item = pending.result()
                except StopAsyncIteration:
                    pending = None
                    return
                pending = None
                yield item
        finally:
            await _shielded_cleanup(iterator, pending, source_context)

    return _iterate()
