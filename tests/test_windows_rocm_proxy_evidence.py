"""A completed old request must not overwrite a replacement worker's evidence."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import threading


def test_stale_completion_keeps_current_worker_execution_evidence(monkeypatch):
    """Return both completed jobs while publishing only the current worker."""
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    proxies = importlib.import_module("core.windows_rocm.asr_proxy")
    backend = importlib.import_module("services.asr_backend")
    entered, release = threading.Event(), threading.Event()
    created = []
    class Proxy:
        def __init__(self, source, device):
            """Assign distinct CPU and GPU evidence to successive fake workers."""
            self.execution_device = "cpu" if not created else "cuda"
            self.execution_precision = "int8" if not created else "float16"
            self.fallback_reason = self.fallback_stage = None
            created.append(self)
        def transcribe(self, audio_path, **options):
            """Pause the original worker to expose evidence races with its replacement."""
            if self is created[0]:
                entered.set()
                assert release.wait(10)
            return {"text": audio_path}
        def close(self, wait=False):
            return True
    class Owner:
        _model_name = "local-snapshot"
    owner = Owner()
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    monkeypatch.setattr(backend, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(backend, "_local_model_source", lambda source: source)
    monkeypatch.setattr(proxies, "RocmASRProxy", Proxy)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(isolation.transcribe_isolated, owner, "old.wav")
        try:
            assert entered.wait(10)
            assert isolation.unload_isolated(owner)
            assert isolation.transcribe_isolated(owner, "new.wav") == {"text": "new.wav"}
            assert owner._device == "cuda"
        finally:
            release.set()
        assert old.result(timeout=10) == {"text": "old.wav"}
    try:
        assert owner._device == owner._rocm_asr_proxy.execution_device == "cuda"
        assert owner._compute_type == "float16"
    finally:
        isolation.unload_isolated(owner)
