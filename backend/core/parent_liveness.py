"""Terminate a desktop-contained backend when its owning shell disappears."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Optional

# Poll cadence for the Windows pipe watcher. Exit latency after the desktop
# closes its end is bounded by this; the desktop's own kill-on-close Job is the
# hard backstop, so one second is plenty, and a longer period means fewer
# idle wake-ups of a thread that otherwise does nothing.
WINDOWS_PIPE_POLL_INTERVAL_S = 1.0
_FILE_TYPE_PIPE = 3  # winbase.h FILE_TYPE_PIPE

def _exit_after_parent_loss(code: int) -> None:
    """Retire backend-only crash forensics before the desktop-owned exit.

    Losing the containment pipe means the desktop process ended, including an
    Electron development reload.  That is not a backend crash: the shell owns
    this child and the watchdog is deliberately terminating it.  ``os._exit``
    skips FastAPI lifespan cleanup, so clear the run sentinel here first.  A
    real backend abort/OOM never reaches this callback and remains detectable
    on the next start.
    """
    try:
        from core import run_sentinel

        run_sentinel.clear_sentinel()
    except Exception:
        pass
    os._exit(code)

def _watch_parent_pipe(reader: BinaryIO, exit_process: Callable[[int], None]) -> None:
    """Block until the desktop-owned stdin pipe closes, then exit immediately."""
    try:
        while reader.read(1):
            pass
    except (OSError, ValueError):
        # A broken or already-closed parent-owned pipe is equivalent to EOF.
        pass
    exit_process(0)


def _watch_parent_pipe_handle(
    handle: int,
    exit_process: Callable[[int], None],
    *,
    peek: Optional[Callable[[int], Any]] = None,
    read_file: Optional[Callable[[int, int], Any]] = None,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = WINDOWS_PIPE_POLL_INTERVAL_S,
) -> None:
    """Windows twin of :func:`_watch_parent_pipe` that never leaves a read
    pending on the pipe.

    A synchronous ``ReadFile`` parked on the stdin pipe — whether issued through
    the C runtime's ``read()`` or straight to the kernel — deadlocks the
    OpenBLAS DLL initializer that ``import torch`` reaches (numpy's
    ``_multiarray_umath``) in the startup worker: every desktop-spawned backend
    on Windows froze at "Loading ML runtime (PyTorch)" while the identical
    command from a terminal, with no stdin pipe and no watchdog, started in
    seconds. A thread that merely sleeps does not trigger it; only the pending
    read on that pipe does. So instead of blocking in a read, poll with
    ``PeekNamedPipe``: it returns immediately, holds no I/O on the file object,
    drains any keepalive bytes the desktop might write, and fails with
    ``ERROR_BROKEN_PIPE`` the moment the desktop closes its end — which is the
    same EOF signal the POSIX reader gets.
    """
    if peek is None or read_file is None:
        import _winapi  # Windows-only stdlib module; the caller gates on the platform

        peek = peek or _winapi.PeekNamedPipe
        read_file = read_file or _winapi.ReadFile
    try:
        while True:
            available, _ = peek(handle)
            if available:
                # Bytes are already buffered, so this read cannot block.
                read_file(handle, available)
            else:
                sleep(interval)
    except OSError:
        # ERROR_BROKEN_PIPE (109) is how the closed parent end surfaces here.
        pass
    exit_process(0)


def _windows_pipe_handle(reader: Any) -> Optional[int]:
    """The OS handle behind ``reader`` when it is a pipe, else None."""
    try:
        import msvcrt
        import _winapi

        handle = msvcrt.get_osfhandle(reader.fileno())
        if _winapi.GetFileType(handle) != _FILE_TYPE_PIPE:
            return None
        return handle
    except (OSError, ValueError, AttributeError, ImportError):
        return None


def arm_desktop_parent_watchdog() -> bool:
    """Use stdin EOF as an unforgeable parent-liveness signal for desktop runs."""
    if os.environ.get("OMNIVOICE_DESKTOP_CONTAINED") != "1":
        return False
    reader = getattr(sys.stdin, "buffer", None)
    if reader is None:
        return False
    target: Callable[..., None] = _watch_parent_pipe
    args: tuple = (reader, _exit_after_parent_loss)
    if os.name == "nt":
        handle = _windows_pipe_handle(reader)
        if handle is not None:
            target = _watch_parent_pipe_handle
            args = (handle, _exit_after_parent_loss)
        # A non-pipe stdin (file, NUL) cannot have a read pending against a
        # pipe file object, so the blocking reader stays correct there.
    threading.Thread(
        target=target,
        args=args,
        name="desktop-parent-watchdog",
        daemon=True,
    ).start()
    return True
