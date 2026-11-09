"""English-only Whisper must accept English transcription without invented multilingual support."""
import importlib
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def english(monkeypatch, tmp_path):
    """Provide an English-only pipeline that rejects language and task options."""
    backend = importlib.import_module("services.asr_backend").PyTorchWhisperBackend
    path = tmp_path / "speech.wav"
    sf.write(path, np.zeros(1600, dtype=np.float32), 16000)
    calls = []
    class Pipeline:
        device = "cpu"
        model = SimpleNamespace(generation_config=SimpleNamespace(is_multilingual=False),
                                name_or_path="custom-checkpoint", dtype="torch.float32")
        def __call__(self, audio, **options):
            """Record generation options and reject multilingual options for the fake model."""
            calls.append(options)
            supplied = options.get("generate_kwargs", {})
            if any(key in supplied for key in ("language", "task")):
                raise ValueError("Cannot specify task or language for an English-only model")
            return {"text": "hello", "chunks": [{"text": "hello", "timestamp": (0., .1)}]}
    pipe = Pipeline()
    return backend(asr_pipe=pipe), path, calls


@pytest.mark.parametrize("language", [None, "en", "English", "<|en|>"])
def test_english_transcription_removes_only_redundant_language_options(english, language):
    backend, path, calls = english
    result = backend.transcribe(str(path), language=language, word_timestamps=False)
    assert result["text"] == "hello"
    assert not {"language", "task", "is_multilingual"} & calls[0].get("generate_kwargs", {}).keys()
    assert calls[0]["return_timestamps"] is True


@pytest.mark.parametrize("language,task", [("de", "transcribe"), ("en", "translate"), (None, "translate")])
def test_english_only_rejects_unsupported_requests_before_inference(english, language, task):
    backend, path, calls = english
    with pytest.raises(ValueError, match="English-only"):
        backend.transcribe(str(path), language=language, task=task)
    assert not calls


@pytest.mark.parametrize("config", [SimpleNamespace(is_multilingual=True), SimpleNamespace()])
def test_multilingual_and_unknown_configs_keep_request_options(english, config):
    backend, path, calls = english
    backend._pipe.model = SimpleNamespace(generation_config=config)
    def run(audio, rate, words, options):
        calls.append(options)
        return {"text": "hallo"}
    backend._run_with_oom_fallback = run
    assert backend.transcribe(str(path), language="de", task="translate")["text"] == "hallo"
    assert calls == [{"language": "de", "task": "translate"}]


def test_translation_capability_uses_loaded_english_only_config(english):
    backend, _path, _calls = english
    # The arbitrary local name must not override the actual generation config.
    assert backend.supports_translation() is False


def test_execution_evidence_tracks_actual_pipeline_and_model(english):
    backend, _path, _calls = english
    evidence = importlib.import_module("services.engine_evidence")
    backend._pipe.device = "cuda:0"
    backend._pipe.model.dtype = "torch.float16"
    snapshot = evidence.snapshot(engine_id="pytorch-whisper", engine_cls=type(backend),
        instance=backend, routing={"routing_status": "accelerated"},
        caps=SimpleNamespace(family="cpu", device_name=""))
    assert snapshot["actual_execution_device"] == "cuda:0"
    assert snapshot["precision_or_quantization"] == "torch.float16"
    backend._pipe.device = "cpu"
    backend._pipe.model.dtype = "torch.float32"
    snapshot = evidence.snapshot(engine_id="pytorch-whisper", engine_cls=type(backend),
        instance=backend, routing={"routing_status": "cpu_only"},
        caps=SimpleNamespace(family="cpu", device_name=""))
    assert snapshot["actual_execution_device"] == "cpu"
    assert snapshot["precision_or_quantization"] == "torch.float32"


def test_inventory_checks_metadata_without_executing_transformers(monkeypatch):
    """Check installation metadata without executing the Transformers runtime."""
    import builtins
    from importlib import util
    backend = importlib.import_module("services.asr_backend").PyTorchWhisperBackend
    original = builtins.__import__
    def no_transformers(name, *args, **kwargs):
        """Reject Transformers runtime imports while allowing unrelated imports."""
        if name == "transformers" or name.startswith("transformers."):
            pytest.fail("Background availability executed the heavy Transformers import")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(util, "find_spec", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(builtins, "__import__", no_transformers)
    from services.performance_readiness import inventory_only
    assert inventory_only(backend.is_available)() == (True, "ready")


def test_availability_reports_missing_transformers(monkeypatch):
    """Report a missing Transformers installation as unavailable."""
    from importlib import util
    backend = importlib.import_module("services.asr_backend").PyTorchWhisperBackend
    monkeypatch.setattr(util, "find_spec", lambda name: None)
    available, detail = backend.is_available()
    assert not available and "transformers" in detail


def test_deep_pipeline_import_failure_keeps_context_and_import_error_category(monkeypatch):
    """Preserve transitive import errors without misdiagnosing an installed package."""
    import builtins
    backend = importlib.import_module("services.asr_backend").PyTorchWhisperBackend()
    original = builtins.__import__
    def broken(name, *args, **kwargs):
        """Simulate a transitive Transformers import failure and delegate other imports."""
        if name == "transformers":
            raise ImportError("missing transitive audio helper", name="transformers")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ImportError, match="PyTorch Whisper.*Transformers pipeline") as error:
        backend.ensure_loaded()
    assert "missing transitive audio helper" in str(error.value)
    assert isinstance(error.value.__cause__, ImportError)
    assert error.value.name is None  # do not falsely diagnose the installed package as absent
