"""Power-loss durability for files published with a temp-file + ``os.replace``.

``os.replace`` makes a write atomic against a *process* dying mid-write, but
not against the *machine* losing power: the rename can reach the disk before
the file's data does, so after a hard power-off the published name holds an
empty or torn file. The longform render's resume manifest and cached chapter
audio are exactly the files a power-off mid-render must not lose (#2279).

Flush the temp file's data before the rename and the directory entry after it.
On macOS ``fsync`` only hands data to the drive, which may keep it in its own
volatile cache; ``F_FULLFSYNC`` is the call that reaches stable storage.
Every helper is best-effort: durability is hardening, never a reason to fail
the write that already succeeded.
"""
from __future__ import annotations

import os
import sys


def flush_fd(fd: int) -> None:
    """Flush an open descriptor's data to stable storage (best-effort)."""
    if sys.platform == "darwin":
        try:
            import fcntl

            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except (AttributeError, OSError):
            pass  # filesystem without full-sync support: plain fsync below
    try:
        os.fsync(fd)
    except OSError:
        # Best-effort by contract (module docstring): a filesystem that cannot
        # sync (some network/FUSE mounts) must not fail a write that succeeded.
        return


def flush_file(path: str) -> None:
    """Flush a closed file's data by path (best-effort).

    Opened read-write because Windows' ``fsync`` rejects a read-only handle.
    """
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return
    try:
        flush_fd(fd)
    finally:
        os.close(fd)


def flush_dir(path: str) -> None:
    """Persist a directory's entries, e.g. a rename into it (POSIX only;
    Windows has no directory handle to flush and journals renames itself)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        flush_fd(fd)
    finally:
        os.close(fd)
