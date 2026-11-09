"""Exercise actual ASR loaders with a verified fake Windows HIP CT2 runtime."""
import builtins
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch  # noqa: F401 - load before the fixture replaces package discovery.
from services import asr_backend as ab


@pytest.fixture
def hip(monkeypatch, tmp_path):
    """A Windows HIP CTranslate2 install whose pins, binary and devices check out."""
    import types

    from core import device_caps
    from core.windows_rocm import ct2
    from core.windows_rocm.recipe import expected_versions, load_recipe

    recipe = load_recipe()
    pins = expected_versions(recipe)
    real_version = ct2.metadata.version
    real_find_spec = ct2.util.find_spec
    initialized = []
    loads = []

    monkeypatch.setattr(ct2.sys, "platform", "win32")
    monkeypatch.setattr(ct2, "validate_target", lambda *a, **k: None)
    monkeypatch.setattr(
        ct2.metadata, "version", lambda name: pins[name] if name in pins else real_version(name)
    )
    monkeypatch.setattr(ct2, "package_root", lambda *a, **k: tmp_path)

    faked_modules = {"faster_whisper", "ctranslate2"}

    def _find_spec(name, *args, **kwargs):
        if name in faked_modules:
            return SimpleNamespace(name=name, submodule_search_locations=[str(tmp_path)])
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(ct2.util, "find_spec", _find_spec)
    monkeypatch.setattr(ct2, "sha256", lambda path: recipe["ctranslate2"]["binary_sha256"])
    monkeypatch.setattr(ct2, "initialize_dll_search", lambda *a, **k: initialized.append(True))

    native = types.ModuleType("ctranslate2")
    native.get_cuda_device_count = lambda: 1
    native.get_supported_compute_types = lambda device: {"float16"}
    monkeypatch.setitem(sys.modules, "ctranslate2", native)

    monkeypatch.setattr(ct2, "_VERIFIED", set())
    monkeypatch.setattr(ct2, "_FAILED", {})

    def _verify():
        if native.get_cuda_device_count() < 1:
            raise RuntimeError("no HIP device visible to CTranslate2")
        if "float16" not in native.get_supported_compute_types("cuda"):
            raise RuntimeError("float16 is not supported on the HIP device")

    monkeypatch.setattr(ct2, "verify_environment", _verify)

    caps = SimpleNamespace(
        family="rocm",
        requested_family=None,
        available_families=("rocm", "cpu"),
        device_name="AMD Radeon RX 7800 XT",
        vram_gb=16.0,
        notes=(),
        probe_ok=True,
    )
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda *a, **k: caps)

    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ready"))
    monkeypatch.setattr(ab, "_ctranslate2_cudnn_ok", lambda: (True, "ready"))

    whisper = types.ModuleType("faster_whisper")

    def _whisper_model(source, **kwargs):
        assert initialized, "the HIP DLL search path must be set up before loading"
        loads.append(kwargs)
        return SimpleNamespace(source=source)

    whisper.WhisperModel = _whisper_model
    monkeypatch.setitem(sys.modules, "faster_whisper", whisper)
    monkeypatch.setattr(ab, "_local_model_source", lambda *a, **k: "local-whisper-model")

    return SimpleNamespace(ct2=ct2, native=native, caps=caps, loads=loads, initialized=initialized)


def test_faster_whisper_actually_loads_verified_hip(hip):
    assert ab.FasterWhisperBackend.is_available()[0]
    engine = ab.FasterWhisperBackend()
    engine._ensure_model()
    assert hip.loads == [{"device": "cuda", "compute_type": "float16"}]
    assert engine._device == "cuda"


@pytest.mark.parametrize("failure", ["binary", "count", "precision", "version"])
def test_stock_or_unusable_ct2_never_gets_hip_gpu(hip, monkeypatch, failure):
    if failure == "binary":
        monkeypatch.setattr(hip.ct2, "sha256", lambda path: "stock-cuda")
    elif failure == "count":
        hip.native.get_cuda_device_count = lambda: 0
    elif failure == "precision":
        hip.native.get_supported_compute_types = lambda device: {"int8"}
    else:
        monkeypatch.setattr(hip.ct2.metadata, "version", lambda name: "4.4.0")
    ab.FasterWhisperBackend()._ensure_model()
    assert hip.loads == [{"device": "cpu", "compute_type": "int8"}]


