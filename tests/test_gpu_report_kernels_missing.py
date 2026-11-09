"""A card the installed PyTorch wheel has no kernels for must be named as such
(#3067).

The loader already falls back to the CPU for a card outside the wheel's
architecture list (an sm_52 Maxwell on torch 2.8+cu128, which ships sm_70+).
What the GPU report did *not* say was the truth about the remedy:

* the host was presented as accelerated, with a vague "may fail at kernel
  launch" caveat and no remedy;
* once #3079 excludes the unusable accelerator from the probe, the host fell
  through to the report's build/vendor mapping, where an NVIDIA card with a
  CUDA build has no ``"cuda"`` key - so it defaulted to
  ``nvidia_cuda_unavailable``, i.e. "CUDA could not start";
* whose remedy is "update the NVIDIA driver", i.e. exactly the thing that
  cannot add the missing kernels.

So the user was told to do the one thing that would not help. The report must
name the real cause (the wheel has no kernels for this card) and offer a
remedy that can fix it (a build that covers the architecture).

Every host shape here is a fake - no torch, no GPU - so this runs on any CI OS.
"""
from __future__ import annotations

import json
import os
import re

import pytest

LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "electron", "src", "renderer", "src", "i18n", "locales",
)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

CUDA_BUILD = {"kind": "cuda", "version": "2.8.0+cu128", "runtime": "12.8"}
CPU_BUILD = {"kind": "cpu", "version": "2.8.0+cpu", "runtime": None}


def _m(name: str):
    """Resolve a module per call, not at import: other suites purge
    ``sys.modules``, and a stale import would patch an object nothing else
    uses (same trap as tests/test_gpu_report_amd_windows.py)."""
    import importlib

    return importlib.import_module(name)


@pytest.fixture
def device_caps():
    return _m("core.device_caps")


@pytest.fixture
def gpu_report():
    return _m("core.gpu_report")


@pytest.fixture
def inventory():
    return _m("core.gpu_inventory")


@pytest.fixture
def m5000m(inventory):
    return inventory.HostGPU(
        vendor="nvidia", name="NVIDIA Quadro M5000M", vram_gb=8.0, discrete=True
    )


@pytest.fixture
def rtx4070(inventory):
    return inventory.HostGPU(
        vendor="nvidia", name="NVIDIA GeForce RTX 4070", vram_gb=12.0, discrete=True
    )


@pytest.fixture(autouse=True)
def _no_forced_cuda(monkeypatch):
    """The classifier reads OMNIVOICE_FORCE_CUDA from the environment, so a
    shell or CI job that exports it would change every verdict here."""
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)


@pytest.fixture
def forced_cuda(_no_forced_cuda, monkeypatch):
    """OMNIVOICE_FORCE_CUDA=1 asks for CUDA anyway; the loader honors it."""
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")


def _mismatch_note(device_caps) -> str:
    """The probe's note for a card the wheel cannot drive.

    The probe is the only producer of KERNEL_RISK_MARKER, so this note *is*
    the architecture mismatch.
    """
    return (
        "NVIDIA Quadro M5000M (sm_52) not in this torch build's archs "
        "(sm_70, sm_75, sm_80, sm_86, sm_90, sm_100, sm_120) "
        f"— {device_caps.KERNEL_RISK_MARKER}"
    )


def _excluded_caps(device_caps, note: str):
    """The probe's verdict once it excludes the accelerator it cannot drive
    (the shape #3079 produces)."""
    return device_caps.HostCaps(
        family="cpu",
        available_families=("cpu",),
        notes=(note,),
        device_name="NVIDIA Quadro M5000M",
        probe_ok=True,
    )


def _advertised_caps(device_caps, note: str):
    """The probe's verdict while it still advertises the accelerator (the
    shape main produces)."""
    return device_caps.HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        notes=(note,),
        device_name="NVIDIA Quadro M5000M",
        probe_ok=True,
    )


def test_a_card_the_wheel_cannot_drive_is_not_reported_as_cuda_unavailable(
    device_caps, gpu_report, m5000m
):
    """Fail-before: the vendor/build mapping answered "nvidia_cuda_unavailable"
    here, whose remedy was a driver update that cannot add missing kernels."""
    note = _mismatch_note(device_caps)
    caps = _excluded_caps(device_caps, note)
    code, params = gpu_report.classify_host(caps, (m5000m,), CUDA_BUILD)
    assert code == "kernels_missing"
    assert params["gpu"] == "NVIDIA Quadro M5000M"
    assert params["torch"] == "2.8.0+cu128"


