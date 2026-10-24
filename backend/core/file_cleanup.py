"""Reliable file deletion for user-visible destructive operations."""
from __future__ import annotations

import os
import time


class FileCleanupError(OSError):
    """A requested file exists but could not be removed."""


def unlink_if_present(path: str | os.PathLike[str]) -> bool:
    """Delete *path*, returning whether it existed.

    Missing files make delete operations idempotent. Other failures must reach
    the caller so it cannot discard the only record from which cleanup can be
    retried.
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise FileCleanupError("file cleanup failed") from exc
    return True


def sweep_stale_files(directory: str | os.PathLike[str], max_age_seconds: float) -> int:
    """Remove regular files in *directory* not modified for *max_age_seconds*.

    Best effort, non-recursive: for scratch directories whose files are only
    useful briefly. Entries that vanish or cannot be removed are skipped.
    Returns the number of files removed.
    """
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    return removed
