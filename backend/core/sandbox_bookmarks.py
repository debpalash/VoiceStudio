"""Resolve app-scoped folder bookmarks in the sandboxed Python process.

Child processes inherit static sandbox rights, not Powerbox extensions. Each
process therefore starts its own access, including after a new folder pick.
The journal contains opaque IDs/bookmarks, never plaintext home paths.
"""
from __future__ import annotations

import atexit
import base64
import ctypes
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading


class FolderAccess:
    def __init__(self, path: Path, close):
        self.path = path.resolve()
        self.close = close

    def covers(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.path)


def access_bookmark(value: str):
    raw = base64.b64decode(value, validate=True)
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("Invalid sandbox folder bookmark")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [pointer, pointer, ctypes.c_long]
    cf.CFDataCreate.restype = pointer
    cf.CFURLCreateByResolvingBookmarkData.argtypes = [
        pointer, pointer, ctypes.c_ulong, pointer, pointer,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(pointer),
    ]
    cf.CFURLCreateByResolvingBookmarkData.restype = pointer
    cf.CFURLStartAccessingSecurityScopedResource.argtypes = [pointer]
    cf.CFURLStartAccessingSecurityScopedResource.restype = ctypes.c_ubyte
    cf.CFURLGetFileSystemRepresentation.argtypes = [pointer, ctypes.c_ubyte, pointer, ctypes.c_long]
    cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_ubyte
    cf.CFURLStopAccessingSecurityScopedResource.argtypes = [pointer]
    cf.CFURLStopAccessingSecurityScopedResource.restype = None
    cf.CFRelease.argtypes = [pointer]
    cf.CFRelease.restype = None
    data = cf.CFDataCreate(None, ctypes.create_string_buffer(raw), len(raw))
    if not data:
        raise RuntimeError("Sandbox folder bookmark could not be decoded")
    stale, error = ctypes.c_ubyte(), pointer()
    try:
        # WithSecurityScope | WithoutUI | WithoutMounting: background work
        # cannot prompt or mount a volume without another explicit selection.
        url = cf.CFURLCreateByResolvingBookmarkData(
            None, data, (1 << 10) | (1 << 8) | (1 << 9), None, None,
            ctypes.byref(stale), ctypes.byref(error),
        )
    finally:
        cf.CFRelease(data)
        if error.value:
            cf.CFRelease(error)
    if not url:
        raise PermissionError("Sandbox folder authorization needs renewal")
    if not cf.CFURLStartAccessingSecurityScopedResource(url):
        cf.CFRelease(url)
        raise PermissionError("Sandbox folder authorization needs renewal")
    path = ctypes.create_string_buffer(4096)
    if not cf.CFURLGetFileSystemRepresentation(url, 1, path, len(path)):
        cf.CFURLStopAccessingSecurityScopedResource(url)
        cf.CFRelease(url)
        raise PermissionError("Sandbox folder authorization could not be resolved")
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            cf.CFURLStopAccessingSecurityScopedResource(url)
            cf.CFRelease(url)
    try:
        return FolderAccess(Path(os.fsdecode(path.value)), close)
    except (OSError, ValueError, RuntimeError):
        close()
        raise


class FolderGrants:
    def __init__(self, start=access_bookmark):
        self.start = start
        self.leases = {}
        self.lock = threading.Lock()

    def refresh(self, file: Path) -> bool:
        try:
            if file.stat().st_size > 8 * 1024 * 1024:
                return False
            records = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            return False
        if not isinstance(records, list) or any(
            not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("id", ""))) or
            not isinstance(item.get("bookmark"), str) or not item["bookmark"] for item in records
        ) or len({item["id"] for item in records}) != len(records):
            return False
        ok = True
        with self.lock:
            for item in records:
                previous = self.leases.get(item["id"])
                if previous and previous[0] == item["bookmark"]:
                    continue
                try:
                    access = self.start(item["bookmark"])
                except (OSError, ValueError, RuntimeError):
                    ok = False
                    continue
                if previous:
                    previous[1].close()
                self.leases[item["id"]] = (item["bookmark"], access)
        return ok

    def close(self):
        with self.lock:
            for _, access in self.leases.values():
                access.close()
            self.leases.clear()

    def covers(self, path: Path) -> bool:
        with self.lock:
            return any(access.covers(path) for _, access in self.leases.values())


_grants = FolderGrants()
atexit.register(_grants.close)


def restore(required_path: str | None = None) -> bool:
    if sys.platform != "darwin" or os.environ.get("VOICESTUDIO_BUNDLED_CODE_ONLY") != "1":
        return True
    file = os.environ.get("VOICESTUDIO_SANDBOX_BOOKMARKS")
    if not file:
        return required_path is None
    ok = _grants.refresh(Path(file))
    if not ok:
        logging.getLogger("omnivoice.sandbox").warning("Sandbox folder authorization needs renewal")
    if required_path:
        path = Path(required_path)
        # The journal lives in the app's own userData container. It already
        # has static access there; other destinations need an active scope.
        return path.resolve().is_relative_to(Path(file).parent.resolve()) or _grants.covers(path)
    return ok
