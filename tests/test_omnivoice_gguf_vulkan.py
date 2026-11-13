"""Regression coverage for the pinned GGUF runtime's compute contract (#3042)."""
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def gguf():
    """Resolve the current backend after any earlier sys.modules replacement."""
    import importlib

    return importlib.import_module("engines.omnivoice_gguf.backend")


def test_vulkan_loader_without_a_gpu_keeps_cpu_routing():
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps(
        family="cpu",
        available_families=("cpu",),
        notes=("Vulkan runtime loader detected; no compatible GPU family found",),
    )
    route = resolve_routing(("cuda", "vulkan", "mps", "cpu"), caps)
    assert route["effective_device"] == "cpu"


def test_pinned_cli_does_not_receive_unsupported_compute_flags(monkeypatch, tmp_path, gguf):
    """Only pass flags supported by the pinned native CLI."""
    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    monkeypatch.setenv(gguf.DEVICE_ENV, "1")
    with patch.object(gguf, "_binary_path", return_value=tmp_path / "tts"):
        argv = gguf.OmniVoiceGGUFBackend()._build_argv(
            base=Path("model.gguf"), tokenizer=Path("codec.gguf"),
            out_path=Path("out.wav"), ref_audio=None, ref_text=None, language="en")
    assert "--backend" not in argv
    assert "--device" not in argv


@pytest.mark.parametrize("backend,index,expected", [
    ("vulkan", "1", "Vulkan1"), ("cuda", "0", "CUDA0"), ("cpu", "", "CPU"),
])
def test_overrides_use_pinned_runtime_environment(monkeypatch, backend, index, expected, gguf):
    """Native compute choices use backend-local GGML device names."""
    monkeypatch.setenv(gguf.BACKEND_ENV, backend)
    monkeypatch.setenv(gguf.DEVICE_ENV, index)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    assert gguf._spawn_env()["GGML_BACKEND"] == expected


def test_global_cpu_pin_reaches_native_subprocess(monkeypatch, gguf):
    """The global CPU choice takes priority over native GPU overrides."""
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    monkeypatch.setenv("GGML_BACKEND", "Vulkan0")
    assert gguf._spawn_env()["GGML_BACKEND"] == "CPU"


def test_unverified_native_device_keeps_cpu_timeout_budget(monkeypatch, gguf):
    """Unverified native acceleration must retain the conservative timeout."""
    from core.device_caps import HostCaps
    from services.engine_routing import runtime_compute_profile

    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    caps = HostCaps(family="cuda", available_families=("cuda", "cpu"))
    profile = runtime_compute_profile(gguf.OmniVoiceGGUFBackend, caps)
    assert profile["effective_device"] == "cpu"
    assert profile["runtime_device_verified"] is False


@pytest.mark.parametrize("backend,index", [
    ("vulkan", "-1"), ("vulkan", "1.5"), ("vulkan", "١"),
    ("cpu", "1"), ("unknown", "0"), ("", "1"),
])
def test_invalid_device_overrides_fail_clearly(monkeypatch, backend, index, gguf):
    """Reject invalid backend and device combinations before spawning."""
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setenv(gguf.BACKEND_ENV, backend)
    monkeypatch.setenv(gguf.DEVICE_ENV, index)
    with pytest.raises(ValueError, match="OMNIVOICE_GGUF_"):
        gguf._spawn_env()


