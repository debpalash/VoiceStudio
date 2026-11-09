"""Announce local setting writes to the Cloud sync engine (desktop-app-guide §5).

``prefs``, ``settings_store`` and the renderer mirror call ``notify(source, key)``
after a write. Writes made while applying a pulled change run inside
``from_sync()`` and stay silent, so a pulled value is never echoed back.
Listeners never break the write that called them.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Callable, Iterator

logger = logging.getLogger("omnivoice.change_hooks")

Listener = Callable[[str, str], None]
_listeners: list[Listener] = []
_from_sync: contextvars.ContextVar[bool] = contextvars.ContextVar("from_sync", default=False)


def subscribe(listener: Listener) -> Callable[[], None]:
    _listeners.append(listener)

    def unsubscribe() -> None:
        if listener in _listeners:
            _listeners.remove(listener)

    return unsubscribe


def notify(source: str, key: str) -> None:
    if _from_sync.get():
        return
    for listener in list(_listeners):
        try:
            listener(source, key)
        except Exception:  # noqa: BLE001 — a sync hook must never fail a settings write
            logger.exception("change hook failed for %s", source)


@contextmanager
def from_sync() -> Iterator[None]:
    token = _from_sync.set(True)
    try:
        yield
    finally:
        _from_sync.reset(token)
