"""Bounded local artifact snapshots shared by model loading and recovery."""
from __future__ import annotations

import os


def tree_fingerprint(path: str | None, *, max_entries: int = 5000) -> tuple:
    """Return relative filenames, sizes and mtimes; unreadable trees never match."""
    if not path:
        return (path,)
    files = []
    visited = 0
    try:
        def unreadable(error: OSError) -> None:
            raise error

        for root, dirs, names in os.walk(path, onerror=unreadable):
            visited += 1 + len(dirs) + len(names)
            if visited > max_entries:
                return (path, object())
            for name in names:
                full = os.path.join(root, name)
                info = os.stat(full)
                files.append((os.path.relpath(full, path), info.st_size, info.st_mtime_ns))
    except OSError:
        return (path, object())
    return (path, tuple(sorted(files)))
