"""GPU acceleration report — "which of my engines will use the GPU, and if not, why?"

Joins three independent facts that the rest of the backend keeps apart:

* the **hardware** the OS sees (``gpu_inventory`` — no torch involved),
* the **PyTorch build** that is installed (CUDA / ROCm / CPU wheel), and
* every engine's **routing verdict** (``engine_routing`` via ``list_backends``).

The reason a Radeon owner on Windows sees the CPU doing the work is the join of
the first two: the installer ships an NVIDIA-CUDA PyTorch wheel, which cannot
address an AMD card, so every PyTorch engine lands on the CPU. No single source
reported that; this module does.

Output is **codes + parameters**, never prose. The renderer maps each code to an
``i18n`` string (``settings.gpu_report_*``); ``reason`` carries the backend's own
English text only as a fallback / for pasted diagnostics. ``build_gpu_report`` is
pure (inputs in, dict out) so it is testable without a GPU; ``collect_gpu_report``
is the thin impure wrapper.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from core.device_caps import ACCELERATOR_PRIORITY, KERNEL_RISK_MARKER, HostCaps, cuda_forced
from core.gpu_inventory import HostGPU, detect_host_gpus, discrete_candidates, pick_for_build

#: Device families PyTorch can accelerate on. An engine whose targets include
#: anything else (Vulkan, a DirectML string) picks its device through its own
#: runtime, not through PyTorch.
_PYTORCH_FAMILIES = frozenset(ACCELERATOR_PRIORITY)


def _cuda_forced() -> bool:
    """Same flag the loader and routing badge read (``device_caps.cuda_forced``)."""
    return cuda_forced()


# Host states in which a physical GPU exists that PyTorch cannot drive.
UNUSABLE_GPU_STATES = frozenset({
    "amd_cuda_build",
    "amd_cpu_build",
    "amd_rocm_no_device",
    "nvidia_cpu_build",
    "nvidia_cuda_unavailable",
    "nvidia_rocm_build",
    "intel_unsupported",
    "kernels_missing",
})

#: What the user can honestly do about each host state, most useful first. Each
#: is an i18n suffix (``settings.gpu_report_option_<code>``).
_OPTIONS: dict[str, tuple[str, ...]] = {
    # Windows: only these two exist. DirectML is not offered: torch-directml
    # needs torch 2.4.x, which the engines here no longer support.
    "amd_cuda_build:win32": ("vulkan_engines", "rocm_windows_manual"),
    "amd_cpu_build:win32": ("vulkan_engines", "rocm_windows_manual"),
    "amd_rocm_no_device:win32": ("rocm_windows_manual",),
    "amd_cuda_build:linux": ("rocm_variant_linux", "vulkan_engines"),
    "amd_cpu_build:linux": ("rocm_variant_linux", "vulkan_engines"),
    "amd_rocm_no_device:linux": ("rocm_device_access",),
    "nvidia_cpu_build": ("reinstall_cuda_torch",),
    "nvidia_cuda_unavailable": ("update_nvidia_driver",),
    # The wheel is right, the card's architecture is not in it: a driver
    # update cannot add the missing kernels, so it is not offered (#3067).
    "kernels_missing": ("install_matching_torch",),
}


def torch_build() -> dict[str, Any]:
    """``{"kind": "cuda"|"rocm"|"cpu"|"unknown", "version": ...}`` — metadata only."""
    try:
        import torch

        hip = getattr(torch.version, "hip", None)
        cuda = getattr(torch.version, "cuda", None)
        kind = "rocm" if hip else ("cuda" if cuda else "cpu")
        return {
            "kind": kind,
            "version": str(getattr(torch, "__version__", "") or "") or None,
            "runtime": hip or cuda or None,
        }
    except Exception:  # noqa: BLE001 - diagnostics never raise
        return {"kind": "unknown", "version": None, "runtime": None}


def classify_host(
    caps: HostCaps,
    gpus: tuple[HostGPU, ...],
    build: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """``(state_code, params)`` — why this host is, or is not, GPU-accelerated."""
    if not caps.probe_ok:
        return "probe_failed", {}
    if caps.requested_family == "cpu" and len(caps.available_families) > 1:
        # The user pinned the compute device to the CPU, so the GPU is not
        # used at all - whatever the wheel can or cannot do with the card.
        return "pinned_cpu", {}

    kind = build.get("kind")
    # Diagnose the card this build was meant to drive: on a Radeon + GeForce
    # host a CUDA build that finds nothing is an NVIDIA problem, not an AMD one.
    card = pick_for_build(gpus, str(kind))
    params = {"build": kind, "torch": build.get("version")}

    # The installed wheel has no kernels for this card's compute architecture
    # (an sm_52 Maxwell on torch 2.8+cu128, which ships sm_70+, #3067). The
    # loader already falls back to the CPU for such a card, so the report must
    # not tell the user the GPU is in use — whatever the probe advertised. The
    # driver works and the wheel does not, so the remedy is a build that
    # covers the architecture; a driver update cannot add the missing kernels.
    #
    # Only when the rejected card is what the engine would have used: with a
    # working XPU or MPS alongside it, the engine runs there and the missing
    # CUDA kernels change nothing.
    if (
        any(KERNEL_RISK_MARKER in n for n in caps.notes)
        and not _cuda_forced()
        and caps.family in ("cuda", "rocm", "cpu")
    ):
        return "kernels_missing", {
            # The probed device is the one the kernel check tested. The
            # inventory only knows the cards the OS lists, and on a multi-GPU
            # host its first card of this vendor is not necessarily device 0.
            "gpu": caps.device_name or (card.name if card is not None else ""),
            **params,
        }

    if caps.family != "cpu":
        if any(KERNEL_RISK_MARKER in n for n in caps.notes):
            # The user forced CUDA (OMNIVOICE_FORCE_CUDA), so the engine runs
            # on the GPU and may fail at kernel launch: a caveat, not a host
            # that cannot use its card.
            return "accelerated_caveat", {"family": caps.family, "device": caps.device_name}
        return "accelerated", {"family": caps.family, "device": caps.device_name}

    if card is not None and card.vendor == "amd":
        code = {
            "cuda": "amd_cuda_build",
            "rocm": "amd_rocm_no_device",
        }.get(str(kind), "amd_cpu_build")
        return code, {"gpu": card.name, **params}
    if card is not None and card.vendor == "nvidia":
        code = {
            "cpu": "nvidia_cpu_build",
            "unknown": "nvidia_cpu_build",
            "rocm": "nvidia_rocm_build",
        }.get(str(kind), "nvidia_cuda_unavailable")
        return code, {"gpu": card.name, **params}
    if card is not None:
        return "intel_unsupported", {"gpu": card.name}
    return "no_gpu", {}


def _engine_entry(row: dict, kind: str, host_code: str) -> dict[str, Any]:
    compat = tuple(row.get("gpu_compat") or ())
    status = row.get("routing_status")
    device = row.get("effective_device") or "cpu"
    params: dict[str, Any] = {}
    if not row.get("available"):
        # Missing a dependency, binary or model: it cannot run at all, so no
        # verdict (and never a "uses the GPU") - whatever its static routing
        # says. The row still tells the picker it needs installing.
        code, params = "not_installed", {"needs": ", ".join(compat)}
    elif status == "accelerated":
        # An engine accelerates through PyTorch when every compute target it
        # declares is a PyTorch family and it names no runtime of its own.
        # audio.cpp is the counter-example: it runs its own Vulkan, CUDA or
        # ROCm build and reports that device, so it does use the card even
        # when PyTorch cannot drive it.
        targets = {t for t in compat if t != "cpu"}
        rides_on_pytorch = bool(targets) and targets <= _PYTORCH_FAMILIES
        if (
            host_code in UNUSABLE_GPU_STATES
            and rides_on_pytorch
            and not row.get("runtime_backend")
            # A sidecar in its own venv (IndexTTS) has its own PyTorch build,
            # so the parent's missing kernels say nothing about its device.
            and not row.get("own_torch_build")
        ):
            # A host whose card PyTorch cannot drive wins over the routing
            # verdict: the engine runs on the CPU whatever the static routing
            # says (#3067).
            device = "cpu"
            if host_code.startswith("amd_"):
                code = "host_gpu_unusable_rocm" if "rocm" in compat else "host_gpu_unusable_no_amd_path"
            else:
                code = "host_gpu_unusable"
        else:
            code = "gpu_caveat" if row.get("routing_reason") else "gpu"
            params = {"device": device}
    elif status == "unknown":
        code = "unknown"
    elif status == "unavailable":
        code, params = "needs_accelerator", {"needs": ", ".join(compat)}
    elif compat == ("cpu",):
        code = "cpu_by_design"
    elif host_code in UNUSABLE_GPU_STATES:
        if host_code.startswith("amd_"):
            code = "host_gpu_unusable_rocm" if "rocm" in compat else "host_gpu_unusable_no_amd_path"
        else:
            code = "host_gpu_unusable"
    elif host_code == "pinned_cpu":
        code = "pinned_cpu"
    elif status == "cpu_fallback":
        code = "no_family_path"
    else:
        code = "no_gpu"
    return {
        "id": row.get("id"),
        "name": row.get("display_name") or row.get("id"),
        "kind": kind,
        "available": bool(row.get("available")),
        "gpu_compat": list(compat),
        "device": device,
        "status": status,
        "code": code,
        "params": params,
        "reason": row.get("routing_reason"),
    }


def build_gpu_report(
    caps: HostCaps,
    gpus: tuple[HostGPU, ...],
    build: dict[str, Any],
    tts_rows: list[dict],
    asr_rows: list[dict],
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    """Assemble the report. Pure — every input is explicit."""
    plat = platform or sys.platform
    host_code, host_params = classify_host(caps, gpus, build)
    plat_key = "win32" if plat == "win32" else ("linux" if plat.startswith("linux") else plat)
    options = (
        _OPTIONS.get(f"{host_code}:{plat_key}")
        or _OPTIONS.get(host_code)
        or ()
    )
    engines = [
        *(_engine_entry(r, "tts", host_code) for r in tts_rows),
        *(_engine_entry(r, "asr", host_code) for r in asr_rows),
    ]
    return {
        "platform": plat_key,
        "family": caps.family,
        "device_name": caps.device_name,
        "vram_gb": caps.vram_gb,
        "gpus": [
            {"vendor": g.vendor, "name": g.name, "vram_gb": g.vram_gb}
            for g in gpus
        ],
        "torch": build,
        "state": host_code,
        "params": host_params,
        "options": list(options),
        "engines": engines,
    }


def collect_gpu_report() -> dict[str, Any]:
    """Gather live inputs and build the report. Never raises."""
    from core.device_caps import detect_host_caps
    from core.scrub import scrub_text

    def rows(loader) -> list[dict]:
        try:
            return list(loader())
        except Exception:  # noqa: BLE001 - one broken registry must not blank the report
            return []

    def own_torch_build(rows_: list[dict], registry) -> list[dict]:
        """Mark subprocess engines whose interpreter is not this process's.

        Such an engine imports its own PyTorch build, so the host verdict
        (which describes the parent's build) must not override its routing.
        Asks the cheap ``shares_parent_torch()`` and never ``venv_python()``,
        which for some engines (IndexTTS) probes imports or bootstraps a venv:
        opening Settings must not start an engine setup.
        """
        for row in rows_:
            if row.get("isolation_mode") != "subprocess":
                continue
            try:
                shares = bool(registry[row["id"]].shares_parent_torch())
            except Exception:  # noqa: BLE001 - unknown interpreter: assume separate
                shares = False
            row["own_torch_build"] = not shares
        return rows_

    def tts():
        from services.tts_backend import _REGISTRY, list_backends

        return own_torch_build(list(list_backends()), _REGISTRY)

    def asr():
        from services.asr_backend import _REGISTRY, list_backends

        return own_torch_build(list(list_backends()), _REGISTRY)

    report = build_gpu_report(
        detect_host_caps(), detect_host_gpus(), torch_build(), rows(tts), rows(asr),
    )
    for e in report["engines"]:
        if e["reason"]:
            e["reason"] = scrub_text(e["reason"])
    for g in report["gpus"]:
        g["name"] = scrub_text(g["name"])
    return report


__all__ = [
    "UNUSABLE_GPU_STATES", "build_gpu_report", "classify_host",
    "collect_gpu_report", "torch_build",
]
