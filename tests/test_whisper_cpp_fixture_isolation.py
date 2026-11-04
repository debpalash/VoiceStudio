"""Keep native ASR test imports coherent after backend dependency reimports."""
import importlib
import subprocess
import sys

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]


@pytest.fixture(params=[None, "backend", "process"],
                ids=["cached-graph", "missing-backend", "missing-process"])
def dependency_cycle(request, monkeypatch):
    original = importlib.import_module("services.asr_backend")
    importlib.import_module("engines.whisper_cpp.backend")
    engines = importlib.import_module("engines")
    services = importlib.import_module("services")
    with monkeypatch.context() as cycle:
        cycle.delitem(sys.modules, "services.asr_backend")
        cycle.delattr(services, "asr_backend", raising=False)
        current = importlib.import_module("services.asr_backend")
        assert current.ASRTimeoutError is not original.ASRTimeoutError
        assert current.ASRTimeoutError.__qualname__ == original.ASRTimeoutError.__qualname__
        if request.param:
            package = importlib.import_module("engines.whisper_cpp")
            cycle.delitem(sys.modules, f"engines.whisper_cpp.{request.param}")
            cycle.delattr(package, request.param)
            cycle.delitem(sys.modules, "engines.whisper_cpp")
            cycle.delattr(engines, "whisper_cpp")
        before = {
            name: module for name, module in sys.modules.items()
            if name == "engines.whisper_cpp" or name.startswith("engines.whisper_cpp.")
        }
        missing = object()
        binding = getattr(engines, "whisper_cpp", missing)
        yield current
        # m must restore the callers' graph, including an absent package binding.
        after = {
            name: module for name, module in sys.modules.items()
            if name == "engines.whisper_cpp" or name.startswith("engines.whisper_cpp.")
        }
        assert after == before
        assert getattr(engines, "whisper_cpp", missing) is binding


@pytest.fixture
def cycled_m(request, dependency_cycle):
    return request.getfixturevalue("m")


def test_fixture_rebinds_entire_engine_graph(cycled_m):
    m = cycled_m
    package = importlib.import_module("engines.whisper_cpp")
    process = importlib.import_module("engines.whisper_cpp.process")
    bootstrap = importlib.import_module("engines.whisper_cpp.bootstrap")
    audio = importlib.import_module("engines.whisper_cpp.audio")
    assert process.ASRTimeoutError is m.ab.ASRTimeoutError
    assert issubclass(m.WhisperCppBackend, m.ab.ASRBackend)
    assert package.WhisperCppBackend is m.wc.WhisperCppBackend
    assert m.wc.run_transcription is process.run_transcription
    assert m.wc._ensure_wav_16k is audio._ensure_wav_16k
    assert m.wc.resolve_model is bootstrap.resolve_model


def test_timeout_is_caught_after_dependency_cycle(cycled_m, tmp_path, monkeypatch):
    process = importlib.import_module("engines.whisper_cpp.process")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("whisper-cli", 1)

    monkeypatch.setattr(process.subprocess, "run", timeout)
    with pytest.raises(cycled_m.ab.ASRTimeoutError):
        process.run_transcription(["whisper-cli"], tmp_path / "out.json", 1)
