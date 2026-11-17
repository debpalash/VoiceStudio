"""#3978: a pre-Volta NVIDIA card (Maxwell/Pascal, capability < 7.0) cannot be
helped by any cu128 build, so every place that used to say "install a cu128
build" must say what is true and point at the Vulkan engines instead.

Fake torch / fake hosts only: no GPU, runs on any CI OS.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "electron", "src", "renderer", "src", "i18n", "locales",
)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
CUDA_BUILD = {"kind": "cuda", "version": "2.10.0+cu128", "runtime": "12.8"}
CU128 = ["sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]


def _m(name):
    return importlib.import_module(name)


def _torch(cc, archs, name):
    cuda = SimpleNamespace(
        is_available=lambda: True,
        get_device_capability=lambda i=0: cc,
        get_device_name=lambda i=0: name,
        get_arch_list=lambda: list(archs),
    )
    return SimpleNamespace(cuda=cuda, version=SimpleNamespace(hip=None, cuda="12.8"))


def _caps(name, arch):
    marker = _m("core.device_caps").KERNEL_RISK_MARKER
    note = f"{name} ({arch}) not in this torch build's archs ({', '.join(CU128)}) — {marker}"
    return _m("core.device_caps").HostCaps(
        family="cuda", available_families=("cuda", "cpu"),
        notes=(note,), device_name=name, probe_ok=True,
    )


def _gpu(name):
    return _m("core.gpu_inventory").HostGPU(vendor="nvidia", name=name, vram_gb=8.0, discrete=True)


@pytest.fixture(autouse=True)
def _no_force(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)


ROWS = [
    {"id": "cosyvoice-gguf", "display_name": "CosyVoice GGUF", "gpu_compat": ["cpu", "vulkan"], "available": True},
    {"id": "omnivoice", "display_name": "OmniVoice", "gpu_compat": ["cuda", "cpu"], "available": True},
    {"id": "audiocpp", "display_name": "audio.cpp", "gpu_compat": ["cpu", "vulkan"], "available": False},
]


def test_is_legacy_arch():
    dc = _m("core.device_caps")
    for tag in ("sm_52", "sm_61", "sm_37"):
        assert dc.is_legacy_nvidia_arch(tag)
    for tag in ("sm_70", "sm_75", "sm_89", "sm_120", "gfx1100", "banana"):
        assert not dc.is_legacy_nvidia_arch(tag)


def test_sm61_loader_message_is_accurate_and_has_no_cu128_advice(monkeypatch):
    mm = _m("services.model_manager")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: _torch((6, 1), CU128, "NVIDIA GeForce GTX 1080 Ti"))
    ok, warning = mm.check_device_compatibility()
    assert ok is False
    assert "cu128" not in warning and "--index-url" not in warning
    assert _m("core.device_caps").LEGACY_NVIDIA_MARKER in warning
    assert "CPU" in warning and "Vulkan" in warning


def test_sm75_mismatched_build_keeps_cu128_advice(monkeypatch):
    mm = _m("services.model_manager")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: _torch((7, 5), ["sm_80", "sm_86"], "NVIDIA T4"))
    ok, warning = mm.check_device_compatibility()
    assert ok is False
    assert "cu128" in warning


def test_generation_error_for_legacy_card(monkeypatch):
    gen = _m("api.routers.generation")
    monkeypatch.setitem(sys.modules, "torch", _torch((6, 1), CU128, "GTX 1060"))
    with pytest.raises(RuntimeError) as exc:
        gen._oom_friendly_reraise(RuntimeError("CUDA error: no kernel image is available for execution"))
    assert "cu128" not in str(exc.value)
    assert "Vulkan" in str(exc.value)


def test_generation_error_for_newer_card_unchanged(monkeypatch):
    gen = _m("api.routers.generation")
    monkeypatch.setitem(sys.modules, "torch", _torch((7, 5), ["sm_80"], "T4"))
    with pytest.raises(RuntimeError) as exc:
        gen._oom_friendly_reraise(RuntimeError("no kernel image is available for execution"))
    assert "cu128" in str(exc.value)


def test_report_for_sm61_offers_vendor_neutral_vulkan_option_from_gpu_compat():
    gr = _m("core.gpu_report")
    card = _gpu("NVIDIA GeForce GTX 1080 Ti")
    report = gr.build_gpu_report(_caps(card.name, "sm_61"), (card,), CUDA_BUILD, ROWS, [], platform="linux")
    assert report["state"] == "kernels_missing_legacy"
    assert report["options"] == ["vulkan_engines_any"]
    assert "install_matching_torch" not in report["options"]
    assert report["params"]["vulkan_engines"] == "CosyVoice GGUF, audio.cpp"
    assert report["params"]["gpu"] == card.name


def test_report_for_sm75_mismatch_is_unchanged():
    gr = _m("core.gpu_report")
    card = _gpu("NVIDIA T4")
    report = gr.build_gpu_report(_caps(card.name, "sm_75"), (card,), CUDA_BUILD, ROWS, [], platform="linux")
    assert report["state"] == "kernels_missing"
    assert report["options"] == ["install_matching_torch"]


def test_amd_states_keep_their_options():
    gr = _m("core.gpu_report")
    assert gr._OPTIONS["amd_cuda_build:win32"] == ("vulkan_engines", "rocm_windows_manual")
    assert gr._OPTIONS["amd_cpu_build:linux"] == ("rocm_variant_linux", "vulkan_engines")
    assert "kernels_missing_legacy" in gr.UNUSABLE_GPU_STATES


@pytest.mark.parametrize("locale", sorted(f[:-5] for f in os.listdir(LOCALES_DIR) if f.endswith(".json")))
def test_every_locale_has_the_legacy_strings(locale):
    with open(os.path.join(LOCALES_DIR, f"{locale}.json"), encoding="utf-8") as fh:
        settings = json.load(fh)["settings"]
    state = settings["gpu_report_state_kernels_missing_legacy"]
    assert set(_PLACEHOLDER.findall(state)) == {"gpu", "torch"}
    option = settings["gpu_report_option_vulkan_engines_any"]
    assert set(_PLACEHOLDER.findall(option)) == {"vulkan_engines"}
    # Neutral: the NVIDIA option must not be the AMD Radeon text.
    assert option != settings["gpu_report_option_vulkan_engines"]


def test_backend_notification_levels_are_accepted_by_the_renderer():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "electron", "src", "renderer", "src", "components",
                           "app-shell", "system-notifications.tsx"), encoding="utf-8") as fh:
        union = re.search(r"level: ((?:'\w+'\s*\|?\s*)+);", fh.read()).group(1)
    accepted = set(re.findall(r"'(\w+)'", union))
    with open(os.path.join(root, "backend", "api", "routers", "system.py"), encoding="utf-8") as fh:
        emitted = set(re.findall(r'"level": "(\w+)"', fh.read()))
    assert emitted <= accepted, f"backend emits unknown notification levels: {emitted - accepted}"


def test_legacy_advice_promises_cuda_126_only_where_setup_will_select_it(monkeypatch):
    dc = _m("core.device_caps")
    monkeypatch.setattr(dc, "legacy_cuda_repair_eligible", lambda: True)
    advice = dc.legacy_nvidia_advice("GTX 1070", "sm_61")
    assert "CUDA 12.6" in advice and "cu128" not in advice
    assert dc.LEGACY_NVIDIA_MARKER in advice and "Repair" in advice
    assert "Repair" in dc.legacy_cuda_repair_advice()
    monkeypatch.setattr(dc, "legacy_cuda_repair_eligible", lambda: False)
    advice = dc.legacy_nvidia_advice("GTX 780", "sm_35")
    assert "Repair" not in advice and "CUDA 12.6" not in advice
    assert "Vulkan" in advice and dc.LEGACY_NVIDIA_MARKER in advice
    assert "CUDA 12.6" not in dc.legacy_cuda_repair_advice()


def test_repair_eligibility_matches_runtime_selection_rules():
    f = _m("core.device_caps").legacy_cuda_from_smi
    assert f("6.1, 580.95.05\n")
    assert f("5.2, 528.33\n6.1, 528.33")
    assert not f("3.5, 470.0")  # Kepler
    assert not f("3.5, 580.0\n6.1, 580.0")  # one card below the floor
    assert not f("6.1, 580.0\n8.6, 580.0")  # mixed with a Volta+ card
    assert not f("7.0, 580.0")
    assert not f("6.1, 470.0")  # driver older than 525
    assert not f("") and not f("[N/A], 580.0")


def test_cu126_arch_list_covers_pascal_and_cu128_does_not():
    dc = _m("core.device_caps")
    cu126 = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "compute_90"]
    assert dc.cuda_build_covers(cu126, 6, 1)
    assert not dc.cuda_build_covers(CU128, 6, 1)


def test_gpu_report_surfaces_the_legacy_cuda_fallback_reason(tmp_path):
    gr = _m("core.gpu_report")
    prefix = tmp_path / ".venv"
    prefix.mkdir()
    assert gr.legacy_cuda_fallback_reason(str(prefix)) is None
    (tmp_path / gr.LEGACY_CUDA_FALLBACK_MARKER).write_text("no kernels for sm_61\n", encoding="utf-8")
    assert gr.legacy_cuda_fallback_reason(str(prefix)) == "no kernels for sm_61"

