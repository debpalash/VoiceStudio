"""ROCm 10 ASR must execute outside Torch's process without losing its contract."""
import importlib
import json
import sys
from types import SimpleNamespace

import pytest


_CHILD = '''
import builtins, json, os, sys
sys.path.insert(0, {backend!r})
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "torch" or name.startswith("torch."):
        raise AssertionError("Torch imported in CT2 inference process")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from types import SimpleNamespace
from core.windows_rocm import ct2
def hip_ready():
    if os.environ.get("ASR_TEST_FORBID_GPU_PROBE") == "1":
        raise AssertionError("CPU selection probed the GPU")
    return os.environ.get("ASR_TEST_HIP_READY", "1") == "1"
ct2.hip_ct2_available = hip_ready
from core.windows_rocm import asr_isolation
# Parent platform/metadata monkeypatches do not cross this process boundary.
asr_isolation.isolation_required = lambda: True
class Model:
    def __init__(self, source, device, compute_type):
        if "oom" in source and device == "cuda":
            raise RuntimeError("HIP out of memory")
        self.source = source
        self.model = SimpleNamespace(device=device, compute_type=compute_type)
    def transcribe(self, audio, **options):
        if audio == "crash.wav": os._exit(3)
        text = json.dumps(dict(source=self.source, options=options))
        word = SimpleNamespace(word="hello", start=0., end=1., probability=.9)
        segment = SimpleNamespace(text=text, start=0., end=1., words=[word])
        return iter([segment]), SimpleNamespace(language="en", language_probability=.98, duration=2.)
sys.modules["faster_whisper"] = SimpleNamespace(WhisperModel=Model)
from engines._asr_sidecar import main
raise SystemExit(main.main())
'''


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Provide an isolated ASR backend backed by a deterministic local fake child."""
    from pathlib import Path
    from importlib import metadata
    ab = importlib.import_module("services.asr_backend")
    transport = importlib.import_module("services.subprocess_asr")
    base = importlib.import_module("services.subprocess_backend")
    manager = importlib.import_module("services.model_manager")
    endpoint = importlib.import_module("services.endpoint_race")
    real_version = metadata.version
    monkeypatch.setattr(metadata, "version", lambda name: (
        "2.13.0+rocm10.0.0" if name == "torch" else real_version(name)))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    monkeypatch.setenv("OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S", "0")
    monkeypatch.setattr(manager, "running_on_gpu_pool", lambda: True)
    monkeypatch.setattr(endpoint, "download_endpoint", lambda **kw: None)
    monkeypatch.setattr(base, "_ensure_reaper_running", lambda: None)
    monkeypatch.setattr(ab, "_local_model_source", lambda name: "local-" + name)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: (
        __import__("os").environ.get("OMNIVOICE_DEVICE") != "cpu"))
    profile = importlib.import_module("services.performance_profiles")
    monkeypatch.setattr(profile, "asr_decode_defaults", lambda: {"beam_size": 2, "best_of": 3})
    script = tmp_path / "fake_ct2_child.py"
    script.write_text(_CHILD.format(backend=str(Path(__file__).parents[1] / "backend")))
    monkeypatch.setattr(transport.IsolatedFasterWhisperBackend, "sidecar_script", classmethod(lambda cls: script))
    def forbidden(*args, **kwargs):
        pytest.fail("CT2 inference loaded in the Torch parent process")
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(
        WhisperModel=forbidden, __spec__=SimpleNamespace()))
    engines = []
    def create(name="snapshot"):
        engine = ab.FasterWhisperBackend(model_name=name)
        engines.append(engine)
        return engine
    create.engines = engines
    yield create
    for engine in engines:
        engine.unload()


def test_normal_backend_preserves_exact_model_options_and_result_in_child(isolated):
    """Preserve decoding options, normalized results, and execution evidence across IPC."""
    engine = isolated()
    result = engine.transcribe("clip.wav", language="de", initial_prompt="names",
                               temperature=.2, task="translate")
    heard = json.loads(result["segments"][0]["text"])
    assert heard == {"source": "local-snapshot", "options": {
        "word_timestamps": True, "condition_on_previous_text": False, "vad_filter": True,
        "beam_size": 2, "best_of": 3, "language": "de", "initial_prompt": "names",
        "temperature": .2, "task": "translate"}}
    assert result["duration"] == 2. and result["language_probability"] == .98
    assert result["segments"][0]["words"] == [
        {"word": "hello", "start": 0., "end": 1., "probability": .9}]
    assert result["chunks"][0]["timestamp"] == (0., 1.)
    assert engine._device == "cpu" and engine._compute_type == "int8"
    assert engine.execution_evidence_loaded()


def test_cpu_override_and_disabled_timestamps_survive_isolation(isolated, monkeypatch):
    monkeypatch.setenv("ASR_TEST_FORBID_GPU_PROBE", "1")
    engine = isolated()
    result = engine.transcribe("clip.wav", word_timestamps=False)
    assert result["segments"][0]["words"] == []
    assert engine._device == "cpu"
    engine.unload()
    assert not engine.execution_evidence_loaded()
    assert engine.transcribe("again.wav")["language"] == "en"


def test_native_crash_fails_one_job_and_next_request_respawns(isolated):
    engine = isolated()
    with pytest.raises(RuntimeError, match="crashed"):
        engine.transcribe("crash.wav")
    assert engine.transcribe("again.wav")["language"] == "en"


def test_gpu_memory_failure_keeps_cpu_fallback_and_reports_actual_device(isolated, monkeypatch):
    """Report CPU execution and memory fallback evidence after a child GPU load failure."""
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    engine = isolated("oom-snapshot")
    assert engine.transcribe("clip.wav")["language"] == "en"
    assert engine._device == "cpu" and engine._compute_type == "int8"
    assert engine._fallback_stage == "model_load"
    assert "memory" in engine._fallback_reason.lower()


def test_effective_parent_cpu_choice_skips_gpu_in_child(isolated, monkeypatch):
    ab = importlib.import_module("services.asr_backend")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "xpu")
    monkeypatch.setenv("ASR_TEST_FORBID_GPU_PROBE", "1")
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: False)
    engine = isolated()
    assert engine.transcribe("clip.wav")["language"] == "en"
    assert engine._device == "cpu"


def test_catalogue_reports_actual_isolated_cpu_execution(isolated, monkeypatch):
    ab = importlib.import_module("services.asr_backend")
    caps = importlib.import_module("core.device_caps")
    routing = importlib.import_module("services.engine_routing")
    monkeypatch.setattr(caps, "detect_host_caps", lambda: SimpleNamespace(family="cpu", device_name=""))
    monkeypatch.setattr(routing, "runtime_compute_profile", lambda *a: {
        "gpu_compat": ["cpu"], "routing_status": "cpu_only", "routing_reason": None})
    engine = isolated()
    engine.transcribe("clip.wav")
    monkeypatch.setattr(ab, "_REGISTRY", {"faster-whisper": ab.FasterWhisperBackend})
    monkeypatch.setattr(ab, "_RUNTIME_INSTANCES", {"faster-whisper": engine})
    row, = ab.list_backends()
    assert row["isolation_mode"] == "subprocess"
    assert row["execution_evidence"]["actual_execution_device"] == "cpu"
    assert row["execution_evidence"]["parent_memory_observable"] is False


def test_dropping_a_direct_backend_releases_its_owned_child(isolated):
    import gc
    import weakref

    engine = isolated()
    engine.transcribe("clip.wav")
    proxy = engine._rocm_asr_proxy
    process = proxy._proc
    isolated.engines.remove(engine)
    reference = weakref.ref(engine)
    del engine
    gc.collect()
    try:
        assert reference() is None
        assert process.poll() is not None
    finally:
        proxy.close()


@pytest.mark.parametrize("fallback", [False, True])
def test_error_reports_actual_cpu_device_without_torch_discovery(isolated, monkeypatch, fallback):
    """Decorate child failures with the actual device without native Torch discovery."""
    transport = importlib.import_module("services.subprocess_asr")
    if fallback:
        monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    engine = isolated("oom-snapshot" if fallback else "snapshot")
    engine.transcribe("clip.wav")
    def forbidden():
        pytest.fail("Error decoration attempted native Torch device discovery")
    monkeypatch.setattr(transport.SubprocessASRBackend, "_device", staticmethod(forbidden))
    with pytest.raises(RuntimeError, match=r"crashed.*device=cpu"):
        engine.transcribe("crash.wav")
