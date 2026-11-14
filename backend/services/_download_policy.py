"""Bounded, cancellation-safe blocking policy checks for model transfers only."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import math
import queue
import threading
import time
import weakref

from core.url_safety import UnsafeURLError

# Lazily started daemon calls never join asyncio's default executor at shutdown.
# Admission waits instead of rejecting ordinary eight-range concurrency.
_slots = threading.BoundedSemaphore(16)
_DEFAULT_TIMEOUT = 30.0
_POLL = 0.025
_MISSING = object()
_cancel_check = ContextVar('model_download_cancel_check', default=None)
_scope = ContextVar('model_download_policy_scope', default=None)


class _Scope:
    interrupted = False


@contextmanager
def range_policy_scope():
    state = _Scope()
    token = _scope.set(state)
    try:
        yield state
    finally:
        _scope.reset(token)


def set_cancellation_check(check):
    return _cancel_check.set(check)


def reset_cancellation_check(token):
    _cancel_check.reset(token)


def _notify(future_ref, abandoned):
    future = future_ref()
    if not abandoned.is_set() and future is not None and not future.done():
        future.set_result(None)


def _take(results):
    try:
        return results.get_nowait()
    except queue.Empty:
        return _MISSING


def _unwrap(outcome):
    success, value = outcome
    if success:
        return value
    if isinstance(value, UnsafeURLError):
        raise value
    raise UnsafeURLError('Model download destination validation could not complete') from value


async def run_check(function, *args, timeout=None, **kwargs):
    """Use a finite admission/DNS budget without retaining a cancelled loop.

    Only the synchronous validator and its plain arguments reach a worker.
    Completion wakes a live waiter through weak references; cancelled callers
    abandon the result, while the worker always releases its own capacity.
    """
    budget = timeout if type(timeout) in (int, float) and math.isfinite(timeout) else _DEFAULT_TIMEOUT
    deadline = time.monotonic() + max(0.0, budget)
    check_cancelled = _cancel_check.get()
    state = _scope.get()
    slots = _slots
    results = queue.SimpleQueue()
    abandoned = threading.Event()
    ready = asyncio.get_running_loop().create_future()
    loop_ref = weakref.ref(asyncio.get_running_loop())
    ready_ref = weakref.ref(ready)
    outcome = _MISSING

    def poll_cancelled():
        if check_cancelled is not None:
            check_cancelled()

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise UnsafeURLError('Model download destination validation timed out')
        return min(_POLL, left)

    def worker():
        try:
            try:
                result = (True, function(*args, **kwargs))
            except BaseException as error:
                result = (False, error)
            results.put(result)
        finally:
            slots.release()
        if not abandoned.is_set():
            loop = loop_ref()
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(_notify, ready_ref, abandoned)
                except RuntimeError:
                    pass  # The cancelled caller may already have closed its loop.

    try:
        poll_cancelled()
        remaining()
        while not slots.acquire(blocking=False):
            poll_cancelled()
            await asyncio.sleep(remaining())
        try:
            poll_cancelled()
            remaining()
        except BaseException:
            slots.release()
            raise
        try:
            threading.Thread(target=worker, name='model-download-policy', daemon=True).start()
        except Exception as error:
            slots.release()
            raise UnsafeURLError('Model download destination validation is unavailable') from error
        while True:
            outcome = _take(results)
            # A completed refusal stays authoritative even if cancellation races it.
            if outcome is not _MISSING and not outcome[0]:
                return _unwrap(outcome)
            poll_cancelled()
            if outcome is not _MISSING:
                return _unwrap(outcome)
            await asyncio.wait({ready}, timeout=remaining())
    except asyncio.CancelledError:
        if outcome is _MISSING:
            outcome = _take(results)
        if outcome is not _MISSING and not outcome[0]:
            return _unwrap(outcome)
        if state is not None:
            # A raw DNS result may be ready while the caller's public-address
            # filter has not run yet. A cancelled handoff is still incomplete.
            state.interrupted = True
        poll_cancelled()
        raise
    finally:
        abandoned.set()
        ready.cancel()
