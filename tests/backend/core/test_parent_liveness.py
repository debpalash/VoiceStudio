import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

def test_parent_pipe_eof_exits_cleanly():
    from core.parent_liveness import _watch_parent_pipe

    exits = []
    _watch_parent_pipe(io.BytesIO(b""), exits.append)
    assert exits == [0]


def test_parent_pipe_ignores_bytes_until_eof():
    from core.parent_liveness import _watch_parent_pipe

    exits = []
    _watch_parent_pipe(io.BytesIO(b"keepalive"), exits.append)
    assert exits == [0]


def test_watchdog_is_disabled_outside_desktop(monkeypatch):
    from core.parent_liveness import arm_desktop_parent_watchdog

    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    assert arm_desktop_parent_watchdog() is False


def test_desktop_child_exits_when_parent_closes_stdin():
    env = os.environ.copy()
    env["OMNIVOICE_DESKTOP_CONTAINED"] = "1"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from core.parent_liveness import arm_desktop_parent_watchdog; "
            "arm_desktop_parent_watchdog(); print('ready', flush=True); "
            "__import__('time').sleep(30)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=Path(__file__).parents[3] / "backend",
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        child.stdin.close()
        assert child.wait(timeout=3) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


# ── Windows: the watchdog must never leave a read pending on the stdin pipe ──
# A synchronous ReadFile parked on the desktop-owned stdin pipe (via the C
# runtime's read() or straight to the kernel) deadlocked the OpenBLAS DLL
# initializer that `import torch` reaches in the startup worker: every
# desktop-spawned backend on Windows froze at "Loading ML runtime" while the
# same command from a terminal (no stdin pipe, no watchdog) started in seconds.
# A thread that only sleeps is harmless; the pending read is the trigger. The
# Windows watcher therefore polls PeekNamedPipe and reads only bytes that are
# already buffered, so no I/O is ever outstanding on that file object.


def _peek_sequence(events):
    """`events` items: int → bytes available; OSError → broken pipe."""
    it = iter(events)

    def peek(handle):
        ev = next(it)
        if isinstance(ev, BaseException):
            raise ev
        return (ev, 0)

    return peek


def test_windows_watcher_exits_on_broken_pipe_without_blocking_reads():
    from core.parent_liveness import _watch_parent_pipe_handle

    reads, sleeps, exits = [], [], []
    _watch_parent_pipe_handle(
        7,
        exits.append,
        peek=_peek_sequence([0, 0, OSError(109, "The pipe has been ended")]),
        read_file=lambda h, n: reads.append((h, n)),
        sleep=sleeps.append,
        interval=0.01,
    )
    assert exits == [0]
    assert reads == []  # nothing buffered → never a read, let alone a pending one
    assert sleeps == [0.01, 0.01]


def test_windows_watcher_drains_only_buffered_bytes_until_eof():
    from core.parent_liveness import _watch_parent_pipe_handle

    reads, exits = [], []
    _watch_parent_pipe_handle(
        7,
        exits.append,
        peek=_peek_sequence([3, 0, 1, OSError(109, "The pipe has been ended")]),
        read_file=lambda h, n: reads.append((h, n)) or (b"x" * n, 0),
        sleep=lambda s: None,
    )
    assert exits == [0]
    # Reads are sized to exactly what PeekNamedPipe reported, so they return
    # immediately instead of parking on the pipe.
    assert reads == [(7, 3), (7, 1)]


def test_windows_watchdog_arms_pipe_poller_on_pipe_stdin(monkeypatch):
    import types
    import core.parent_liveness as pl

    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(pl.os, "name", "nt")

    class FakeBuffer:
        def fileno(self):
            return 0

    monkeypatch.setattr(pl.sys, "stdin", types.SimpleNamespace(buffer=FakeBuffer()))
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(get_osfhandle=lambda fd: 0xABC))
    monkeypatch.setitem(sys.modules, "_winapi", types.SimpleNamespace(GetFileType=lambda h: 3))

    started = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.update(target=target, args=args, daemon=daemon)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(pl.threading, "Thread", FakeThread)
    assert pl.arm_desktop_parent_watchdog() is True
    assert started["started"] is True
    assert started["target"] is pl._watch_parent_pipe_handle
    assert started["args"] == (0xABC, pl._exit_after_parent_loss)
    assert started["daemon"] is True


