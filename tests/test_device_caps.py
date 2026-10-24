"""Unit tests for backend/core/device_caps.py (GPU compatibility matrix, PR 1).

Every host shape is exercised by mocking ``torch`` (and friends) via
``sys.modules`` and re-probing with ``device_caps.refresh()`` — the probe imports
torch lazily inside ``_probe()``, so the override lands. Covers the full §1a
degradation contract: torch-unimportable, CUDA-init-raises, device_count==0,
multi-GPU, mem_get_info failure, arch mismatch, ROCm vs CUDA, MPS, XPU, DirectML,
and the cpu-only baseline + caching.
"""
from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from core import device_caps
from core.device_caps import DIRECTML_MARKER, KERNEL_RISK_MARKER


def _torch_mock(
    *,
    cuda_available=False,
    cuda_raises=False,
    device_count=1,
    hip=None,
    device_name="NVIDIA RTX 4090",
    total_vram_bytes=24 * 1024 ** 3,
    mem_raises=False,
    capability=(8, 9),
    arch_list=None,
    mps_available=False,
    xpu_available=False,
):
    """Build a torch-module mock with a controllable accelerator shape."""

    def _is_available():
        if cuda_raises:
            raise RuntimeError("CUDA init blew up")
        return cuda_available

    def _mem_get_info():
        if mem_raises:
            raise RuntimeError("mem_get_info refused")
        return (total_vram_bytes // 2, total_vram_bytes)

    cuda = types.SimpleNamespace(
        is_available=_is_available,
        device_count=lambda: device_count,
        get_device_name=lambda i: device_name,
        mem_get_info=_mem_get_info,
        get_device_capability=lambda i: capability,
        _get_arch_list=lambda: (arch_list if arch_list is not None else []),
    )
    version = types.SimpleNamespace()
    if hip is not None:
        version.hip = hip
    backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_available)
    )
    xpu = types.SimpleNamespace(
        is_available=lambda: xpu_available,
        get_device_name=lambda i: "Intel Arc A770",
    )
    return types.SimpleNamespace(
        cuda=cuda, version=version, backends=backends, xpu=xpu
    )


def _probe_with(modules):
    with patch.dict("sys.modules", modules):
        return device_caps.refresh()


def test_torch_unimportable_degrades_to_cpu_probe_not_ok():
    # A sentinel that raises on import is awkward; instead drop torch from
    # sys.modules and block re-import via a finder is heavy — simplest is to
    # map "torch" to something whose attribute access is irrelevant because the
    # import itself must fail. Use a builtins.__import__ shim.
    real_import = __import__

    def _blocked(name, *a, **k):
        if name == "torch":
            raise ImportError("no torch here")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", _blocked):
        caps = device_caps.refresh()
    assert caps.probe_ok is False
    assert caps.family == "cpu"
    assert caps.available_families == ("cpu",)
    assert any("torch not importable" in n for n in caps.notes)


def test_cuda_host_clean():
    caps = _probe_with({"torch": _torch_mock(cuda_available=True)})
    assert caps.family == "cuda"
    assert caps.available_families == ("cuda", "cpu")
    assert caps.device_name == "NVIDIA RTX 4090"
    assert round(caps.vram_gb) == 24
    assert caps.driver is None
    assert caps.notes == ()
    assert caps.probe_ok is True


def test_rocm_distinguished_from_cuda():
    caps = _probe_with({"torch": _torch_mock(cuda_available=True, hip="6.1.40091")})
    assert caps.family == "rocm"
    assert caps.available_families == ("rocm", "cpu")
    assert caps.driver == "6.1.40091"


def test_cuda_available_but_zero_devices_is_not_cuda():
    caps = _probe_with({"torch": _torch_mock(cuda_available=True, device_count=0)})
    assert caps.family == "cpu"
    assert any("device_count==0" in n for n in caps.notes)


def test_cuda_init_raises_is_swallowed_probe_stays_ok():
    caps = _probe_with({"torch": _torch_mock(cuda_raises=True)})
    assert caps.family == "cpu"
    assert caps.probe_ok is True
    assert any("CUDA init raised" in n for n in caps.notes)


def test_mem_get_info_failure_keeps_family_zeroes_vram():
    caps = _probe_with({"torch": _torch_mock(cuda_available=True, mem_raises=True)})
    assert caps.family == "cuda"
    assert caps.vram_gb == 0.0
    assert any("VRAM query failed" in n for n in caps.notes)