def test_the_verdict_does_not_depend_on_what_the_probe_advertised(
    device_caps, gpu_report, m5000m
):
    """The loader falls back to the CPU for this card, so the report must say
    so whether or not the probe still advertises the accelerator.

    #3079 excludes the unusable accelerator from the probe, which is the first
    shape; main still advertises it, which is the second. Both must land on
    the same verdict, or the report flips depending on which PR shipped first.
    """
    note = _mismatch_note(device_caps)
    excluded = _excluded_caps(device_caps, note)
    advertised = _advertised_caps(device_caps, note)
    assert gpu_report.classify_host(excluded, (m5000m,), CUDA_BUILD)[0] == "kernels_missing"
    assert gpu_report.classify_host(advertised, (m5000m,), CUDA_BUILD)[0] == "kernels_missing"


def test_a_forced_cuda_host_is_a_caveat_not_an_unusable_host(
    device_caps, gpu_report, m5000m, forced_cuda
):
    """OMNIVOICE_FORCE_CUDA=1 runs the engine on the GPU anyway, so the host
    is usable and the risk is a caveat - the state that used to cover it."""
    caps = _advertised_caps(device_caps, _mismatch_note(device_caps))
    code, params = gpu_report.classify_host(caps, (m5000m,), CUDA_BUILD)
    assert code == "accelerated_caveat"
    assert params["device"] == "NVIDIA Quadro M5000M"


def test_the_remedy_is_a_build_with_kernels_not_a_driver_update(gpu_report):
    assert gpu_report._OPTIONS["kernels_missing"] == ("install_matching_torch",)
    # The driver works on this host; it is the wheel's arch list that is short.
    assert "update_nvidia_driver" not in gpu_report._OPTIONS["kernels_missing"]


def test_the_host_state_counts_as_an_unusable_gpu_for_the_engine_rows(gpu_report):
    # Otherwise the per-engine rows fall through to the generic "no path for
    # your GPU type" and lose the reason.
    assert "kernels_missing" in gpu_report.UNUSABLE_GPU_STATES


def test_the_engine_row_does_not_claim_the_gpu_on_a_host_that_cannot_use_it(gpu_report):
    """The routing verdict can still say "accelerated" for such a host; the
    row must not then tell the user the engine uses the GPU, because the
    loader falls back to the CPU."""
    row = {
        "id": "omnivoice",
        "display_name": "OmniVoice",
        "available": True,
        "gpu_compat": ["cuda", "cpu"],
        "routing_status": "accelerated",
        "effective_device": "cuda",
        "routing_reason": "CUDA selected, but: ...",
    }
    entry = gpu_report._engine_entry(row, "tts", "kernels_missing")
    assert entry["code"] == "host_gpu_unusable"
    assert entry["device"] == "cpu"


def test_an_engine_with_its_own_runtime_keeps_using_the_card(gpu_report):
    """audio.cpp runs its own runtime: the card IS used, whatever PyTorch can
    do with it. ``runtime_backend`` is what tells the two apart - it is None
    for a PyTorch engine and the runtime's name for a native one."""
    row = {
        "id": "audiocpp",
        "display_name": "audio.cpp",
        "available": True,
        "gpu_compat": ["cpu", "vulkan"],
        "routing_status": "accelerated",
        "effective_device": "vulkan",
        "routing_reason": None,
        "runtime_backend": "vulkan",
    }
    entry = gpu_report._engine_entry(row, "tts", "kernels_missing")
    assert entry["code"] == "gpu"
    assert entry["device"] == "vulkan"


def test_a_native_cuda_engine_keeps_using_the_card(gpu_report):
    """A native runtime reports its own device - "cuda" or "rocm" - even
    though it does not go through PyTorch, so the device name alone cannot
    tell the two apart. The engine still uses the card."""
    row = {
        "id": "audiocpp",
        "display_name": "audio.cpp",
        "available": True,
        "gpu_compat": ["cpu", "cuda"],
        "routing_status": "accelerated",
        "effective_device": "cuda",
        "routing_reason": None,
        "runtime_backend": "cuda",
    }
    entry = gpu_report._engine_entry(row, "tts", "kernels_missing")
    assert entry["code"] == "gpu"
    assert entry["device"] == "cuda"