def test_settings_cpu_pin_reaches_real_spawn_boundary(monkeypatch, gguf):
    """A CPU choice from Settings reaches the subprocess environment."""
    from types import SimpleNamespace

    from core import prefs
    from services import binary_preflight

    monkeypatch.delenv("OMNIVOICE_DEVICE", raising=False)
    monkeypatch.setattr(prefs, "resolve", lambda *args, **kwargs: "cpu")
    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    monkeypatch.setattr(binary_preflight, "validate_executable", lambda *args, **kwargs: None)
    captured = {}

    def run(argv, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(gguf.subprocess, "run", run)
    gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert captured["env"]["GGML_BACKEND"] == "CPU"


def test_auto_preserves_native_selection(monkeypatch, gguf):
    """Automatic selection leaves GGML free to choose its backend."""
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.delenv(gguf.BACKEND_ENV, raising=False)
    monkeypatch.delenv(gguf.DEVICE_ENV, raising=False)
    monkeypatch.delenv("GGML_BACKEND", raising=False)
    assert "GGML_BACKEND" not in gguf._spawn_env()


def test_native_compatibility_retains_gpu_capability(gguf):
    """Conservative metadata does not remove native GPU support."""
    assert gguf.OmniVoiceGGUFBackend.gpu_compat == ("cuda", "vulkan", "mps", "cpu")


@pytest.mark.parametrize("label,expected", [
    ("Arabic", "arb"), ("ar", "arb"), ("Kurdish", "kmr"), ("ku", "kmr"),
    ("Chinese (Simplified)", "zh"), ("Chinese (Traditional)", "zh"),
    ("Uyghur", "ug"), ("Haitian Creole", "ht"),
])
def test_gguf_lang_arg_resolves_app_picker_aliases(monkeypatch, tmp_path, gguf, label, expected):
    """Picker labels the binary cannot resolve must reach ``--lang`` as IDs (#3692)."""
    with patch.object(gguf, "_binary_path", return_value=tmp_path / "tts"):
        argv = gguf.OmniVoiceGGUFBackend()._build_argv(
            base=Path("model.gguf"), tokenizer=Path("codec.gguf"),
            out_path=Path("out.wav"), ref_audio=None, ref_text=None, language=label)
    assert argv[argv.index("--lang") + 1] == expected


def test_gguf_lang_arg_keeps_plain_iso_mapping(gguf):
    assert gguf._iso_to_omnivoice_lang("fr") == "French"
    assert gguf._iso_to_omnivoice_lang("auto") is None


def test_gguf_lang_arg_keeps_ids_already_valid(gguf):
    assert gguf._iso_to_omnivoice_lang("ht") == "ht"
    assert gguf._iso_to_omnivoice_lang("ug") == "ug"


def _crash_harness(monkeypatch, gguf, *, first, second=0):
    """Run _run_subprocess with scripted exit codes; return (error, envs)."""
    from types import SimpleNamespace

    from services import binary_preflight

    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setattr(gguf, "_GPU_CRASHED", False)
    monkeypatch.setattr(binary_preflight, "validate_executable", lambda *a, **k: None)
    monkeypatch.setattr(gguf, "_binary_path", lambda slug=None: Path("tts"))
    codes = [first, second]
    envs = []

    def run(argv, **kwargs):
        envs.append(kwargs["env"].get("GGML_BACKEND"))
        return SimpleNamespace(returncode=codes.pop(0), stderr="ggml_vulkan: found", stdout="")

    monkeypatch.setattr(gguf.subprocess, "run", run)
    return envs


def test_gpu_access_violation_retries_on_cpu_and_sticks(monkeypatch, gguf):
    """A native GPU crash (#4050, GTX 1080 on Vulkan) falls back to the CPU."""
    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    envs = _crash_harness(monkeypatch, gguf, first=3221225477)
    gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert envs == ["Vulkan0", "CPU"]
    assert gguf._GPU_CRASHED is True
    # Later requests start on the CPU directly.
    envs.clear()
    monkeypatch.setattr(gguf.subprocess, "run", lambda argv, **kw: (
        envs.append(kw["env"].get("GGML_BACKEND"))
        or __import__("types").SimpleNamespace(returncode=0, stderr="", stdout="")))
    gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert envs == ["CPU"]
    monkeypatch.setattr(gguf, "_GPU_CRASHED", False)


def test_cpu_crash_and_ordinary_errors_are_not_masked(monkeypatch, gguf):
    monkeypatch.setenv(gguf.BACKEND_ENV, "cpu")
    envs = _crash_harness(monkeypatch, gguf, first=3221225477)
    with pytest.raises(RuntimeError, match="exited 3221225477"):
        gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert envs == ["CPU"]

    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    envs = _crash_harness(monkeypatch, gguf, first=1)
    with pytest.raises(RuntimeError, match="exited 1"):
        gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert envs == ["Vulkan0"] and gguf._GPU_CRASHED is False


def test_a_crash_on_cpu_after_the_gpu_retry_still_raises(monkeypatch, gguf):
    monkeypatch.setenv(gguf.BACKEND_ENV, "vulkan")
    envs = _crash_harness(monkeypatch, gguf, first=-11, second=-11)
    with pytest.raises(RuntimeError, match="exited -11"):
        gguf.OmniVoiceGGUFBackend()._run_subprocess(["tts"], stdin_text="hello")
    assert envs == ["Vulkan0", "CPU"]
    monkeypatch.setattr(gguf, "_GPU_CRASHED", False)


def test_metal_auto_run_qualifies_for_cpu_retry(monkeypatch):
    from engines.omnivoice_gguf import backend as b

    monkeypatch.setattr(b.sys, "platform", "darwin")
    monkeypatch.setattr(b.platform, "machine", lambda: "arm64")
    assert b._native_gpu_crash(-11, {}) is True
    assert b._native_gpu_crash(-11, {"GGML_BACKEND": "CPU"}) is False


def test_no_vulkan_adapter_reported_after_gpu_crash(monkeypatch):
    from engines.omnivoice_gguf import backend as b, compute

    monkeypatch.setattr(b, "_GPU_CRASHED", True)
    assert compute.selected_vulkan_adapter() is None


def test_quant_tier_survives_a_gpu_crash(monkeypatch):
    """After a GPU crash renders move to the CPU, but the quant tier (and so the
    voice) stays the same for the rest of the job."""
    from types import SimpleNamespace

    from engines.omnivoice_gguf import backend as b, compute, hardware_probe

    adapter = SimpleNamespace(effective_vram_mb=12_000, bf16=False)
    monkeypatch.setattr(compute, "_configured_vulkan_adapter", lambda: adapter)
    monkeypatch.setattr(b, "_GPU_CRASHED", False)
    before = compute.automatic_compute_class()
    monkeypatch.setattr(b, "_GPU_CRASHED", True)
    assert compute.selected_vulkan_adapter() is None
    assert compute.automatic_compute_class() == before != "cpu"
    assert before == hardware_probe._bucket(12_000) or before == "mid-vram"
