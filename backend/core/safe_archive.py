"""Bounded reads from untrusted ZIP bundles.

Voice bundles (``.omnivoice`` / ``.ovsvoice``) arrive from other people. A
small upload can declare gigabytes of content, thousands of entries, or member
names that point outside any directory they are joined to. Open them through
:func:`open_bounded_zip`, which checks every entry before anything is read,
and read members only through :func:`read_member` / :func:`copy_member`, which
stop at a byte cap even if an entry's declared size is wrong.
"""
from __future__ import annotations

import io
import os
import zipfile

from core.path_security import UnsafePath, safe_relative_path

MAX_MEMBERS = 64
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
_CHUNK = 1024 * 1024


class ArchiveError(ValueError):
    """An archive was refused. ``status`` is the HTTP status to report."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def open_bounded_zip(
    data: bytes,
    *,
    max_members: "int | None" = None,
    max_member_bytes: "int | None" = None,
    max_total_bytes: "int | None" = None,
) -> zipfile.ZipFile:
    """Open ``data`` as a ZIP after validating every entry's name and size.

    Limits default to the module constants, read at call time."""
    max_members = MAX_MEMBERS if max_members is None else max_members
    max_member_bytes = MAX_MEMBER_BYTES if max_member_bytes is None else max_member_bytes
    max_total_bytes = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, EOFError) as exc:
        raise ArchiveError(400, "not a valid ZIP bundle") from exc
    infos = zf.infolist()
    if len(infos) > max_members:
        raise ArchiveError(413, f"bundle has too many entries (max {max_members})")
    total = 0
    for info in infos:
        name = info.filename[:-1] if info.is_dir() else info.filename
        try:
            safe_relative_path(name)
        except UnsafePath as exc:
            raise ArchiveError(400, "bundle contains an unsafe file name") from exc
        if info.file_size > max_member_bytes:
            raise ArchiveError(413, "bundle entry is too large")
        total += info.file_size
        if total > max_total_bytes:
            raise ArchiveError(413, "bundle content is too large")
    return zf


def _stream(zf: zipfile.ZipFile, name: str, max_bytes: int, sink) -> None:
    try:
        with zf.open(name) as src:
            seen = 0
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    return
                seen += len(chunk)
                if seen > max_bytes:
                    raise ArchiveError(413, "bundle entry is too large")
                sink(chunk)
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, ValueError) as exc:
        raise ArchiveError(400, "bundle entry is unreadable") from exc


def read_member(zf: zipfile.ZipFile, name: str, max_bytes: int = MAX_JSON_BYTES) -> bytes:
    """The bytes of ``name``, refusing anything larger than ``max_bytes``."""
    buf = bytearray()
    _stream(zf, name, max_bytes, buf.extend)
    return bytes(buf)


def copy_member(
    zf: zipfile.ZipFile, name: str, dest_path: str, max_bytes: "int | None" = None
) -> None:
    """Stream ``name`` to ``dest_path`` (a path the caller built, never the
    member name). A partial file is removed when the copy is refused."""
    max_bytes = MAX_MEMBER_BYTES if max_bytes is None else max_bytes
    try:
        with open(dest_path, "wb") as dst:
            _stream(zf, name, max_bytes, dst.write)
    except BaseException:
        try:
            os.remove(dest_path)
        except OSError:
            # Best effort: the original error is the one worth reporting.
            pass
        raise


__all__ = [
    "ArchiveError",
    "MAX_JSON_BYTES",
    "MAX_MEMBERS",
    "MAX_MEMBER_BYTES",
    "MAX_TOTAL_BYTES",
    "copy_member",
    "open_bounded_zip",
    "read_member",
]
