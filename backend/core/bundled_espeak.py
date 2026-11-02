"""Lease immutable, separately signed espeak images in private MAS runtimes.

espeak has global native state. phonemizer normally isolates each wrapper by
copying its dylib into a temporary directory, which App Sandbox cannot load.
The build stages separate images instead; this module never writes code.
"""
from __future__ import annotations

from pathlib import Path
import threading


class LibraryLease:
    def __init__(self, pool: "NativeInstances", index: int):
        self.pool = pool
        self.index = index
        self.path = pool.paths[index]
        self.library = None
        self.closed = False

    def close(self) -> None:
        with self.pool.lock:
            if self.closed:
                return
            self.closed = True
        try:
            if self.library is not None:
                self.library.espeak_Terminate()
        finally:
            self.library = None
            with self.pool.lock:
                self.pool.available.append(self.index)


class NativeInstances:
    def __init__(self, paths: list[Path], source: Path):
        self.paths = paths
        self.source = source.resolve()
        self.available = list(reversed(range(len(paths))))
        self.lock = threading.Lock()

    def claim(self, source: Path) -> LibraryLease:
        if Path(source).resolve() != self.source:
            raise RuntimeError("The phonemizer library is not the bundled espeak runtime")
        with self.lock:
            if not self.available:
                raise RuntimeError("All bundled phonemizer instances are currently in use")
            index = self.available.pop()
            if not self.paths[index].is_file():
                self.available.append(index)
                raise RuntimeError("A bundled phonemizer instance is missing")
            return LibraryLease(self, index)


_pool: NativeInstances | None = None
_pool_lock = threading.Lock()


def claim(source: Path) -> LibraryLease:
    global _pool
    with _pool_lock:
        if _pool is None:
            packages = Path(__file__).resolve().parent
            runtime = packages.parents[2]
            _pool = NativeInstances(
                [runtime / "native/espeak" / f"{index:03}" / "libespeak-ng.dylib" for index in range(128)],
                packages / "espeakng_loader/libespeak-ng.dylib",
            )
    return _pool.claim(source)
