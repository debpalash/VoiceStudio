"""CPU-only (no discrete GPU) and Windows-on-ARM hosts must install and run.

Covers the backend half of the track: the TTS model loads in a precision the
CPU can actually execute, setup tells an ARM64 Windows user what to expect, the
CPU preset recommends a light Whisper, and the Electron/pyproject torch pins
stay equal. The Electron half (CPU wheels, x64 interpreter on ARM64) lives in
electron/src/main/runtime-torch-variant.test.ts.
"""
from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# Resolved per call: other tests pop and re-import ``core.*`` / ``omnivoice.*``,
# so a module-level import would patch a stale module object.
def _device_caps():
    return importlib.import_module("core.device_caps")


def _dtype():
    return importlib.import_module("omnivoice.utils.dtype")


# ── precision follows the device ─────────────────────────────────────────────

@pytest.mark.parametrize("device", ["cuda", "xpu", "mps", "privateuseone:0", "CUDA"])
def test_accelerators_keep_half_precision(device, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_CPU_DTYPE", raising=False)
    assert _dtype().tts_dtype_name(device) == "float16"


@pytest.mark.parametrize("device", ["cpu", "CPU", " cpu ", "", None])
def test_cpu_loads_float32(device, monkeypatch):
    # fp16 has no fast CPU GEMM — the old hard-coded float16 made CPU hosts crawl.
    monkeypatch.delenv("OMNIVOICE_CPU_DTYPE", raising=False)
    assert _dtype().tts_dtype_name(device) == "float32"


def test_cpu_dtype_override_is_validated(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_CPU_DTYPE", "bfloat16")
    assert _dtype().tts_dtype_name("cpu") == "bfloat16"
    monkeypatch.setenv("OMNIVOICE_CPU_DTYPE", "int4")
    assert _dtype().tts_dtype_name("cpu") == "float32"
    # The override never downgrades an accelerator.
    monkeypatch.setenv("OMNIVOICE_CPU_DTYPE", "bfloat16")
    assert _dtype().tts_dtype_name("cuda") == "float16"


class _Stop(Exception):
    pass


def _fake_torch():
    return types.SimpleNamespace(
        float16="fp16", float32="fp32", bfloat16="bf16", cuda=types.SimpleNamespace(
            is_available=lambda: False
        ),
    )


def _capture_dtype(monkeypatch, device):
    from services import model_manager as mm

    seen = {}

    class _Model:
        @staticmethod
        def from_pretrained(checkpoint, **kwargs):
            seen.update(kwargs)
            raise _Stop

    monkeypatch.setattr(mm, "_lazy_torch", _fake_torch)
    monkeypatch.setattr(mm, "_lazy_omnivoice", lambda: _Model)
    monkeypatch.setattr(mm, "get_best_device", lambda: device)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "stub/checkpoint")
    monkeypatch.setattr(mm, "should_preload_tts_asr", lambda: False)
    monkeypatch.delenv("OMNIVOICE_CPU_DTYPE", raising=False)
    return mm, seen


def test_in_process_loader_uses_float32_on_a_no_gpu_host(monkeypatch):
    mm, seen = _capture_dtype(monkeypatch, "cpu")
    with pytest.raises(_Stop):
        mm._load_model_sync()
    assert seen["dtype"] == "fp32"
    assert seen["device_map"] == "cpu"


def test_in_process_loader_keeps_float16_on_cuda(monkeypatch):
    mm, seen = _capture_dtype(monkeypatch, "cuda")
    with pytest.raises(_Stop):
        mm._load_model_sync()
    assert seen["dtype"] == "fp16"


@pytest.mark.parametrize("device,expected", [("cpu", "fp32"), ("mps", "fp16")])
def test_sidecar_loader_matches_the_in_process_loader(monkeypatch, device, expected):
    mm, seen = _capture_dtype(monkeypatch, device)
    from engines.omnivoice_subprocess import main as sidecar

    monkeypatch.setattr(sidecar, "_model", None)
    monkeypatch.setattr(sidecar, "_send", lambda *a, **k: None)
    with pytest.raises(_Stop):
        sidecar._load_model(None)
    assert seen["dtype"] == expected


def test_every_loader_goes_through_the_shared_dtype_policy():
    """The CLIs honour OMNIVOICE_CPU_DTYPE too, not just the backend loaders."""
    for rel in (
        "backend/services/model_manager.py",
        "backend/engines/omnivoice_subprocess/main.py",
        "omnivoice/cli/infer.py",
        "omnivoice/cli/demo.py",
        "omnivoice/cli/infer_batch.py",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "from omnivoice.utils.dtype import tts_dtype_name" in text, rel
        assert "tts_dtype_name(" in text, rel


def test_no_hardcoded_fp16_model_loads_remain():
    """Every OmniVoice.from_pretrained call site must be device-aware."""
    offenders = []
    for rel in (
        "backend/services/model_manager.py",
        "backend/engines/omnivoice_subprocess/main.py",
        "omnivoice/cli/infer.py",
        "omnivoice/cli/demo.py",
        "omnivoice/cli/infer_batch.py",
    ):
        for number, line in enumerate((REPO / rel).read_text("utf-8").splitlines(), 1):
            if "dtype=torch.float16" in line.replace(" ", "") and "else torch.float16" not in line:
                offenders.append(f"{rel}:{number}")
    assert not offenders, f"hard-coded float16 model load (CPU hosts crawl): {offenders}"


# ── Windows on ARM detection ─────────────────────────────────────────────────

def _host(monkeypatch, platform, machine, env=None):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(_device_caps()._platform, "machine", lambda: machine)
    for key in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)


def test_x64_python_under_arm_emulation_is_detected(monkeypatch):
    # platform.machine() says AMD64 inside the emulated interpreter.
    _host(monkeypatch, "win32", "AMD64", {
        "PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_ARCHITEW6432": "ARM64",
    })
    assert _device_caps().is_windows_on_arm() is True


def test_native_arm64_python_is_detected(monkeypatch):
    _host(monkeypatch, "win32", "ARM64", {"PROCESSOR_ARCHITECTURE": "ARM64"})
    assert _device_caps().is_windows_on_arm() is True


def test_ordinary_x64_windows_and_other_platforms_are_not_arm(monkeypatch):
    _host(monkeypatch, "win32", "AMD64", {"PROCESSOR_ARCHITECTURE": "AMD64"})
    assert _device_caps().is_windows_on_arm() is False
    # Apple Silicon / Linux arm64 are not "Windows on ARM", whatever the env says.
    _host(monkeypatch, "darwin", "arm64", {"PROCESSOR_ARCHITECTURE": "ARM64"})
    assert _device_caps().is_windows_on_arm() is False
    _host(monkeypatch, "linux", "aarch64")
    assert _device_caps().is_windows_on_arm() is False


def _preflight(monkeypatch):
    from api.routers.setup import wizard
    import services.media_tools as media_tools

    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass",
        "detail": "stubbed", "fix": None, "mirror_reachable": True,
    })
    monkeypatch.setattr(media_tools, "summary", lambda auto_acquire=True: None)
    resp = wizard.preflight()
    checks = resp["checks"] if isinstance(resp, dict) else resp.checks
    return resp, {
        (c if isinstance(c, dict) else c.model_dump())["id"]:
            (c if isinstance(c, dict) else c.model_dump())
        for c in checks
    }


