"""Concurrent first use must share one inference worker and lifecycle owner."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import threading


def test_concurrent_first_use_creates_one_owned_proxy(monkeypatch):
    """Two first callers must share one worker after bounded device admission."""
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    proxies = importlib.import_module("core.windows_rocm.asr_proxy")
    backend = importlib.import_module("services.asr_backend")
    first_probe, second_probe = threading.Event(), threading.Event()
    release, second_started = threading.Event(), threading.Event()
    calls, created = [], []

    def probe():
        """Block the first probe and signal subsequent probes to expose creation races."""
        calls.append(True)
        if len(calls) == 1:
            first_probe.set()
            assert release.wait(10)
        else:
            second_probe.set()
        return False

    class Proxy:
        def __init__(self, source, device):
            created.append(self)
        def close(self, wait=False):
            return True

    class Owner:
        _model_name = "local-snapshot"

    owner = Owner()
    monkeypatch.setattr(backend, "_ctranslate2_cuda_ok", probe)
    monkeypatch.setattr(backend, "_local_model_source", lambda source: source)
    monkeypatch.setattr(proxies, "RocmASRProxy", Proxy)
    def second_call():
        second_started.set()
        return isolation._proxy(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(isolation._proxy, owner)
        try:
            assert first_probe.wait(10)
            second = pool.submit(second_call)
            assert second_started.wait(10)
            second_probe.wait(.5)
        finally:
            release.set()
        assert first.result(timeout=10) is second.result(timeout=10)
    assert len(created) == 1
    assert owner._rocm_asr_proxy is created[0]


def test_unload_cannot_observe_partial_proxy_publication(monkeypatch):
    """An unload during initialization must leave the eventual owner coherent."""
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    proxies = importlib.import_module("core.windows_rocm.asr_proxy")
    backend = importlib.import_module("services.asr_backend")
    entered, release = threading.Event(), threading.Event()
    class Owner:
        _model_name = "local-snapshot"
    class Proxy:
        def close(self, wait=False):
            return True
    owner = Owner()
    original = isolation.weakref.finalize
    def finalize(instance, *args):
        """Pause finalization for the selected owner before invoking the real finalizer."""
        if instance is owner:
            entered.set()
            assert release.wait(10)
        return original(instance, *args)
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    monkeypatch.setattr(backend, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(backend, "_local_model_source", lambda source: source)
    monkeypatch.setattr(proxies, "RocmASRProxy", lambda *args: Proxy())
    monkeypatch.setattr(isolation.weakref, "finalize", finalize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        creation = pool.submit(isolation._proxy, owner)
        try:
            assert entered.wait(10)
            assert isolation.unload_isolated(owner)
        finally:
            release.set()
        creation.result(timeout=10)
    assert isolation.unload_isolated(owner)
    assert owner._rocm_asr_proxy is None
