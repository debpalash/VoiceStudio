"""Cross-device-safe filesystem primitives.

``os.replace`` is atomic only within one filesystem; across devices it raises
``OSError(EXDEV)`` (surfacing to Windows users as ``[Errno 18]`` / ``[Errno 22]``
in past issue reports — the D:-drive/relocated-models class, #763/#479). Every
current call site derives its temp file from the destination directory, which
keeps same-device semantics — but nothing *enforced* that, and the next writer
that stages in ``%TEMP%`` and renames into a user-relocated data/models dir on
another drive reintroduces the whole class. This helper is the enforcement
point: replace when possible, degrade to copy+fsync+replace when the OS says
the two paths live on different devices.
"""
from __future__ import annotations

import errno
import os
import shutil
import tempfile


def safe_replace(src: str, dst: str) -> None:
    """``os.replace`` with a cross-device fallback.

    Same-device: identical to ``os.replace`` (atomic). Cross-device (EXDEV):
    copy to a temp sibling of ``dst`` (same device as the destination), fsync,
    then atomically replace — and remove ``src``. Not atomic *end-to-end*
    across devices (impossible), but the destination itself still only ever
    transitions atomically from old content to complete new content.
    """
    try:
        os.replace(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    fd, tmp = tempfile.mkstemp(prefix=".xdev-", dir=os.path.dirname(dst) or ".")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        with open(tmp, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass  # best-effort temp cleanup; the replace above already landed or raised
    try:
        os.remove(src)
    except OSError:
        pass  # src may be gone already (another EXDEV fallback won the race)
