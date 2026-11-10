"""A request retaining an unloaded proxy must not respawn an unowned child."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import threading

import pytest


def test_unload_between_proxy_lookup_and_worker_start_prevents_orphan(monkeypatch):
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    proxies = importlib.import_module("core.windows_rocm.asr_proxy")
    transport = importlib.import_module("services.subprocess_backend")
    backend = importlib.import_module("services.asr_backend")
    entered, release = threading.Event(), threading.Event()
    spawned = []
    original = proxies.RocmASRProxy.ensure_loaded
    def paused(proxy):
        entered.set()
        assert release.wait(10)
        original(proxy)
    class Owner:
        _model_name = "installed-snapshot"
    owner = Owner()
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    monkeypatch.setattr(backend, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(backend, "_local_model_source", lambda source: source)
    monkeypatch.setattr(transport.SubprocessBackend, "_spawn", lambda proxy: spawned.append(proxy))
    monkeypatch.setattr(proxies.RocmASRProxy, "ensure_loaded", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(isolation.ensure_isolated, owner)
        try:
            assert entered.wait(10)
            old_proxy = owner._rocm_asr_proxy
            assert isolation.unload_isolated(owner)
            assert owner._rocm_asr_proxy is None
            release.set()
            with pytest.raises(RuntimeError, match="unloaded|retired"):
                request.result(timeout=10)
            assert not spawned
            # The next request gets a fresh, usable owner and cleanup callback.
            replacement = isolation._proxy(owner)
            assert replacement is not old_proxy
            replacement.ensure_loaded()
            assert spawned == [replacement]
        finally:
            release.set()
            isolation.unload_isolated(owner)
