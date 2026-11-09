"""Report whether this backend is doing work, for idle shutdown by its owner.

The desktop app stops a backend it started for a terminal command once the
backend has been idle for a while. "Idle" must be conservative: a dub, a batch,
a model download or an open stream all keep it alive. Requests and WebSocket
sessions are counted by middleware; long-running work that outlives its request
registers a probe from the module that owns the work, so this module never
imports engine code and new job types extend the rule where they are defined.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("omnivoice.activity")

# Polls of these paths are how owners observe the backend; they are not use.
_PASSIVE_PATHS = frozenset({"/health", "/startup/progress", "/system/activity"})

_lock = threading.Lock()
_inflight = 0
_last_activity = time.monotonic()
_probes: dict[str, Callable[[], bool]] = {}


def register_probe(name: str, probe: Callable[[], bool]) -> None:
    """Register ``probe() -> True`` while work named ``name`` is running."""
    with _lock:
        _probes[name] = probe


def touch() -> None:
    global _last_activity
    with _lock:
        _last_activity = time.monotonic()


def _enter() -> None:
    global _inflight, _last_activity
    with _lock:
        _inflight += 1
        _last_activity = time.monotonic()


def _leave() -> None:
    global _inflight, _last_activity
    with _lock:
        _inflight -= 1
        _last_activity = time.monotonic()


def snapshot() -> dict:
    with _lock:
        inflight = _inflight
        idle = time.monotonic() - _last_activity
        probes = list(_probes.items())
    busy = []
    for name, probe in probes:
        try:
            if probe():
                busy.append(name)
        except Exception:
            # An unreadable registry must keep the backend alive, never stop it.
            logger.debug("activity probe %s failed", name, exc_info=True)
            busy.append(name)
    if inflight or busy:
        idle = 0.0
    return {"inflight": inflight, "busy": sorted(busy), "idle_seconds": round(idle, 1)}


class ActivityMiddleware:
    """Count open HTTP requests (including streams) and WebSocket sessions."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or scope.get("path") in _PASSIVE_PATHS:
            await self.app(scope, receive, send)
            return
        _enter()
        try:
            await self.app(scope, receive, send)
        finally:
            _leave()
