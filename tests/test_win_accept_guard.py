"""#2276 — a transient client reset must not close the Windows listening socket.

CPython's ProactorEventLoop accept loop closes the LISTENING socket on any
OSError from an accept future, so one WinError 64 (AV/VPN loopback
inspection, aborted health probe) left the backend alive but deaf on
127.0.0.1:3900. ``core.win_accept_guard`` wraps ``IocpProactor.accept`` so
transient errors re-arm the accept instead.

The unit tests run on every platform with a scripted fake proactor. The
``_start_serving`` tests drive CPython's *real* proactor accept loop (bound
onto a selector loop) so the fail-before case reproduces the actual bug, not
a model of it. A Windows-only test exercises a real ProactorEventLoop server.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import socket
import sys
from asyncio import proactor_events
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

def _winerr(code: int) -> OSError:
    exc = OSError(22, "The specified network name is no longer available")
    exc.winerror = code  # only set by the constructor on Windows
    return exc


class _Listener:
    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return -1 if self.closed else 7

    def close(self) -> None:
        self.closed = True


class FakeProactor:
    """accept() replays a script: exceptions (future fails), ('raise', exc)
    (synchronous raise), 'pending' (never resolves) or any other value (result)."""

    def __init__(self, loop, script) -> None:
        self._loop = loop
        self.script = list(script)
        self.calls = 0
        self.issued: list[asyncio.Future] = []

    def accept(self, listener):
        self.calls += 1
        step = self.script.pop(0) if self.script else "pending"
        if isinstance(step, tuple) and step[0] == "raise":
            raise step[1]
        fut = self._loop.create_future()
        self.issued.append(fut)
        if isinstance(step, BaseException):
            fut.set_exception(step)
        elif step != "pending":
            fut.set_result(step)
        return fut


@pytest.fixture
def guard():
    """Resolve the module at run time so sys.modules pollution cannot go stale."""
    return importlib.import_module("core.win_accept_guard")


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture
def guarded(guard, monkeypatch):
    """A FakeProactor subclass with the guard applied (fresh per test)."""
    cls = type("GuardedFake", (FakeProactor,), {})
    assert guard.patch_proactor_class(cls)
    monkeypatch.setattr(guard, "_IMMEDIATE_RETRIES", 1000)  # no call_later waits
    return cls


def _settle(loop, fut=None, rounds: int = 200):
    async def spin():
        for _ in range(rounds):
            if fut is not None and fut.done():
                return
            await asyncio.sleep(0)

    loop.run_until_complete(spin())


def test_transient_error_reissues_accept_on_same_listener(loop, guarded):
    conn = object()
    p = guarded(loop, [_winerr(64), _winerr(1236), ConnectionResetError(), (conn, "addr")])
    outer = p.accept(_Listener())
    _settle(loop, outer)
    assert outer.result() == (conn, "addr")
    assert p.calls == 4


def test_synchronous_transient_error_is_retried(loop, guarded):
    p = guarded(loop, [("raise", _winerr(10054)), ("c", "a")])
    outer = p.accept(_Listener())
    _settle(loop, outer)
    assert outer.result() == ("c", "a")


def test_outer_cancel_cancels_inflight_accept(loop, guarded):
    p = guarded(loop, ["pending"])
    outer = p.accept(_Listener())
    outer.cancel()
    _settle(loop)
    assert p.issued[0].cancelled()


def test_outer_cancel_during_backoff_stops_retrying(loop, guarded, monkeypatch, guard):
    monkeypatch.setattr(guard, "_IMMEDIATE_RETRIES", 0)
    # A backoff far above any loop clock resolution: Windows' ~15.6 ms monotonic
    # tick made a 10 ms call_later fire inside _settle, so the retry had already
    # run before the cancel (Smoke (Windows) failure on #2283).
    monkeypatch.setattr(guard, "_BASE_DELAY_S", 30.0)
    monkeypatch.setattr(guard, "_MAX_DELAY_S", 30.0)
    p = guarded(loop, [_winerr(64), ("c", "a")])
    outer = p.accept(_Listener())
    _settle(loop, rounds=5)  # first failure processed, retry now on call_later
    outer.cancel()
    _settle(loop)
    assert p.calls == 1


def test_inner_cancel_cancels_outer(loop, guarded):
    p = guarded(loop, ["pending"])
    outer = p.accept(_Listener())
    p.issued[0].cancel()  # what IocpProactor.close()/_stop_serving does
    _settle(loop, outer)
    assert outer.cancelled()


def test_non_transient_error_propagates_unchanged(loop, guarded):
    err = PermissionError(13, "denied")
    p = guarded(loop, [err, ("c", "a")])
    outer = p.accept(_Listener())
    _settle(loop, outer)
    assert outer.exception() is err
    assert p.calls == 1


def test_synchronous_non_transient_error_raises_like_unwrapped(loop, guarded):
    p = guarded(loop, [("raise", OSError(9, "bad fd"))])
    with pytest.raises(OSError):
        p.accept(_Listener())


def test_transient_error_after_listener_closed_propagates(loop, guarded):
    listener = _Listener()
    listener.closed = True
    err = _winerr(64)
    p = guarded(loop, [err])
    outer = p.accept(listener)
    _settle(loop, outer)
    assert outer.exception() is err


def test_retry_cap_then_propagates(loop, guarded, monkeypatch, guard):
    monkeypatch.setattr(guard, "MAX_CONSECUTIVE_RETRIES", 5)
    p = guarded(loop, [_winerr(64) for _ in range(20)])
    outer = p.accept(_Listener())
    _settle(loop, outer)
    assert getattr(outer.exception(), "winerror", None) == 64
    assert p.calls == 6


def test_backoff_is_bounded_and_grows(guard):
    delays = [guard.retry_delay(n) for n in range(1, guard.MAX_CONSECUTIVE_RETRIES + 1)]
    assert delays[0] == 0.0
    assert delays == sorted(delays)
    assert max(delays) <= 0.5
    assert sum(delays) < 60  # never a tight loop, never an endless stall


def test_classifier(guard):
    assert guard.is_transient_accept_error(_winerr(64))
    assert guard.is_transient_accept_error(ConnectionAbortedError())
    assert not guard.is_transient_accept_error(_winerr(10048))
    assert not guard.is_transient_accept_error(ValueError())


def test_patch_is_idempotent(guard):
    cls = type("F", (FakeProactor,), {})
    assert guard.patch_proactor_class(cls)
    wrapped = cls.accept
    assert not guard.patch_proactor_class(cls)
    assert cls.accept is wrapped


def test_install_is_noop_off_windows(monkeypatch, guard):
    monkeypatch.setattr(sys, "platform", "linux")
    guard.install()  # must not import asyncio.windows_events


# ── CPython's real proactor accept loop ─────────────────────────────────────


class _ServingLoop(asyncio.SelectorEventLoop):
    """Runs BaseProactorEventLoop._start_serving verbatim over a fake proactor."""

    _start_serving = proactor_events.BaseProactorEventLoop._start_serving

    def __init__(self, proactor_cls, script) -> None:
        super().__init__()
        self._proactor = proactor_cls(self, script)
        self._accept_futures: dict = {}
        self.transports: list = []
        self.errors: list = []
        self.set_exception_handler(lambda _lp, ctx: self.errors.append(ctx))

    def _make_socket_transport(self, conn, protocol, extra=None, server=None):
        self.transports.append(conn)


def _serve(proactor_cls):
    listener = socket.socket()
    lp = _ServingLoop(proactor_cls, [_winerr(64), ("conn-1", "a"), ("conn-2", "b")])
    try:
        lp._start_serving(asyncio.Protocol, listener)
        _settle(lp)
        return lp.transports, lp.errors, listener.fileno() != -1
    finally:
        for f in lp._accept_futures.values():
            f.cancel()
        _settle(lp)
        listener.close()
        lp.close()


def test_unguarded_cpython_accept_loop_closes_listener():
    """Documents the root cause: without the guard one WinError 64 kills serving."""
    transports, errors, open_ = _serve(FakeProactor)
    assert not open_ and transports == [] and errors


def test_guarded_cpython_accept_loop_keeps_serving(monkeypatch, guard):
    monkeypatch.setattr(guard, "_IMMEDIATE_RETRIES", 1000)
    cls = type("GuardedFake", (FakeProactor,), {})
    guard.patch_proactor_class(cls)
    transports, errors, open_ = _serve(cls)
    assert open_ and errors == []
    assert transports == ["conn-1", "conn-2"]


def _guard_installed_before_serving(src: str) -> bool:
    """Module-level install() call precedes every uvicorn.run(...) (AST linenos)."""
    tree = ast.parse(src)
    installs = [n.lineno for n in tree.body
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                and getattr(n.value.func, "id", "") == "_install_accept_guard"]
    runs = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run" and getattr(n.func.value, "id", "") == "uvicorn"]
    return bool(installs) and bool(runs) and installs[0] < min(runs)


def test_order_check_rejects_install_after_serving():
    before = "_install_accept_guard()\nimport uvicorn\nuvicorn.run(app)\n"
    after = "import uvicorn\nif x:\n    uvicorn.run(app)\n_install_accept_guard()\n"
    assert _guard_installed_before_serving(before)
    assert not _guard_installed_before_serving(after)
    assert not _guard_installed_before_serving("import uvicorn\nuvicorn.run(app)\n")


def test_main_installs_guard_before_serving():
    src = Path(__file__).resolve().parents[1].joinpath("backend", "main.py").read_text(
        encoding="utf-8")
    assert _guard_installed_before_serving(src), (
        "main.py must call core.win_accept_guard.install at module level, "
        "before any uvicorn.run")


# ── Windows: real ProactorEventLoop ─────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="IocpProactor is Windows-only")
def test_real_proactor_server_survives_winerror_64(guard):
    from asyncio import windows_events

    class Flaky(windows_events.IocpProactor):
        failed = False

        def accept(self, listener):
            if not Flaky.failed:
                Flaky.failed = True
                fut = self._loop.create_future()
                fut.set_exception(OSError(22, "netname deleted", None, 64))
                return fut
            return super().accept(listener)

    guard.patch_proactor_class(Flaky)
    lp = windows_events.ProactorEventLoop(Flaky())
    errors: list = []
    lp.set_exception_handler(lambda _lp, ctx: errors.append(ctx))

    async def main():
        async def echo(reader, writer):
            writer.write(await reader.readline())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        for i in range(3):
            r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 10)
            w.write(b"ping%d\n" % i)
            assert await asyncio.wait_for(r.readline(), 10) == b"ping%d\n" % i
            w.close()
        assert Flaky.failed
        server.close()
        await server.wait_closed()

    try:
        lp.run_until_complete(main())
    finally:
        lp.close()
    assert not [e for e in errors if e.get("message") == "Accept failed on a socket"]
