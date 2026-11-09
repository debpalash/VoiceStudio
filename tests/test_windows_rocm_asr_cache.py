"""The worker cache identity persists across requests and invalidates on selection changes."""
import importlib
import sys
from types import SimpleNamespace


def test_worker_reuses_same_model_and_reloads_changed_model_or_device(monkeypatch):
    runtime = importlib.import_module("engines._asr_sidecar.runtime")
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    ct2 = importlib.import_module("core.windows_rocm.ct2")
    execstack = importlib.import_module("core.execstack")
    monkeypatch.setattr(runtime, "_model", None)
    monkeypatch.setattr(runtime, "_identity", None)
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    monkeypatch.setattr(isolation, "worker_device", lambda device: device)
    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (True, "ready"))
    monkeypatch.setattr(ct2, "prepare_import", lambda: None)
    loads = []
    def model(source, **options):
        loads.append((source, options["device"]))
        return SimpleNamespace(source=source, device=options["device"])
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=model))
    first = {"model": "local-a", "device": "cpu", "vad_filter": True}
    instance = runtime.get_model(first)
    assert runtime.get_model(first) is instance
    second = runtime.get_model({**first, "model": "local-b"})
    assert second is not instance
    assert runtime.get_model({**first, "model": "local-b"}) is second
    gpu = runtime.get_model({**first, "model": "local-b", "device": "cuda"})
    assert gpu is not second
    assert loads == [("local-a", "cpu"), ("local-b", "cpu"), ("local-b", "cuda")]
