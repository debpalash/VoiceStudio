"""Best-effort native-crash traceback diagnostics for backend processes."""
from __future__ import annotations

import faulthandler
import os
import sys
from typing import IO, Optional

_OPT_OUT_VARIABLE = "OMNIVOICE_DISABLE_FAULTHANDLER"
_AFFIRMATIVE = {"1", "on", "true", "yes"}


def _was_opted_out() -> bool:
    raw = os.getenv(_OPT_OUT_VARIABLE)
    return raw is not None and raw.strip().casefold() in _AFFIRMATIVE


def enable_fault_handler(stderr: Optional[IO[str]] = None) -> bool:
    """Request process-wide fatal tracebacks as a best-effort startup aid.

    A supplied stream remains owned by the caller and must outlive the process;
    the runtime retains its descriptor for signal-time writes.
    """
    if _was_opted_out():
        return False
    destination = stderr if stderr is not None else sys.stderr
    try:
        faulthandler.enable(file=destination, all_threads=True)
    except Exception:
        return False
    return True


_FORENSICS_MARK = "_voicestudio_exit_forensics"


def _say(message: str) -> None:
    """One line on stderr, which the desktop shell and the crash record capture."""
    try:
        print(f"[exit-forensics] {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — diagnostics must never raise
        pass


def _caller(depth: int = 2) -> str:
    try:
        frame = sys._getframe(depth)
        return f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
    except Exception:  # noqa: BLE001
        return "unknown"


def install_exit_forensics() -> bool:
    """Make a silent backend exit explain itself (#3887). Idempotent.

    Adds one stderr line for each way the process can end without a traceback:
    a hard ``os._exit`` (with its caller), an uncaught exception on the main
    or a worker thread (including a ``SystemExit`` a thread swallows), and
    normal interpreter shutdown (with the stack that started it). Exit codes
    and behaviour are unchanged. Returns False when already installed.
    """
    import atexit
    import threading
    import traceback

    if getattr(os, _FORENSICS_MARK, False):
        return False

    original_exit = os._exit

    def logged_exit(code=0):
        _say(f"backend exiting via os._exit({code!r}) from {_caller()}")
        original_exit(code)

    logged_exit.__wrapped__ = original_exit  # type: ignore[attr-defined]
    os._exit = logged_exit  # type: ignore[assignment]

    previous_hook = sys.excepthook

    def excepthook(exc_type, exc, tb):
        _say(
            "uncaught exception ends the main thread: "
            + "".join(traceback.format_exception_only(exc_type, exc)).strip()
        )
        previous_hook(exc_type, exc, tb)

    sys.excepthook = excepthook

    previous_thread_hook = threading.excepthook

    def thread_hook(args):
        name = getattr(args.thread, "name", "?")
        _say(
            f"uncaught {args.exc_type.__name__} in thread {name!r}: {args.exc_value}"
            + (" (SystemExit in a thread does not stop the process)" if args.exc_type is SystemExit else "")
        )
        previous_thread_hook(args)

    threading.excepthook = thread_hook

    def at_exit():
        frames = "".join(traceback.format_stack(limit=6)).strip()
        _say(
            f"interpreter shutting down on thread {threading.current_thread().name!r}; "
            f"stack:\n{frames}"
        )

    atexit.register(at_exit)
    setattr(os, _FORENSICS_MARK, True)
    return True

