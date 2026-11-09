"""Native preflight runs in the worker without loading model weights."""
import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def no_native_startup(monkeypatch):
    runtime = importlib.import_module("core.windows_rocm.runtime")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(runtime, "initialize_dll_search", lambda: None)
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})


def test_worker_ct2_import_failure_disqualifies_optimistic_availability(monkeypatch):
    """Reject optimistic ASR readiness when the actual CT2 import fails."""
    worker = importlib.import_module("services._performance_probe")
    asr = importlib.import_module("services.asr_backend")
    ct2 = importlib.import_module("core.windows_rocm.ct2")
    monkeypatch.setattr(asr, "_probe_available", lambda cls: True)
    monkeypatch.setattr(ct2, "prepare_import", lambda: None)
    policy = importlib.import_module("core.windows_rocm.isolation_policy")
    monkeypatch.setattr(policy, "isolation_required", lambda: False)
    original = importlib.import_module

    def unavailable(name, *args):
        """Simulate a native faster-whisper import failure without blocking other modules."""
        if name == "faster_whisper":
            raise ImportError("native CT2 missing")
        return original(name, *args)

    monkeypatch.setattr(importlib, "import_module", unavailable)
    with pytest.raises(ImportError, match="native CT2 missing"):
        worker.check("asr", "faster-whisper")


def test_auto_worker_retains_hardware_order_but_skips_unloadable_ct2(monkeypatch):
    """Retain auto backend priority while skipping candidates that fail deep readiness."""
    worker = importlib.import_module("services._performance_probe")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "_probe_available", lambda cls: True)
    monkeypatch.setattr(worker, "_asr_ready", lambda asr, cls, probe=None: cls.id == "pytorch-whisper")

    def detect():
        """Return the first available backend in the simulated hardware priority order."""
        for cls in (asr.WhisperXBackend, asr.FasterWhisperBackend, asr.PyTorchWhisperBackend):
            if asr._probe_available(cls):
                return cls.id

    monkeypatch.setattr(asr, "_auto_detect", detect)
    assert worker.check("asr", "auto") == ("pytorch-whisper", True)


def test_other_managed_families_use_deep_preflight_only(monkeypatch):
    """Use family-specific deep checks for TTS, translation, and dictation readiness."""
    worker = importlib.import_module("services._performance_probe")
    calls = []
    monkeypatch.setitem(sys.modules, "services.tts_backend", SimpleNamespace(
        get_backend_class=lambda engine: SimpleNamespace(is_available=lambda: (True, "ready"))))
    monkeypatch.setitem(sys.modules, "services.translation_engines", SimpleNamespace(
        is_ready=lambda engine, **kwargs: calls.append(kwargs) or False))
    monkeypatch.setitem(sys.modules, "services.sherpa_dictation", SimpleNamespace(
        sherpa_available=lambda: (False, "bad native library")))
    assert worker.check("tts", "omnivoice") == ("omnivoice", True)
    assert worker.check("translation", "nllb") == ("nllb", False)
    assert worker.check("dictation", "offline-transducer") == ("offline-transducer", False)
    assert calls == [{"import_check": True}]


def test_auto_fallback_is_unresolved_when_its_package_is_not_ready(monkeypatch):
    worker = importlib.import_module("services._performance_probe")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "_auto_detect", lambda: "pytorch-whisper")
    monkeypatch.setattr(asr, "_probe_available", lambda cls: False)
    assert worker.check("asr", "auto") == ("pytorch-whisper", False)


def test_worker_retains_parent_deep_failure_exclusions(monkeypatch):
    worker = importlib.import_module("services._performance_probe")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr.FasterWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready")))
    assert worker.check("asr", "faster-whisper", ("faster-whisper",))[1] is False


def test_dll_search_precedes_tts_runtime_import(monkeypatch):
    """Initialize DLL search paths before importing the TTS runtime."""
    import builtins

    worker = importlib.import_module("services._performance_probe")
    runtime = importlib.import_module("core.windows_rocm.runtime")
    initialized = []
    monkeypatch.setattr(runtime, "initialize_dll_search", lambda: initialized.append(True))
    original = builtins.__import__

    def ordered(name, *args, **kwargs):
        """Assert DLL search initialization precedes TTS import and delegate the import."""
        if name == "services.tts_backend":
            assert initialized
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", ordered)
    monkeypatch.setitem(sys.modules, "services.tts_backend", SimpleNamespace(
        get_backend_class=lambda engine: SimpleNamespace(is_available=lambda: (True, "ready"))))
    assert worker.check("tts", "omnivoice") == ("omnivoice", True)


@pytest.mark.parametrize("requested", ["faster-whisper-isolated", "auto"])
def test_fresh_lazy_registry_resolves_isolated_readiness(monkeypatch, requested):
    """Resolve an isolated backend from an initially unloaded lazy registry."""
    worker = importlib.import_module("services._performance_probe")
    asr = importlib.import_module("services.asr_backend")
    isolated = SimpleNamespace(id="faster-whisper-isolated")
    registry = asr._LazyASRRegistry({})
    registry._LAZY = {isolated.id: lambda: isolated}
    assert isolated.id in registry and not dict.__contains__(registry, isolated.id)
    monkeypatch.setattr(asr, "_REGISTRY", registry)
    monkeypatch.setattr(asr, "_auto_detect", lambda: isolated.id)
    monkeypatch.setattr(worker, "_asr_ready", lambda _asr, cls, probe=None: cls is isolated)
    assert worker.check("asr", requested) == (isolated.id, True)
    assert dict.__contains__(registry, isolated.id)