def test_explicit_cpu_override_stays_cpu(hip):
    hip.caps.family = "cpu"
    ab.FasterWhisperBackend()._ensure_model()
    assert hip.loads == [{"device": "cpu", "compute_type": "int8"}]


def test_cpu_auto_and_profile_never_probe_gpu(hip, monkeypatch):
    hip.caps.family = "cpu"
    monkeypatch.setattr(hip.ct2, "verify_environment", lambda: pytest.fail("CPU selection probed GPU"))
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    monkeypatch.setattr(ab, "_probe_available", lambda cls: cls.id == "faster-whisper")
    assert ab._auto_detect() == "faster-whisper"
    assert "rocm" not in ab.FasterWhisperBackend.runtime_gpu_compat()


def test_auto_prefers_verified_windows_ct2_over_heavy_pytorch(hip, monkeypatch):
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", lambda cls: True)
    assert ab._auto_detect() == "faster-whisper"


def test_isolated_worker_uses_same_verified_hip_gate(hip, monkeypatch):
    from core import execstack

    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (True, "ready"))
    path = Path(__file__).parents[1] / "backend/engines/_asr_sidecar/main.py"
    spec = importlib.util.spec_from_file_location("test_hip_asr_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "_local_model_source", lambda name: "local", raising=False)
    worker._get_model()
    assert hip.loads[0]["device"] == "cuda"


def test_isolated_availability_initializes_dll_search(hip):
    from services.subprocess_asr import IsolatedFasterWhisperBackend

    assert IsolatedFasterWhisperBackend.is_available()[0]
    assert hip.ct2._VERIFIED  # Native discovery succeeded in the bounded child.


def test_runtime_gpu_metadata_admits_only_verified_hip(hip, monkeypatch):
    assert "rocm" in ab.FasterWhisperBackend.runtime_gpu_compat()
    monkeypatch.setattr(hip.ct2, "sha256", lambda path: "stock")
    assert "rocm" not in ab.FasterWhisperBackend.runtime_gpu_compat()


def test_actual_catalogue_and_execution_metadata_agree_on_hip(hip, monkeypatch):
    monkeypatch.setattr(ab, "_REGISTRY", {"faster-whisper": ab.FasterWhisperBackend})
    monkeypatch.setattr(ab, "_DEEP_IMPORT_BROKEN", {})
    row, = ab.list_backends()
    assert row["available"] and row["effective_device"] == "rocm"
    assert "rocm" in row["gpu_compat"]
    assert "rocm" in row["execution_evidence"]["declared_device_families"]


def test_linux_hip_and_cuda_existing_choices_preserved(hip, monkeypatch):
    monkeypatch.setattr(hip.ct2.sys, "platform", "linux")
    assert not ab._ctranslate2_cuda_ok()
    hip.caps.family = "cuda"
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    assert ab._ctranslate2_cuda_ok()


@pytest.mark.parametrize("kind", ["timeout", "crash"])
def test_failed_native_child_never_imports_ct2_in_availability(hip, monkeypatch, kind):
    from services.subprocess_asr import IsolatedFasterWhisperBackend

    calls = []
    def fail():
        calls.append(True)
        if kind == "timeout":
            raise subprocess.TimeoutExpired("probe", 90)
        raise RuntimeError("native child exited -1073740791")

    monkeypatch.setattr(hip.ct2, "verify_environment", fail)
    imported = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {"faster_whisper", "ctranslate2"}:
            pytest.fail("availability imported a native runtime after failed child")
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for cls in (ab.FasterWhisperBackend, IsolatedFasterWhisperBackend):
        ok, reason = cls.is_available()
        assert not ok and "Windows" in reason
        assert "rocm" not in cls.runtime_gpu_compat()
    assert len(calls) == 1, "one failed child must not repeat for every catalogue row/profile"
    monkeypatch.setattr(hip.ct2, "verify_environment", lambda: None)
    assert not hip.ct2.hip_ct2_available(), "a failed probe is not re-run on every refresh"
    hip.ct2._FAILED.clear()  # what a backend restart after rerunning setup does
    assert hip.ct2.hip_ct2_available(), "a fresh process verifies again"