def test_arch_mismatch_emits_kernel_risk_note(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    caps = _probe_with({
        "torch": _torch_mock(
            cuda_available=True, capability=(12, 0), arch_list=["sm_80", "sm_89"]
        )
    })
    assert caps.family == "cuda"
    assert any(KERNEL_RISK_MARKER in n for n in caps.notes)


def test_arch_in_build_emits_no_note():
    caps = _probe_with({
        "torch": _torch_mock(
            cuda_available=True, capability=(8, 9), arch_list=["sm_80", "sm_89"]
        )
    })
    assert caps.notes == ()


def test_multi_gpu_advisory_note():
    caps = _probe_with({"torch": _torch_mock(cuda_available=True, device_count=3)})
    assert caps.family == "cuda"
    assert any("3 GPUs detected" in n for n in caps.notes)
    # advisory, not a kernel risk
    assert not any(KERNEL_RISK_MARKER in n for n in caps.notes)


def test_mps_host_uses_half_ram():
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(total=32 * 1024 ** 3)
    )
    caps = _probe_with({
        "torch": _torch_mock(mps_available=True),
        "psutil": fake_psutil,
    })
    assert caps.family == "mps"
    assert caps.available_families == ("mps", "cpu")
    assert round(caps.vram_gb) == 16
    assert caps.device_name == "Apple Silicon (MPS)"


def test_xpu_host():
    caps = _probe_with({
        "torch": _torch_mock(xpu_available=True),
        "intel_extension_for_pytorch": types.SimpleNamespace(),
    })
    assert caps.family == "xpu"
    assert caps.available_families == ("xpu", "cpu")
    assert caps.vram_gb == 0.0


def test_directml_present_reports_cpu_with_marker():
    caps = _probe_with({
        "torch": _torch_mock(),
        "torch_directml": types.SimpleNamespace(
            device_count=lambda: 1, device=lambda i: "privateuseone:0"
        ),
    })
    assert caps.family == "cpu"
    assert any(DIRECTML_MARKER in n for n in caps.notes)


def test_hybrid_cuda_plus_xpu_keeps_both_in_available():
    # NVIDIA GPU + Intel iGPU via IPEX: family is the priority pick (cuda) but
    # available_families must not drop the secondary accelerator.
    caps = _probe_with({
        "torch": _torch_mock(cuda_available=True, xpu_available=True),
        "intel_extension_for_pytorch": types.SimpleNamespace(),
    })
    assert caps.family == "cuda"
    assert caps.available_families == ("cuda", "xpu", "cpu")


def test_cpu_only_baseline():
    caps = _probe_with({"torch": _torch_mock()})
    assert caps.family == "cpu"
    assert caps.available_families == ("cpu",)
    assert caps.notes == ()
    assert caps.probe_ok is True


def test_cpu_always_in_available_families_invariant():
    for mods in (
        {"torch": _torch_mock(cuda_available=True)},
        {"torch": _torch_mock(cuda_available=True, hip="6.1")},
        {"torch": _torch_mock(mps_available=True),
         "psutil": types.SimpleNamespace(
             virtual_memory=lambda: types.SimpleNamespace(total=8 * 1024 ** 3))},
        {"torch": _torch_mock()},
    ):
        caps = _probe_with(mods)
        assert "cpu" in caps.available_families


def test_result_is_cached_until_refresh():
    first = _probe_with({"torch": _torch_mock(cuda_available=True)})
    # No refresh → same cached object even though the (now-restored) real torch
    # would probe differently.
    again = device_caps.detect_host_caps()
    assert again is first
    # refresh re-probes against whatever torch is now live.
    device_caps.refresh()


def teardown_module(_module):
    # Drop any cached mock-derived result so other test modules re-probe clean.
    device_caps.detect_host_caps.cache_clear()


def test_builtin_xpu_does_not_require_ipex():
    caps = _probe_with({'torch': _torch_mock(xpu_available=True), 'intel_extension_for_pytorch': None})
    assert caps.family == 'xpu'


def test_registered_npu_is_reported_without_importing_vendor_packages():
    torch = _torch_mock()
    torch.npu = types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda i: 'Ascend')
    caps = _probe_with({'torch': torch, 'intel_extension_for_pytorch': None})
    assert caps.family == 'npu'
    assert caps.available_families == ('npu', 'cpu')
    assert caps.device_name == 'Ascend'


def test_unavailable_npu_does_not_claim_acceleration():
    torch = _torch_mock()
    torch.npu = types.SimpleNamespace(is_available=lambda: False)
    caps = _probe_with({'torch': torch, 'intel_extension_for_pytorch': None})
    assert caps.family == 'cpu'