def test_a_pytorch_engine_row_is_marked_as_riding_on_pytorch(gpu_report):
    """The generic routing path reports ``runtime_backend: None``; that is what
    makes the engine's device choice depend on PyTorch."""
    row = {
        "id": "omnivoice",
        "display_name": "OmniVoice",
        "available": True,
        "gpu_compat": ["cuda", "rocm", "mps", "cpu"],
        "routing_status": "accelerated",
        "effective_device": "cuda",
        "routing_reason": None,
        "runtime_backend": None,
    }
    entry = gpu_report._engine_entry(row, "tts", "kernels_missing")
    assert entry["code"] == "host_gpu_unusable"
    assert entry["device"] == "cpu"


def test_an_engine_with_its_own_pytorch_build_keeps_its_routing(gpu_report):
    """A sidecar in its own venv (IndexTTS) imports its own PyTorch build, so
    the parent's missing kernels do not decide where it runs."""
    row = {
        "id": "indextts",
        "display_name": "IndexTTS2",
        "available": True,
        "gpu_compat": ["cuda", "cpu"],
        "routing_status": "accelerated",
        "effective_device": "cuda",
        "routing_reason": None,
        "runtime_backend": None,
        "isolation_mode": "subprocess",
        "own_torch_build": True,
    }
    entry = gpu_report._engine_entry(row, "tts", "kernels_missing")
    assert entry["code"] == "gpu"
    assert entry["device"] == "cuda"


def test_only_sidecars_with_a_separate_interpreter_get_their_own_build(
    gpu_report, monkeypatch,
):
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from services import asr_backend, tts_backend

    def _no_resolve():  # #3104 review: Settings must not resolve/bootstrap venvs
        raise AssertionError("the GPU report called venv_python()")

    own = SimpleNamespace(shares_parent_torch=lambda: False, venv_python=_no_resolve)
    shared = SimpleNamespace(shares_parent_torch=lambda: True, venv_python=_no_resolve)
    broken = SimpleNamespace(venv_python=_no_resolve)  # no cheap answer: separate
    rows = [
        {"id": "indextts", "isolation_mode": "subprocess"},
        {"id": "omnivoice-subprocess", "isolation_mode": "subprocess"},
        {"id": "omnivoice", "isolation_mode": "in-process"},
        {"id": "legacy-sidecar", "isolation_mode": "subprocess"},
    ]
    monkeypatch.setattr(tts_backend, "list_backends", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(
        tts_backend, "_REGISTRY", {"indextts": own, "omnivoice-subprocess": shared, "legacy-sidecar": broken},
    )
    monkeypatch.setattr(asr_backend, "list_backends", lambda: [])
    seen: list[list[dict]] = []
    monkeypatch.setattr(
        gpu_report, "build_gpu_report",
        lambda caps, gpus, build, tts, asr: seen.append(tts) or {"engines": [], "gpus": []},
    )
    gpu_report.collect_gpu_report()
    marks = {r["id"]: r.get("own_torch_build") for r in seen[0]}
    assert marks == {
        "indextts": True, "omnivoice-subprocess": False, "omnivoice": None,
        "legacy-sidecar": True,
    }


@pytest.mark.parametrize(("module", "cls_name", "env_var"), [
    ("engines.omnivoice_subprocess", "OmniVoiceSubprocessBackend", None),
    ("services.subprocess_asr", "IsolatedFasterWhisperBackend", None),
    ("engines.pockettts", "PocketTTSBackend", "OMNIVOICE_POCKETTTS_DIR"),
    ("engines.supertonic3.backend", "Supertonic3Backend", "OMNIVOICE_SUPERTONIC3_DIR"),
])
def test_shares_parent_torch_agrees_with_venv_python(module, cls_name, env_var, monkeypatch):
    """The cheap answer the GPU report reads must match the interpreter the
    sidecar actually launches with."""
    import importlib
    import sys
    from pathlib import Path

    cls = getattr(importlib.import_module(module), cls_name)
    if env_var:
        monkeypatch.delenv(env_var, raising=False)
    assert cls.shares_parent_torch() is (Path(cls.venv_python()) == Path(sys.executable))


def test_a_resolver_backed_sidecar_reports_its_own_build_without_resolving():
    """IndexTTS's venv_python() probes imports or bootstraps a venv; the base
    contract answers without it."""
    import importlib

    cls = importlib.import_module("engines.indextts").IndexTTS2Backend
    assert cls.shares_parent_torch() is False


def test_the_message_names_the_probed_card_not_another_one(device_caps, gpu_report, inventory):
    """On a multi-GPU host the inventory's first card of this vendor is not
    necessarily PyTorch's device 0 - the card the kernel check tested. The
    message must name the card that actually needs a different build."""
    probed = "NVIDIA Quadro M5000M"
    other = inventory.HostGPU(vendor="nvidia", name="NVIDIA GeForce RTX 4090", vram_gb=24.0)
    caps = device_caps.HostCaps(
        family="cpu",
        available_families=("cpu",),
        notes=(_mismatch_note(device_caps),),
        device_name=probed,
        probe_ok=True,
    )
    code, params = gpu_report.classify_host(caps, (other,), CUDA_BUILD)
    assert code == "kernels_missing"
    assert params["gpu"] == probed


def test_a_working_xpu_is_not_reported_as_a_missing_kernel_host(
    device_caps, gpu_report, inventory
):
    """A rejected CUDA card does not matter when another accelerator works:
    the engine runs on the XPU, so the report must not send the user off to
    replace a PyTorch build."""
    caps = device_caps.HostCaps(
        family="xpu",
        available_families=("xpu", "cpu"),
        notes=(_mismatch_note(device_caps),),
        device_name="Intel Arc A770",
        probe_ok=True,
    )
    card = inventory.HostGPU(vendor="intel", name="Intel Arc A770", vram_gb=16.0)
    code, _ = gpu_report.classify_host(caps, (card,), CUDA_BUILD)
    assert code != "kernels_missing"
    assert code == "accelerated_caveat"


def test_a_cpu_pinned_host_is_not_reported_as_a_missing_kernel_host(
    device_caps, gpu_report, m5000m
):
    """The user pinned the compute device to the CPU, so the GPU is not used
    whatever the wheel can do with the card."""
    caps = device_caps.HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        notes=(_mismatch_note(device_caps),),
        device_name="NVIDIA Quadro M5000M",
        probe_ok=True,
        requested_family="cpu",
    )
    code, _ = gpu_report.classify_host(caps, (m5000m,), CUDA_BUILD)
    assert code == "pinned_cpu"