def test_preflight_tells_a_windows_on_arm_user_what_to_expect(monkeypatch):
    from api.routers.setup import wizard

    monkeypatch.setattr(wizard, "is_windows_on_arm", lambda: True)
    resp, checks = _preflight(monkeypatch)
    arch = checks["arch"]
    # Informational: setup must stay usable on a Snapdragon laptop.
    assert arch["status"] == "warn"
    assert "emulation" in arch["detail"] and "CPU" in arch["detail"]
    ok = resp["ok"] if isinstance(resp, dict) else resp.ok
    assert ok is True or checks["ram"]["status"] == "fail" or checks["disk"]["status"] == "fail"


def test_preflight_has_no_arm_row_elsewhere(monkeypatch):
    from api.routers.setup import wizard

    monkeypatch.setattr(wizard, "is_windows_on_arm", lambda: False)
    _resp, checks = _preflight(monkeypatch)
    assert "arch" not in checks


def test_cpu_only_preflight_advises_light_engines_without_blocking(monkeypatch):
    from api.routers.setup import wizard

    monkeypatch.setattr(wizard, "is_windows_on_arm", lambda: False)
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": "unknown", "backend": "cpu", "available": False,
        "driver": None, "device_name": None, "notes": [],
    })
    _resp, checks = _preflight(monkeypatch)
    gpu = checks["gpu"]
    assert gpu["status"] == "warn"  # advisory, never "fail"
    assert "CPU" in gpu["detail"]
    assert "integrated" in gpu["fix"] and "lightweight" in gpu["fix"]


# ── CPU preset ───────────────────────────────────────────────────────────────

def test_cpu_preset_includes_the_light_whisper():
    import yaml

    models = yaml.safe_load((REPO / "backend/config/models.yaml").read_text("utf-8"))["models"]
    by_id = {m["repo_id"]: m for m in models}
    assert "cpu" in by_id["Systran/faster-whisper-small"]["curated_on"]
    # ...and it fits a small laptop: well under the heavyweight picks.
    assert by_id["Systran/faster-whisper-small"]["size_gb"] < 1


# ── torch pins stay in lockstep ──────────────────────────────────────────────

def test_electron_cpu_torch_pins_match_pyproject_constraints():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    constraints = dict(re.findall(r'"(torch|torchaudio|torchvision)==([\d.]+)"', pyproject))
    ts = (REPO / "electron/src/main/runtime-project.ts").read_text(encoding="utf-8")
    block = ts[ts.index("export const CPU_TORCH_PINS"):]
    block = block[: block.index("] as const")]
    pins = dict(re.findall(r"'(torch|torchaudio|torchvision)==([\d.]+)\+cpu'", block))
    assert pins == constraints, (
        "Electron CPU torch pins drifted from [tool.uv].constraint-dependencies"
    )
    py_pins = (REPO / "backend/services/sidecar_install.py").read_text(encoding="utf-8")
    assert "+cpu" in py_pins and "UV_PIP_CPU_ARGS" in py_pins
    from core.torch_indexes import PYTORCH_CPU_INDEX_URL

    assert PYTORCH_CPU_INDEX_URL in ts, "Electron and Python must use the same CPU index"
