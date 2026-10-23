"""GGUF quant sizing must respect CPU choices and unverified native capacity."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def gguf():
    """Resolve the live backend after any earlier module replacement."""
    return importlib.import_module("engines.omnivoice_gguf.backend")


@pytest.mark.parametrize("cpu_choice", ["global", "settings", "engine", "native"])
@pytest.mark.parametrize("quant_override", [None, "auto", "omnivoice-base-F32.gguf"])
def test_cpu_choice_selects_cpu_quant_without_losing_explicit_override(
    monkeypatch, gguf, cpu_choice, quant_override,
):
    """A high-VRAM GPU must not enlarge the automatic CPU model selection."""
    probe = importlib.import_module("engines.omnivoice_gguf.hardware_probe")
    prefs = importlib.import_module("core.prefs")
    for variable in ("OMNIVOICE_DEVICE", "OMNIVOICE_GGUF_BACKEND",
                     "OMNIVOICE_GGUF_DEVICE", "GGML_BACKEND"):
        monkeypatch.delenv(variable, raising=False)
    if cpu_choice == "settings":
        monkeypatch.setattr(prefs, "resolve", lambda *args, **kwargs: "cpu")
    else:
        monkeypatch.setenv({"global": "OMNIVOICE_DEVICE",
                           "engine": "OMNIVOICE_GGUF_BACKEND",
                           "native": "GGML_BACKEND"}[cpu_choice],
                          "CPU" if cpu_choice == "native" else "cpu")
    monkeypatch.setattr(probe, "detect_capabilities", lambda: SimpleNamespace(
        backend="cuda", vram_mb=24_576, compute_class="high-vram"))
    engine = gguf.OmniVoiceGGUFBackend(quant_override=quant_override)
    selected = engine._select_quant_entry()
    if quant_override not in (None, "auto"):
        assert selected["base"] == quant_override
    else:
        assert selected == engine._ensure_quant_map()["cpu"]


@pytest.mark.parametrize("native_choice", ["auto", "vulkan", "cuda", "native-vulkan"])
@pytest.mark.parametrize("host_backend", ["cuda", "mps"])
@pytest.mark.parametrize("quant_override", [None, "auto", "omnivoice-base-BF16.gguf",
                                          "omnivoice-base-F32.gguf"])
def test_unverified_native_capacity_uses_conservative_quant(
    monkeypatch, gguf, native_choice, host_backend, quant_override,
):
    """A parent GPU's memory cannot establish the selected child adapter's capacity."""
    probe = importlib.import_module("engines.omnivoice_gguf.hardware_probe")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setenv("OMNIVOICE_GGUF_BACKEND", native_choice
                       if native_choice != "native-vulkan" else "auto")
    monkeypatch.delenv("OMNIVOICE_GGUF_DEVICE", raising=False)
    monkeypatch.delenv("GGML_BACKEND", raising=False)
    if native_choice == "native-vulkan":
        monkeypatch.setenv("GGML_BACKEND", "Vulkan1")
    elif native_choice in {"vulkan", "cuda"}:
        monkeypatch.setenv("OMNIVOICE_GGUF_DEVICE", "1")
    monkeypatch.setattr(probe, "detect_capabilities", lambda: SimpleNamespace(
        backend=host_backend, vram_mb=24_576, compute_class="high-vram"))
    engine = gguf.OmniVoiceGGUFBackend(quant_override=quant_override)
    selected = engine._select_quant_entry()
    if quant_override == "omnivoice-base-BF16.gguf":
        assert selected == engine._ensure_quant_map()["high-vram"]
    elif quant_override == "omnivoice-base-F32.gguf":
        assert selected["base"] == quant_override
    else:
        assert selected == engine._ensure_quant_map()["cpu"]
    # Quant sizing must preserve the selected native backend/device environment.
    expected = {"vulkan": "Vulkan1", "cuda": "CUDA1",
                "native-vulkan": "Vulkan1"}.get(native_choice)
    assert gguf._spawn_env().get("GGML_BACKEND") == expected
