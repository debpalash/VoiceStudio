"""In-process registry of voice reference files held by running renders (#2535).

A long render (audiobook, Stories, dub, batch) resolves a voice's reference
path from the profile row and re-reads that file for every segment. When the
profile moves to a new take, the superseded file is retired, not deleted, and
the retired-file sweep must not remove it while any render still holds it, no
matter how long that render has been running.

A render opens a :class:`VoiceFileLease`, calls :meth:`VoiceFileLease.hold` on
every reference path it resolves, and releases the lease when it ends.
:func:`remove_if_unused` is the sweep's only deletion path: it checks the
registry and deletes under the same lock, so a file is never removed while a
lease holds it.
"""
from __future__ import annotations

import os
import threading
from collections import Counter
from typing import Optional

_lock = threading.Lock()
_held: Counter[str] = Counter()


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


class VoiceFileLease:
    """Reference files one render uses; thread-safe, released exactly once."""

    def __init__(self) -> None:
        self._paths: set[str] = set()
        self._released = False

    def hold(self, path: Optional[str]) -> Optional[str]:
        """Mark ``path`` in use until :meth:`release`; returns it unchanged."""
        if not path:
            return path
        key = _key(path)
        with _lock:
            if not self._released and key not in self._paths:
                self._paths.add(key)
                _held[key] += 1
        return path

    def release(self) -> None:
        with _lock:
            if self._released:
                return
            self._released = True
            for key in self._paths:
                _held[key] -= 1
                if _held[key] <= 0:
                    del _held[key]
            self._paths.clear()

    def __enter__(self) -> "VoiceFileLease":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def hold(lease: Optional[VoiceFileLease], path: Optional[str]) -> Optional[str]:
    """``lease.hold(path)`` that tolerates callers without a lease."""
    return lease.hold(path) if lease is not None else path


def in_use(path: str) -> bool:
    with _lock:
        return _held.get(_key(path), 0) > 0


def remove_if_unused(path: str) -> bool:
    """Delete ``path`` unless a lease holds it. Returns False when held.

    Raises ``OSError`` from the removal itself (``FileNotFoundError``
    included) so the caller decides how to treat it.
    """
    with _lock:
        if _held.get(_key(path), 0) > 0:
            return False
        os.remove(path)
        return True
