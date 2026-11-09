"""#3887: a backend that exits silently must say who ended it."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")


def _run(body: str):
    script = "import sys, os, threading\nsys.path.insert(0, %r)\n" % BACKEND
    script += "from core.crash_diagnostics import install_exit_forensics\n"
    script += textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)


def test_os_exit_names_code_and_caller():
    r = _run("install_exit_forensics()\nos._exit(3)\n")
    assert r.returncode == 3
    assert "os._exit(3)" in r.stderr and "<string>" in r.stderr


def test_thread_exception_is_reported_and_exit_code_unchanged():
    r = _run(
        """
        install_exit_forensics()
        def boom():
            raise SystemExit(5)
        t = threading.Thread(target=boom, name="worker-x"); t.start(); t.join()
        """
    )
    assert r.returncode == 0
    assert "SystemExit in thread 'worker-x'" in r.stderr


def test_main_exception_and_atexit_and_idempotent():
    r = _run(
        """
        assert install_exit_forensics() is True
        assert install_exit_forensics() is False
        raise RuntimeError("kaput")
        """
    )
    assert r.returncode == 1
    assert "uncaught exception ends the main thread: RuntimeError: kaput" in r.stderr
    assert r.stderr.count("interpreter shutting down") == 1
    assert "Traceback" in r.stderr


def test_idle_worker_survives_system_exit_from_unload(monkeypatch, caplog):
    import services.model_manager as mm

    class Stop(Exception):
        pass

    ticks = iter([None])
    real_sleep = asyncio.sleep

    async def sleep(_s):
        try:
            next(ticks)
        except StopIteration:
            raise Stop from None
        await real_sleep(0)

    def exits():
        raise SystemExit(1)

    monkeypatch.setattr(mm.asyncio, "sleep", sleep)
    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_last_used", 0.0)
    monkeypatch.setattr(mm, "_resolve_idle_timeout", lambda: 1.0)
    monkeypatch.setattr(mm, "unload_shared_model", exits)
    monkeypatch.setattr(mm, "_release_idle_side_models", lambda t: None)

    with caplog.at_level("WARNING"), pytest.raises(Stop):
        asyncio.run(mm.idle_worker())
    assert "idle model unload failed" in caplog.text
    assert "SystemExit 1" in caplog.text or "tried to exit the process" in caplog.text
