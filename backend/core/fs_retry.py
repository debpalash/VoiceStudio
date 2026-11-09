"""Bounded retries for filesystem steps that a transient lock can fail.

On Windows, antivirus scanners and the indexer open a freshly written or just
executed file for a moment, so renaming or deleting its folder right away
fails with ``WinError 5`` (access denied) or ``WinError 32`` (in use) even
though nothing is wrong. Installers that publish a staged folder must wait
that out instead of failing the install (#2685).
"""
from __future__ import annotations

from collections.abc import Callable
import errno
import time
from typing import TypeVar

T = TypeVar("T")

# About four seconds in total: long enough for a scanner to release a file,
# short enough that a real permission problem still surfaces promptly.
LOCK_RETRY_DELAYS = (0.1, 0.25, 0.5, 1.0, 2.0)

_LOCK_ERRNOS = (errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY)
# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_LOCK_WINERRORS = (5, 32, 33)


def is_transient_lock(exc: OSError) -> bool:
    """True for the errors a briefly held file handle produces."""
    return exc.errno in _LOCK_ERRNOS or getattr(exc, "winerror", None) in _LOCK_WINERRORS


def retry_on_lock(operation: Callable[[], T]) -> T:
    """Run ``operation``, retrying only lock-shaped ``OSError``s, then re-raise."""
    for delay in LOCK_RETRY_DELAYS:
        try:
            return operation()
        except OSError as exc:
            if not is_transient_lock(exc):
                raise
            time.sleep(delay)
    return operation()
