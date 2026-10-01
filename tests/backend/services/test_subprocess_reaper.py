"""Idle sidecar reaper (parity Action 13).

A subprocess engine's sidecar holds a process (and, for GPU engines, VRAM) for
the life of the backend. The reaper shuts down sidecars idle past a timeout;
the next request respawns one. These tests drive the stdlib-only echo sidecar
(no torch) and assert the reaper (a) kills an idle sidecar, (b) NEVER touches
one with an op in flight (lock held), (c) respawns transparently afterwards,
and (d) is disabled at a non-positive timeout.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from services.subprocess_backend import (
    SubprocessBackend,
    list_live_sidecars,
    reap_idle_sidecars,
    unload_all_sidecars,
    unload_sidecar,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ECHO_SCRIPT = REPO_ROOT / "backend" / "engines" / "_echo" / "main.py"


class EchoBackend(SubprocessBackend):
    id = "_echo_reaper"
    display_name = "Echo (reaper test)"
    sample_rate = 24000
    supported_languages = ["en"]

    @classmethod
    def is_available(cls):
        return (True, "ready") if ECHO_SCRIPT.is_file() else (False, "missing")

    @classmethod
    def venv_python(cls):
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls):
        return ECHO_SCRIPT


@pytest.fixture
def echo():
    b = EchoBackend()
    yield b
    try:
        b.shutdown()
    except Exception:
        pass


def _spawn_alive(b: EchoBackend) -> None:
    """Bring a sidecar up via a health ping and assert it's live."""
    ok, _ = b.health_check()
    assert ok
    assert b._proc is not None and b._proc.poll() is None


def test_reaper_kills_idle_sidecar_then_respawns(echo):
    _spawn_alive(echo)
    pid = echo._proc.pid

    # Force it past the idle horizon and reap.
    echo._last_used = time.monotonic() - 1000
    reap_idle_sidecars(timeout_s=1.0)
    assert echo._proc is None or echo._proc.poll() is not None  # sidecar gone

    # Next op transparently respawns a fresh sidecar (new pid).
    ok, _ = echo.health_check()
    assert ok
    assert echo._proc is not None and echo._proc.poll() is None
    assert echo._proc.pid != pid


def test_reaper_skips_busy_sidecar(echo):
    _spawn_alive(echo)
    echo._last_used = time.monotonic() - 1000  # "idle" by clock, but…

    # …an op is in flight: hold the per-backend lock as generate/transcribe do.
    # (Assert on THIS backend's process, not the global count — other live
    # backends in the session must not make the assertion flaky.)
    acquired = echo._lock.acquire(blocking=False)
    assert acquired
    try:
        reap_idle_sidecars(timeout_s=1.0)
        assert echo._proc is not None and echo._proc.poll() is None  # not reaped
    finally:
        echo._lock.release()


def test_recent_use_is_not_reaped(echo):
    _spawn_alive(echo)
    echo._touch()  # just used
    reap_idle_sidecars(timeout_s=60.0)
    assert echo._proc is not None and echo._proc.poll() is None


def test_reaper_disabled_at_nonpositive_timeout(echo):
    _spawn_alive(echo)
    echo._last_used = time.monotonic() - 1000
    # A non-positive timeout disables reaping globally — count is always 0.
    assert reap_idle_sidecars(timeout_s=0) == 0
    assert reap_idle_sidecars(timeout_s=-5) == 0
    assert echo._proc is not None and echo._proc.poll() is None  # untouched


def test_reaper_ignores_dead_sidecar(echo):
    _spawn_alive(echo)
    echo.shutdown()  # already down
    echo._last_used = time.monotonic() - 1000
    # Must not raise on an already-dead sidecar; nothing to reap here.
    reap_idle_sidecars(timeout_s=1.0)
    assert echo._proc is None or echo._proc.poll() is not None


def test_idle_seconds_tracks_activity(echo):
    _spawn_alive(echo)
    echo._last_used = time.monotonic() - 5.0
    assert echo.idle_seconds() >= 5.0
    echo._touch()
    assert echo.idle_seconds() < 1.0


# ── On-demand manual unload (parity Action 13, "free VRAM now") ──────────────

def test_list_live_sidecars_reports_running(echo):
    _spawn_alive(echo)
    entry = next((s for s in list_live_sidecars() if s["id"] == echo.id), None)
    assert entry is not None
    assert entry["pid"] == echo._proc.pid
    assert entry["idle_seconds"] >= 0


def test_unload_sidecar_force_kills_regardless_of_idle(echo):
    _spawn_alive(echo)
    pid = echo._proc.pid
    echo._touch()  # freshly used — the idle reaper would leave it alone…
    assert unload_sidecar(echo.id) == 1  # …but a manual unload frees it now
    assert echo._proc is None or echo._proc.poll() is not None
    # Respawns transparently on the next op.
    ok, _ = echo.health_check()
    assert ok and echo._proc.pid != pid


def test_unload_sidecar_skips_busy(echo):
    _spawn_alive(echo)
    acquired = echo._lock.acquire(blocking=False)
    assert acquired
    try:
        assert unload_sidecar(echo.id) == 0  # busy → skipped, not interrupted
        assert echo._proc is not None and echo._proc.poll() is None
    finally:
        echo._lock.release()


def test_unload_all_sidecars_includes_this_one(echo):
    _spawn_alive(echo)
    assert unload_all_sidecars() >= 1
    assert echo._proc is None or echo._proc.poll() is not None


def test_unload_unknown_sidecar_is_noop(echo):
    _spawn_alive(echo)
    assert unload_sidecar("does-not-exist") == 0
    assert echo._proc is not None and echo._proc.poll() is None  # untouched


# ── SubprocessBackend.unload() must be the busy-guarded one ──────────────────

def test_backend_unload_skips_busy_sidecar(echo):
    """TTSBackend.unload() on a mid-synth sidecar must not kill it (MM2-02)."""
    _spawn_alive(echo)
    assert echo._lock.acquire(blocking=False)
    try:
        echo.unload()  # busy → skipped, not interrupted
        assert echo._proc is not None and echo._proc.poll() is None
    finally:
        echo._lock.release()


def test_backend_unload_routes_through_unload_sidecar(echo, monkeypatch):
    seen = []
    # tests/backend/conftest.py re-imports `services` between tests, so patch the
    # globals of the module the class under test actually lives in.
    monkeypatch.setitem(
        SubprocessBackend.unload.__globals__, "unload_sidecar",
        lambda engine_id: seen.append(engine_id) or 0,
    )
    shutdowns = []
    monkeypatch.setattr(EchoBackend, "shutdown", lambda self: shutdowns.append(self.id))
    echo.unload()
    assert seen == [echo.id]
    assert shutdowns == []  # the busy-guarded path, not a bare shutdown()


def test_backend_unload_frees_idle_sidecar(echo):
    _spawn_alive(echo)
    echo.unload()
    assert echo._proc is None or echo._proc.poll() is not None
