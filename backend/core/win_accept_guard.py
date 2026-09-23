"""Windows: keep the backend's listening socket alive through transient accept errors.

#2276. On Windows asyncio uses ``ProactorEventLoop``. Its accept loop
(``BaseProactorEventLoop._start_serving.loop`` in CPython's
``asyncio/proactor_events.py``) catches ``OSError`` from each accept and, while
the socket is still open, calls the exception handler ("Accept failed on a
socket") and then **closes the listening socket**. A single client that resets
its connection between ``AcceptEx`` completing and the accept being finalised —
``ERROR_NETNAME_DELETED`` (WinError 64), ``ERROR_CONNECTION_ABORTED`` (1236),
``WSAECONNABORTED`` (10053) or ``WSAECONNRESET`` (10054) — therefore stops the
server permanently while the process keeps running. Antivirus/VPN loopback
inspection and aborted health probes trigger exactly that, so the desktop
shell saw a live backend that never answered on 127.0.0.1 and timed out.

The selector loops used on macOS/Linux already treat these as non-events
(``selector_events._accept_connection`` ignores ``ConnectionAbortedError``), so
this guard brings Windows in line with them rather than adding new behaviour.

Where the fix sits — ``IocpProactor.accept`` rather than ``_start_serving``:
overriding ``_start_serving`` would mean copying a private CPython function
whose signature and body change between versions (``ssl_shutdown_timeout``
arrived in 3.11, the body keeps shifting). ``IocpProactor.accept(listener)``
has had the same contract since 3.4: return a future resolving to
``(conn, addr)``. Wrapping it leaves CPython's accept loop untouched, covers
every server on the loop (main API, network-share server, ``loop.sock_accept``),
and needs no knowledge of uvicorn. The wrapper returns an outer future that:

* re-issues ``accept`` on the same listener when the inner accept fails with a
  transient client-side error and the listener is still open (fileno != -1);
* propagates non-transient errors, and errors after the listener closed,
  unchanged — so ``Server.close()`` / ``_stop_serving`` behave as before;
* cancels the in-flight inner accept (or pending retry) when it is cancelled,
  and mirrors inner cancellation (``IocpProactor.close`` / ``_stop_serving``);
* caps consecutive retries with a short exponential backoff (via
  ``loop.call_later``) and then propagates, so a listener that fails every
  accept cannot spin the loop forever.

Retries log at WARNING at most once a minute and at DEBUG otherwise.
``install()`` is a no-op off Windows and on repeat calls.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("omnivoice.win_accept_guard")

# Client-side resets that only lose that one connection. winerror values from
# winerror.h / winsock2.h; ConnectionReset/AbortedError cover the errno mapping.
TRANSIENT_WINERRORS = frozenset({
    64,     # ERROR_NETNAME_DELETED — the reported case
    1236,   # ERROR_CONNECTION_ABORTED
    10053,  # WSAECONNABORTED
    10054,  # WSAECONNRESET
})

MAX_CONSECUTIVE_RETRIES = 50
_IMMEDIATE_RETRIES = 3
_BASE_DELAY_S = 0.01
_MAX_DELAY_S = 0.5
_WARN_INTERVAL_S = 60.0

_MARKER = "_omnivoice_accept_guard"
# Mutable holder (not a rebound global): monotonic time of the last WARNING.
_warn_state = {"last": float("-inf")}


def is_transient_accept_error(exc: BaseException) -> bool:
    """True if ``exc`` is a per-client reset that must not kill the listener."""
    if not isinstance(exc, OSError):
        return False
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return True
    return getattr(exc, "winerror", None) in TRANSIENT_WINERRORS


def retry_delay(attempt: int) -> float:
    """Seconds to wait before retry number ``attempt`` (1-based)."""
    if attempt <= _IMMEDIATE_RETRIES:
        return 0.0
    return min(_MAX_DELAY_S, _BASE_DELAY_S * 2 ** (attempt - _IMMEDIATE_RETRIES - 1))


def _log_retry(exc: OSError, attempt: int) -> None:
    now = time.monotonic()
    level = logging.DEBUG
    if now - _warn_state["last"] >= _WARN_INTERVAL_S:
        _warn_state["last"] = now
        level = logging.WARNING
    logger.log(
        level,
        "Transient accept failure on the listening socket (%r); re-arming accept "
        "(retry %d/%d). Usually a client reset by antivirus/VPN loopback "
        "inspection or an aborted probe; the server keeps serving. (#2276)",
        exc, attempt, MAX_CONSECUTIVE_RETRIES,
    )


def _close_accepted(fut: Any) -> None:
    """Close the socket of an accept that completed after we stopped caring."""
    if fut.cancelled() or fut.exception() is not None:
        return
    with contextlib.suppress(Exception):  # best-effort cleanup
        fut.result()[0].close()


def make_resilient_accept(orig_accept: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    """Wrap an ``IocpProactor.accept``-shaped method with transient-error retry."""

    def accept(self: Any, listener: Any) -> Any:
        loop = self._loop
        outer = loop.create_future()
        inner: Any = None
        handle: Any = None
        failures = 0

        def fail_or_retry(exc: BaseException) -> None:
            nonlocal failures, handle
            if (
                not is_transient_accept_error(exc)
                or listener.fileno() == -1
                or failures >= MAX_CONSECUTIVE_RETRIES
            ):
                if failures >= MAX_CONSECUTIVE_RETRIES and is_transient_accept_error(exc):
                    logger.error(
                        "accept failed %d times in a row (%r); giving up on this "
                        "listener. (#2276)", failures, exc,
                    )
                outer.set_exception(exc)
                return
            failures += 1
            _log_retry(exc, failures)  # type: ignore[arg-type]
            delay = retry_delay(failures)
            handle = loop.call_later(delay, attempt) if delay else loop.call_soon(attempt)

        def on_inner_done(fut: Any) -> None:
            nonlocal inner
            inner = None
            if outer.done():  # outer cancelled while this accept was in flight
                _close_accepted(fut)
                return
            if fut.cancelled():
                outer.cancel()
                return
            exc = fut.exception()
            if exc is None:
                outer.set_result(fut.result())
            else:
                fail_or_retry(exc)

        def attempt(first: bool = False) -> None:
            nonlocal inner, handle
            handle = None
            if outer.done():
                return
            try:
                inner = orig_accept(self, listener)
            except OSError as exc:
                # A non-retryable synchronous failure on the first call raises
                # exactly as the unwrapped method would.
                if first and (not is_transient_accept_error(exc) or listener.fileno() == -1):
                    raise
                fail_or_retry(exc)
                return
            inner.add_done_callback(on_inner_done)

        def on_outer_done(fut: Any) -> None:
            if not fut.cancelled():
                return
            if inner is not None:
                inner.cancel()
            if handle is not None:
                handle.cancel()

        attempt(first=True)
        outer.add_done_callback(on_outer_done)
        return outer

    setattr(accept, _MARKER, True)
    accept.__wrapped__ = orig_accept  # type: ignore[attr-defined]
    accept.__name__ = getattr(orig_accept, "__name__", "accept")
    accept.__doc__ = getattr(orig_accept, "__doc__", None)
    return accept


def patch_proactor_class(cls: type) -> bool:
    """Idempotently wrap ``cls.accept``. Returns True if it patched."""
    current = cls.__dict__.get("accept")
    if current is None:
        current = cls.accept
    elif getattr(current, _MARKER, False):
        return False
    cls.accept = make_resilient_accept(current)  # type: ignore[attr-defined]
    return True


def install() -> None:
    """Patch ``asyncio.windows_events.IocpProactor.accept``. No-op off Windows.

    Must run before the server starts accepting (called from the top of
    ``main.py``, which every launch path imports before uvicorn binds). The
    patch is class-level, so loops created earlier are covered too.
    """
    if sys.platform != "win32":
        return
    from asyncio import windows_events

    patch_proactor_class(windows_events.IocpProactor)
