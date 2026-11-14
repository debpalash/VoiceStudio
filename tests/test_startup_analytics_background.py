"""Analytics disk I/O must not hold the backend's listening/ready boundary."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import asyncio
import threading
from types import SimpleNamespace


def test_blocked_analytics_config_read_does_not_hold_health_ready(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    script = r'''
import asyncio, os, time
from pathlib import Path
import main
from core import analytics
original = analytics._CONFIG_PATH
class BlockedConfig:
    def read_text(self, **kwargs):
        Path(os.environ["ANALYTICS_ENTERED"]).touch()
        while not Path(os.environ["ANALYTICS_RELEASE"]).exists():
            time.sleep(0.01)
        return original.read_text(**kwargs)
analytics._CONFIG_PATH = BlockedConfig()
# Isolate the readiness boundary from unrelated model/runtime work.
main._EAGER = False
main._phase_a_build = main._phase_a_finished.set
main._phase_a_finalize = lambda: None
async def phase_b(app):
    pass
main._phase_b = phase_b
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["TEST_PORT"]), log_level="error")
'''
    env = {**os.environ, "OMNIVOICE_EAGER_INIT": "0",
           "OMNIVOICE_DATA_DIR": str(tmp_path / "data"),
           "OMNIVOICE_ENV_FILE": str(tmp_path / "user-env"),
           "OMNIVOICE_STARTUP_WATCHDOG_S": "0", "TEST_PORT": str(port),
           "ANALYTICS_ENTERED": str(entered), "ANALYTICS_RELEASE": str(release),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}
    child = subprocess.Popen([sys.executable, "-c", script], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not entered.exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.02)
        assert entered.exists(), "analytics did not reach the blocked config read"
        ready = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                    ready = json.load(response)["status"] == "ok"
            except (URLError, TimeoutError):
                pass
            if ready:
                break
            time.sleep(0.02)
        assert ready, "blocked analytics disk I/O prevented /health readiness"
        assert not release.exists()
    finally:
        release.touch()
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def test_shutdown_tracks_each_running_analytics_worker(monkeypatch):
    import main
    from core import analytics

    entered, release = threading.Event(), threading.Event()
    crashes = []

    def blocked(crash_record, **kwargs):
        crashes.append(crash_record)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(analytics, "record_startup_lifecycle", blocked)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        for crash in (None, {"uptime_hint_s": 30}):
            entered.clear()
            release.clear()
            main._start_analytics_lifecycle(app, crash)
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                worker = app.state.analytics_worker
                waiter = asyncio.create_task(main._wait_for_analytics_thread(app))
                await asyncio.sleep(0.02)
                assert not waiter.done()
                assert not worker.done()
                release.set()
                await asyncio.wait_for(waiter, 2)
                assert worker.done() and not worker.cancelled()
            finally:
                release.set()
        assert crashes == [None, {"uptime_hint_s": 30}]

    asyncio.run(scenario())


def test_shutdown_cancels_unsubmitted_analytics(monkeypatch):
    import main
    from core import analytics

    calls = []
    monkeypatch.setattr(analytics, "record_startup_lifecycle", lambda *a, **kw: calls.append(a))

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_analytics_lifecycle(app, None)
        await main._wait_for_analytics_thread(app)
        assert app.state.analytics_worker is None
        assert app.state.analytics_task.cancelled()
        assert calls == []

    asyncio.run(scenario())


def test_analytics_failure_is_non_fatal(monkeypatch):
    import main
    from core import analytics

    def broken(*a, **kw):
        raise OSError("disk unavailable")

    monkeypatch.setattr(analytics, "record_startup_lifecycle", broken)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_analytics_lifecycle(app, None)
        await app.state.analytics_task
        assert app.state.analytics_task.exception() is None

    asyncio.run(scenario())
