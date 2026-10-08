"""#2490 / #2491: the liveness probe must depend on the event loop alone.

The desktop shell polls ``/health`` every 2 s with a 1.5 s deadline for the life
of the app, and reports "Backend is running but temporarily not responding"
when three probes in a row miss. ``/health`` used to be a sync route that
imported torch and asked the CUDA driver for the device name on EVERY probe,
from the shared 40-thread worker pool. Under a generation (GIL + driver busy)
or with the pool full of blocked sync routes, a perfectly healthy backend missed
its probes and was announced as unresponsive.

Fail-before: ``health`` is a plain function and a blocked device probe blocks it.
Pass-after: it is a coroutine that answers immediately and never waits on torch.
"""

import asyncio
import importlib
import inspect
import os
import sys
import threading
import time

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)


@pytest.fixture()
def main(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    module = importlib.import_module("main")
    monkeypatch.setattr(module, "_health_device", None, raising=False)
    monkeypatch.setattr(module, "_health_device_thread", None, raising=False)
    return module


def test_health_runs_on_the_event_loop_not_the_worker_pool(main):
    assert inspect.iscoroutinefunction(main.health), (
        "a sync /health queues behind every blocked sync route in the 40-thread "
        "pool, so liveness would depend on unrelated handlers"
    )


def test_a_blocked_device_probe_never_delays_the_liveness_answer(main, monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def _blocked_probe() -> str:
        entered.set()
        release.wait(30)
        return "cuda (Test GPU)"

    monkeypatch.setattr(main, "_probe_health_device", _blocked_probe)
    try:
        started = time.monotonic()
        first = asyncio.run(main.health())
        assert time.monotonic() - started < 1.0, "the probe waited on the device lookup"
        assert first["status"] == "ok"
        assert first["device"] == "unknown"
        assert entered.wait(10), "the device label was never resolved in the background"

        # Repeated probes while the lookup is still wedged stay instant and do
        # not stack up more resolver threads.
        for _ in range(3):
            assert asyncio.run(main.health())["status"] == "ok"
        assert (
            sum(t.name == "health-device" and t.is_alive() for t in threading.enumerate()) == 1
        )
    finally:
        release.set()
        if main._health_device_thread is not None:
            main._health_device_thread.join(timeout=10)

    assert asyncio.run(main.health())["device"] == "cuda (Test GPU)"


def test_a_failing_device_lookup_does_not_fail_the_probe(main, monkeypatch):
    def _boom() -> str:
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(main, "_probe_health_device", _boom)
    asyncio.run(main.health())
    main._health_device_thread.join(timeout=10)
    body = asyncio.run(main.health())
    assert body["status"] == "ok"
    assert body["device"] == "unknown"
