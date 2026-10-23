"""Regression coverage for the pinned GGUF runtime's compute contract (#3042)."""
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def gguf():
    """Resolve the current backend after any earlier sys.modules replacement."""
    import importlib

    return importlib.import_module("engines.omnivoice_gguf.backend")


def test_loader_presence_does_not_prove_engine_acceleration():
    """A loader cannot establish that an executable can use a physical GPU."""
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps(family="cpu", available_families=("cpu",),
                    notes=("Vulkan runtime present",))
    result = resolve_routing(("cuda", "vulkan", "mps", "cpu"), caps)
    assert result["effective_device"] == "cpu"


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