@pytest.mark.parametrize("npu_available,expected", [(True, "cpu"), (False, "privateuseone:0")])
def test_generic_directml_loader_respects_selected_family(monkeypatch, npu_available, expected):
    import importlib
    from services import model_manager

    # Other tests reload core modules; patch the same module the loader imports.
    live_caps = importlib.import_module("core.device_caps")
    torch = _torch_mock()
    torch.npu = types.SimpleNamespace(is_available=lambda: npu_available, get_device_name=lambda i: "NPU")
    modules = {
        "torch": torch,
        "torch_directml": types.SimpleNamespace(device_count=lambda: 1, device=lambda i: "privateuseone:0"),
    }
    with patch.dict("sys.modules", modules):
        try:
            caps = live_caps.refresh()
        finally:
            live_caps.detect_host_caps.cache_clear()
    assert caps.family == ("npu" if npu_available else "cpu")
    monkeypatch.setattr(live_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: torch)
    with patch.dict("sys.modules", modules):
        assert model_manager.get_best_device() == expected


@pytest.fixture
def clear_caps_cache():
    clear = device_caps.detect_host_caps.cache_clear
    yield
    clear()


@pytest.mark.parametrize("requested", ["auto", "cuda"])
def test_unsupported_cuda_is_not_an_available_compute_family(monkeypatch, requested, clear_caps_cache):
    from services.engine_routing import resolve_routing

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setenv("OMNIVOICE_DEVICE", requested)
    caps = _probe_with({"torch": _torch_mock(
        cuda_available=True, capability=(5, 2), arch_list=["sm_70", "sm_80", "sm_120"],
        device_name="Quadro M5000M",
    )})
    assert caps.family == "cpu"
    assert "cuda" not in caps.available_families
    result = resolve_routing(("cuda", "cpu"), caps)
    assert result["routing_status"] == "cpu_fallback"
    assert result["effective_device"] == "cpu"
    assert "sm_52" in result["routing_reason"]
    assert resolve_routing(("cuda",), caps)["routing_status"] == "unavailable"


def test_force_cuda_keeps_unsupported_gpu_with_warning(monkeypatch, clear_caps_cache):
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "true")
    caps = _probe_with({"torch": _torch_mock(
        cuda_available=True, capability=(5, 2), arch_list=["sm_70"],
    )})
    assert caps.family == "cuda"
    assert any(KERNEL_RISK_MARKER in note for note in caps.notes)


def test_unsupported_cuda_does_not_hide_a_working_xpu(monkeypatch, clear_caps_cache):
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    caps = _probe_with({"torch": _torch_mock(
        cuda_available=True, capability=(5, 2), arch_list=["sm_70"], xpu_available=True,
    )})
    assert caps.family == "xpu"
    assert caps.available_families == ("xpu", "cpu")


def test_unsupported_rocm_is_not_an_available_compute_family(monkeypatch, clear_caps_cache):
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    torch = _torch_mock(cuda_available=True, hip="6.4", arch_list=["gfx1100"])
    torch.cuda.get_device_properties = lambda _: types.SimpleNamespace(gcnArchName="gfx803")
    caps = _probe_with({"torch": torch})
    assert caps.family == "cpu"
    assert caps.available_families == ("cpu",)


@pytest.mark.parametrize("forced", [False, True])
def test_preflight_does_not_call_unsupported_cuda_ready(monkeypatch, tmp_path, forced, clear_caps_cache):
    from api.routers.setup import wizard
    from services import media_tools, tts_backend
    from services.engine_routing import resolve_routing

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    if forced:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    caps = _probe_with({"torch": _torch_mock(
        cuda_available=True, capability=(5, 2), arch_list=["sm_70"],
        device_name="Quadro M5000M",
    )})
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(wizard._platform, "machine", lambda: "arm64")
    monkeypatch.setattr(wizard, "is_windows_on_arm", lambda: False)
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 32)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _: 100)
    monkeypatch.setattr(wizard, "hf_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass", "detail": "offline test", "fix": None,
    })
    monkeypatch.setattr(media_tools, "summary", lambda **_: None)
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": "nvidia", "driver": "580.178.04", "device_name": "Quadro M5000M",
        "backend": "cuda", "available": True, "notes": [],
    })
    monkeypatch.setattr(tts_backend, "gpu_routing_verdict", lambda: {
        **resolve_routing(("cuda", "cpu"), caps), "engine": "omnivoice", "host_family": caps.family,
    })
    result = wizard.preflight()
    gpu = next(row for row in result["checks"] if row["id"] == "gpu")
    assert gpu["status"] == "warn"
    assert "CUDA ready" not in gpu["detail"]
    assert "sm_52" in gpu["detail"]
    assert "driver alone" in gpu["fix"]
    assert result["device"]["gpu_available"] is forced
    assert result["device"]["gpu_backend"] == ("cuda" if forced else "cpu")
    assert result["gpu_routing"]["effective_device"] == ("cuda" if forced else "cpu")