def test_windows_watchdog_keeps_blocking_reader_for_non_pipe_stdin(monkeypatch):
    """A file/NUL stdin has no pipe file object to deadlock against, and a
    blocking read on it returns EOF promptly — keep the shared reader there."""
    import types
    import core.parent_liveness as pl

    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(pl.os, "name", "nt")

    class FakeBuffer:
        def fileno(self):
            return 0

    fake_stdin = types.SimpleNamespace(buffer=FakeBuffer())
    monkeypatch.setattr(pl.sys, "stdin", fake_stdin)
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(get_osfhandle=lambda fd: 0xABC))
    monkeypatch.setitem(sys.modules, "_winapi", types.SimpleNamespace(GetFileType=lambda h: 1))  # FILE_TYPE_DISK

    started = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.update(target=target, args=args)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(pl.threading, "Thread", FakeThread)
    assert pl.arm_desktop_parent_watchdog() is True
    assert started["target"] is pl._watch_parent_pipe
    assert started["args"] == (fake_stdin.buffer, pl._exit_after_parent_loss)


# ── Windows integration: the real backend must get past the ML import ────────
# The deadlock needs the real startup: numpy's OpenBLAS DLL loaded through
# `import torch` in the startup worker while the desktop watchdog is armed on a
# piped stdin. Smaller reproductions (a pending read + `import torch` in a bare
# child) do not trigger it, so this spawns the actual backend exactly as the
# desktop shell does. It fails on the pre-fix watchdog by timing out in the
# "ml_imports" step and passes in well under a minute on the fix. Windows-only:
# CI's backend job runs on Linux, so this is exercised on Windows dev machines.


@pytest.mark.skipif(sys.platform != "win32", reason="desktop stdin-pipe watchdog deadlock is Windows-only")
def test_desktop_spawned_backend_gets_past_ml_imports_on_windows(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("uvicorn")
    import json
    import socket
    import threading
    import time
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    root = Path(__file__).parents[3]
    env = os.environ.copy()
    env.update(
        {
            "OMNIVOICE_DESKTOP_CONTAINED": "1",  # arms the watchdog on the stdin pipe
            "OMNIVOICE_PORT": str(port),
            "OMNIVOICE_DATA_DIR": str(tmp_path / "data"),
            "OMNIVOICE_CACHE_DIR": str(tmp_path / "hf_cache"),
            "HF_HUB_CACHE": str(tmp_path / "hf_cache"),  # never the developer's populated cache
            "HF_HUB_OFFLINE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        }
    )
    env.pop("PYTHONPATH", None)
    child = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,   # the desktop shell keeps this open and never writes
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for stream in (child.stdout, child.stderr):
        threading.Thread(target=lambda s=stream: s.read(), daemon=True).start()
    seen = []
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            if child.poll() is not None:
                pytest.fail(f"backend exited early with {child.returncode}; progress seen: {seen[-3:]}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/startup/progress", timeout=2) as resp:
                    progress = json.load(resp)
            except (OSError, ValueError):
                time.sleep(1)
                continue
            status = progress.get("status")
            seen.append((status, progress.get("step")))
            done = {s["id"] for s in progress.get("steps", []) if s.get("state") == "done"}
            # Positive evidence only: the ML import step finished, or startup is
            # ready. Any other terminal state is the failure this test exists for.
            if "ml_imports" in done or status == "ready":
                return
            if status != "starting" or progress.get("error"):
                pytest.fail(
                    f"backend startup reported status={status!r} error={progress.get('error')!r}; "
                    f"progress seen: {seen[-3:]}"
                )
            time.sleep(1)
        pytest.fail(f"backend never got past ml_imports in 180s (deadlocked watchdog?); last progress: {seen[-3:]}")
    finally:
        child.kill()
        child.wait()


def test_parent_loss_retires_crash_sentinel_before_immediate_exit(monkeypatch):
    from core import parent_liveness as pl
    from core import run_sentinel

    cleared = []
    exited = []
    monkeypatch.setattr(run_sentinel, "clear_sentinel", lambda: cleared.append(True))
    monkeypatch.setattr(pl.os, "_exit", exited.append)

    pl._exit_after_parent_loss(0)

    assert cleared == [True]
    assert exited == [0]


def test_windows_pipe_poll_is_not_a_busy_wakeup():
    # Idle CPU: this thread wakes for the life of the backend, so the default
    # period must not be sub-second, while exit latency stays bounded.
    from core.parent_liveness import WINDOWS_PIPE_POLL_INTERVAL_S

    assert 1.0 <= WINDOWS_PIPE_POLL_INTERVAL_S <= 2.0