def test_a_plain_gpu_host_is_unaffected(device_caps, gpu_report, rtx4070):
    """Only the kernel mismatch changes the verdict; every other host shape
    keeps the code it had."""
    caps = device_caps.HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        notes=(),
        device_name="NVIDIA GeForce RTX 4070",
        probe_ok=True,
    )
    assert gpu_report.classify_host(caps, (rtx4070,), CUDA_BUILD)[0] == "accelerated"


def test_a_host_with_no_mismatched_kernels_keeps_its_vendor_verdict(
    device_caps, gpu_report, rtx4070
):
    """A CPU-only wheel on the same NVIDIA card is a different problem
    (nvidia_cpu_build) and keeps its own remedy."""
    caps = device_caps.HostCaps(
        family="cpu",
        available_families=("cpu",),
        notes=("torch not importable; treating host as CPU-only",),
        device_name="NVIDIA GeForce RTX 4070",
        probe_ok=True,
    )
    code, params = gpu_report.classify_host(caps, (rtx4070,), CPU_BUILD)
    assert code == "nvidia_cpu_build"
    assert params["gpu"] == "NVIDIA GeForce RTX 4070"


@pytest.mark.parametrize(
    "locale",
    sorted(f[:-5] for f in os.listdir(LOCALES_DIR) if f.endswith(".json")),
)
def test_every_locale_says_what_the_state_means(locale):
    """The user-facing strings exist in every locale and keep the
    placeholders the state text interpolates."""
    with open(os.path.join(LOCALES_DIR, f"{locale}.json"), encoding="utf-8") as fh:
        settings = json.load(fh)["settings"]

    state = settings.get("gpu_report_state_kernels_missing")
    assert state, f"{locale}.json lacks settings.gpu_report_state_kernels_missing"
    assert set(_PLACEHOLDER.findall(state)) == {"gpu", "torch"}

    option = settings.get("gpu_report_option_install_matching_torch")
    assert option, f"{locale}.json lacks settings.gpu_report_option_install_matching_torch"
    # A remedy of its own - not the driver update this bug used to send people to.
    assert option != settings.get("gpu_report_option_update_nvidia_driver")
