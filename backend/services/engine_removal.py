"""Engines whose files are being deleted.

A neutral module so the delete route, the sidecar spawner
(``subprocess_backend``) and the installer (``sidecar_install``) can all see the
mark without importing each other. Marks are counted: an overlapping delete
that gives up must not clear the mark of one still removing files.
"""
from __future__ import annotations

import contextlib
import threading

_counts: dict[str, int] = {}
_lock = threading.Lock()


@contextlib.contextmanager
def removing(engine_id: str):
    """Mark ``engine_id`` as being deleted for the duration of the block."""
    with _lock:
        _counts[engine_id] = _counts.get(engine_id, 0) + 1
    try:
        yield
    finally:
        with _lock:
            left = _counts.get(engine_id, 1) - 1
            if left > 0:
                _counts[engine_id] = left
            else:
                _counts.pop(engine_id, None)


def is_removing(engine_id: str) -> bool:
    with _lock:
        return engine_id in _counts
