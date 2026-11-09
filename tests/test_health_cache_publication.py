"""A completed refresh must not trigger another native probe or publish unlocked."""
import importlib
from types import SimpleNamespace
import threading

import pytest


@pytest.fixture
def main(monkeypatch, tmp_path):
    """Isolate the health cache and app data for each test."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    module = importlib.import_module("main")
    monkeypatch.setattr(module, "_health_label", ("cpu", "old-engine"))
    monkeypatch.setattr(module, "_health_device_at", 0.0)
    monkeypatch.setattr(module, "_health_device_thread", SimpleNamespace(is_alive=lambda: False))
    return module


def test_refresh_completed_before_lock_acquisition_never_starts_a_second_probe(main, monkeypatch):
    """Refresh completed before lock acquisition never starts a second probe."""
    main._health_device_at = main.time.monotonic() - main._HEALTH_DEVICE_TTL_S - 1

    class CompletionAtLock:
        def __enter__(self):
            """Simulate refresh completion before the scheduling lock is acquired."""
            main._health_label = ("vulkan", "new-engine")
            main._health_device_at = main.time.monotonic()
        def __exit__(self, *args):
            """Leave the simulated scheduling lock without suppressing errors."""
            return False

    monkeypatch.setattr(main, "_health_device_lock", CompletionAtLock())
    monkeypatch.setattr(main.threading, "Thread", lambda **kwargs: pytest.fail(
        "a completed refresh started a redundant native probe"))
    assert main._cached_health_label() == ("vulkan", "new-engine")


def test_native_probe_runs_outside_lock_and_publication_waits_for_it(main, monkeypatch):
    """Native probe runs outside lock and publication waits for it."""
    gate = threading.Lock()
    probed, publishing = threading.Event(), threading.Event()

    class PublicationLock:
        def __enter__(self):
            """Signal publication and wait for the test to release the cache lock."""
            publishing.set()
            gate.acquire()
        def __exit__(self, *args):
            """Release the cache lock after publication."""
            gate.release()

    def probe(engine):
        """Signal that the native probe ran before cache publication."""
        probed.set()
        return "vulkan"

    monkeypatch.setattr(main, "_health_device_lock", PublicationLock())
    monkeypatch.setattr(main, "_probe_health_engine", lambda: "new-engine")
    monkeypatch.setattr(main, "_probe_health_device", probe)
    worker = threading.Thread(target=main._resolve_health_device)
    gate.acquire()
    worker.start()
    try:
        assert probed.wait(5), "the native probe waited for the cache lock"
        assert publishing.wait(5), "the resolver published without the cache lock"
        assert main._health_label == ("cpu", "old-engine")
        assert main._health_device_at == 0.0
    finally:
        gate.release()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert main._health_label == ("vulkan", "new-engine")
    assert main._health_device_at > 0.0
