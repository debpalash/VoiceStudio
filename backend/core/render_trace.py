"""Bounded, local-only render timings. Never record arguments or exception text.

Stage times are inclusive wall times (overlapping work must not be summed).
A completed trace is frozen: an abandoned GPU job cannot change its report.
"""
from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import logging
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

_STAGES = frozenset({'synthesis', 'join', 'effects', 'save', 'watermark', 'mux', 'cache'})
_current: ContextVar[RenderTrace | None] = ContextVar('render_trace', default=None)
_recent: deque = deque(maxlen=32)
_recent_lock = threading.Lock()
logger = logging.getLogger('omnivoice.render')


class RenderTrace:
    def __init__(self, surface: str):
        if surface not in {'generate', 'audiobook', 'longform', 'preview'}:
            raise ValueError('Unknown render surface')
        self.id = uuid.uuid4().hex[:16]
        self.surface = surface
        self._started = time.perf_counter()
        self._lock = threading.Lock()
        self._stages: dict = {}
        self._finished: dict | None = None

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def add(self, name: str, seconds: float, failed: bool):
        with self._lock:
            if self._finished is not None:
                return
            entry = self._stages.setdefault(name, {'calls': 0, 'seconds': 0.0, 'failures': 0})
            entry['calls'] += 1
            entry['seconds'] += seconds
            entry['failures'] += int(failed)

    def _snapshot(self):
        return {
            'id': self.id, 'surface': self.surface,
            'total_seconds': round(time.perf_counter() - self._started, 6),
            'stages': {
                name: {**entry, 'seconds': round(entry['seconds'], 6)}
                for name, entry in self._stages.items()
            },
        }

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._finished if self._finished is not None else self._snapshot())

    def finish(self, outcome: str):
        with self._lock:
            if self._finished is not None:
                return
            self._finished = {**self._snapshot(), 'outcome': outcome}
            record = copy.deepcopy(self._finished)
        with _recent_lock:
            _recent.append(record)
        logger.info('render trace %s', record)


def recent():
    with _recent_lock:
        return copy.deepcopy(list(_recent))


@contextmanager
def stage(name: str):
    if name not in _STAGES:
        raise ValueError('Unknown render stage')
    trace = _current.get()
    if trace is None:
        yield
        return
    start = time.perf_counter()
    failed = True
    try:
        yield
        failed = False
    finally:
        trace.add(name, time.perf_counter() - start, failed)


def call(name, fn, *args, **kwargs):
    with stage(name):
        return fn(*args, **kwargs)


def timed(name):
    """Instrument a shared operation, inert outside a render request."""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapped(*args, **kwargs):
                with stage(name):
                    return await fn(*args, **kwargs)
            return async_wrapped
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            return call(name, fn, *args, **kwargs)
        return wrapped
    return decorate


def bind(fn):
    """Carry only render context into a worker, preserving its other contexts."""
    trace = _current.get()
    if trace is None:
        return fn
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with trace.activate():
            return fn(*args, **kwargs)
    return wrapped


class RenderTraceMiddleware:
    """Trace the entire ASGI response, including streaming and disconnects.

    'complete' means transport completion, not synthesis success: SSE can
    report handled failures with HTTP 200. Stage failures remain visible.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        path = scope.get('path', '')
        surface = {
            '/generate': 'generate', '/audiobook': 'audiobook',
            '/longform/render': 'longform', '/audiobook/preview': 'preview',
        }.get(path)
        if path.startswith('/audiobook/resume/'):
            surface = 'audiobook'
        if scope['type'] != 'http' or scope.get('method') != 'POST' or surface is None:
            await self.app(scope, receive, send)
            return
        trace = RenderTrace(surface)
        status = 500
        complete = False
        disconnected = False

        async def traced_send(message):
            nonlocal status, complete
            await send(message)
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body' and not message.get('more_body', False):
                complete = True

        async def traced_receive():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        outcome = 'failed'
        with trace.activate():
            try:
                await self.app(scope, traced_receive, traced_send)
                outcome = 'failed' if status >= 400 else 'complete' if complete else 'interrupted'
                if disconnected and not complete:
                    outcome = 'interrupted'
            except asyncio.CancelledError:
                outcome = 'interrupted'
                raise
            finally:
                trace.finish(outcome)
