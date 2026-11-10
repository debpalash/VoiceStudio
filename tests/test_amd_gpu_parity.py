"""AMD GPU parity for PyTorch engines.

Every PyTorch engine that runs on an NVIDIA GPU claims ROCm, because a ROCm
(HIP) torch answers ``torch.cuda`` with the AMD GPU. A sidecar's claim holds
only when its own venv carries that HIP torch: routing must say "CPU" when
the venv still has the CUDA or CPU wheel and "unknown" until GPU execution
is verified. A managed install that now takes a ROCm torch on this host is
offered the repair when its venv predates that.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_CUDA_TORCH = "__version__ = '2.11.0+cu128'\ncuda = '12.8'\nhip = None\n"
_ROCM_TORCH = "__version__ = '2.11.0+rocm7.2'\ncuda = None\nhip = '7.2.0'\n"

def _rocm():
    """Build host metadata from the current test module registry."""
    from core.device_caps import HostCaps
    return HostCaps(family="rocm", available_families=("rocm", "cpu"), vram_gb=12.0)

# (module, class) of every sidecar PyTorch engine with a cheap venv lookup.
_SIDECARS = [
    ("engines.voxcpm2_subprocess", "VoxCPM2SubprocessBackend"),
    ("engines.moss_tts_nano_subprocess", "MossTTSNanoSubprocessBackend"),
    ("engines.cosyvoice_subprocess", "CosyVoiceSubprocessBackend"),
    ("engines.dots_tts", "DotsTTSBackend"),
    ("engines.confucius4", "Confucius4Backend"),
    ("engines.moss_tts_v15", "MossTTSV15Backend"),
]


def _cls(module, name):
    return getattr(importlib.import_module(module), name)


def _venv(tmp_path: Path, version_py: str | None) -> Path:
    venv = tmp_path / ".venv"
    py = venv / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    if version_py is not None:
        torch = venv / "lib" / "python3.11" / "site-packages" / "torch"
        torch.mkdir(parents=True)
        (torch / "version.py").write_text(version_py)
    return py


@pytest.mark.parametrize(("module", "name"), _SIDECARS)
def test_sidecar_engines_claim_rocm(module, name):
    assert "rocm" in _cls(module, name).gpu_compat


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("services.tts_backend", "VoxCPM2Backend"),
        ("services.tts_backend", "MossTTSNanoBackend"),
        ("services.tts_backend", "CosyVoiceBackend"),
    ],
)
def test_in_process_twins_claim_rocm(module, name):
    """The in-process twins run on the app's torch, which is the HIP build on
    a ROCm host, so they keep the same claim as their sidecars."""
    assert "rocm" in _cls(module, name).gpu_compat


@pytest.mark.parametrize(("module", "name"), _SIDECARS)
def test_sidecar_with_cuda_torch_routes_to_cpu_on_amd(monkeypatch, tmp_path, module, name):
    """Fail-before/pass-after: a venv with the CUDA wheel was reported as
    accelerated on an AMD host while it actually ran on the CPU."""
    cls = _cls(module, name)
    py = _venv(tmp_path, _CUDA_TORCH)
    monkeypatch.setattr(cls, "sidecar_python_hint", classmethod(lambda c: py))
    profile = cls.runtime_compute_profile(_rocm())
    assert "rocm" not in profile["gpu_compat"]
    assert profile["effective_device"] == "cpu"
    assert profile["routing_status"] == "cpu_fallback"


@pytest.mark.parametrize(("module", "name"), _SIDECARS)
def test_sidecar_hip_build_alone_does_not_prove_gpu_kernels(monkeypatch, tmp_path, module, name):
    cls = _cls(module, name)
    py = _venv(tmp_path, _ROCM_TORCH)
    monkeypatch.setattr(cls, "sidecar_python_hint", classmethod(lambda c: py))
    profile = cls.runtime_compute_profile(_rocm())
    assert profile["effective_device"] == "unknown"
    assert profile["routing_status"] == "unknown"


def test_unknown_sidecar_venv_is_unknown_except_windows_opt_in(monkeypatch, tmp_path):
    """Do not claim acceleration until the selected sidecar is known."""
    import services.subprocess_backend as sb
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend as cls

    py = _venv(tmp_path, None)  # no torch wheel found: unknown
    monkeypatch.setattr(cls, "sidecar_python_hint", classmethod(lambda c: py))
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    assert cls.runtime_compute_profile(_rocm())["effective_device"] == "unknown"
    monkeypatch.setattr(sb.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    assert cls.runtime_compute_profile(_rocm())["effective_device"] == "cpu"


def test_non_amd_hosts_are_untouched(monkeypatch, tmp_path):
    """Retain CUDA routing regardless of the AMD metadata guard."""
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend as cls

    py = _venv(tmp_path, _CUDA_TORCH)
    monkeypatch.setattr(cls, "sidecar_python_hint", classmethod(lambda c: py))
    from core.device_caps import HostCaps
    cuda = HostCaps(family="cuda", available_families=("cuda", "cpu"), vram_gb=12.0)
    profile = cls.runtime_compute_profile(cuda)
    assert profile["gpu_compat"] == cls.gpu_compat
    assert profile["effective_device"] == "cuda"


def test_marker_install_with_old_torch_is_offered_the_rocm_repair(monkeypatch, tmp_path):
    """A complete MOSS-TTS-Nano install made before it took a ROCm torch on
    this host reads unhealthy, so the catalogue offers the dependency repair
    (the step is idempotent: the venv stays, torch is swapped)."""
    from services import sidecar_install as si

    spec = si.get_spec("moss-tts-nano")
    checkout = tmp_path / "managed-cosy-independent"
    monkeypatch.setattr(si, "managed_checkout", lambda s: checkout)
    checkout.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(si, "_user_managed_dir", lambda s: None)
    monkeypatch.setattr(si, "_source_present", lambda s, c: True)
    monkeypatch.setattr(si, "_install_marker_valid", lambda s, c: True)
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!fake\n")
    torch = checkout / ".venv" / "lib" / "python3.11" / "site-packages" / "torch"
    torch.mkdir(parents=True, exist_ok=True)

    (torch / "version.py").write_text(_CUDA_TORCH)
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    assert si._healthy(spec) is False
    (torch / "version.py").write_text(_ROCM_TORCH)
    assert si._healthy(spec) is True
    # CUDA and CPU hosts keep "installed means installed".
    (torch / "version.py").write_text(_CUDA_TORCH)
    for family in ("cuda", "cpu"):
        monkeypatch.setattr(si, "_host_family", lambda f=family: f)
        assert si._healthy(spec) is True
