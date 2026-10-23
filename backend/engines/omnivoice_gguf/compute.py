"""Compute configuration supported by pinned omnivoice.cpp (src/backend.h)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.device_caps import HostCaps
    from services.tts_backend import TTSBackend

BACKEND_ENV = "OMNIVOICE_GGUF_BACKEND"
DEVICE_ENV = "OMNIVOICE_GGUF_DEVICE"


def apply_compute_env(env: dict[str, str]) -> dict[str, str]:
    """Translate app overrides into GGML's native device-name environment."""
    from core.device_caps import requested_device_override

    if requested_device_override() == "cpu":
        env["GGML_BACKEND"] = "CPU"
        return env
    backend = env.get(BACKEND_ENV, "").strip().lower()
    index = env.get(DEVICE_ENV, "").strip()
    if not backend or backend == "auto":
        if index:
            raise ValueError(f"{DEVICE_ENV} requires {BACKEND_ENV}=cuda or vulkan")
        return env
    if backend not in {"cpu", "cuda", "vulkan"}:
        raise ValueError(f"{BACKEND_ENV} must be auto, cpu, cuda, or vulkan")
    if index and (not index.isascii() or not index.isdecimal()):
        raise ValueError(f"{DEVICE_ENV} must be a non-negative device index")
    if backend == "cpu" and index:
        raise ValueError(f"{DEVICE_ENV} is only supported with cuda or vulkan")
    env["GGML_BACKEND"] = (
        "CPU" if backend == "cpu" else
        ("CUDA" if backend == "cuda" else "Vulkan") + str(int(index or "0"))
    )
    return env


def automatic_compute_class() -> str:
    """Use the smallest automatic quant until native adapter capacity is known."""
    # Validate overrides without treating the parent's PyTorch GPU memory as
    # evidence about the native process's selected adapter. Compute stays native.
    apply_compute_env(os.environ.copy())
    return "cpu"


def cpu_selection_explicit() -> bool:
    """True when the compute policy explicitly pins the CPU.

    :func:`apply_compute_env` already folds the Settings choice, the
    global ``OMNIVOICE_DEVICE`` pin, the engine-level
    ``OMNIVOICE_GGUF_BACKEND`` override and an ambient native
    ``GGML_BACKEND`` into one effective device, so the question is
    simply "did that resolve to CPU?".

    Deliberately not :func:`automatic_compute_class`: that reports the
    conservative ``cpu`` *tier* for an unverified native GPU too, which
    is a sizing guess rather than a user decision.
    """
    effective = apply_compute_env(os.environ.copy())
    return effective.get("GGML_BACKEND", "").strip().upper() == "CPU"


def runtime_profile(cls: type[TTSBackend], caps: HostCaps) -> dict:
    """Do not infer a subprocess's devices from the parent's torch or loader.

    The pinned runtime has no capability enumeration API. Auto selection still
    uses native GPU backends, but its unverified route keeps the CPU timeout.
    """
    return {
        "gpu_compat": cls.gpu_compat,
        "min_vram_gb": float(getattr(cls, "min_vram_gb", 0) or 0),
        "effective_device": "cpu",
        "routing_status": "cpu_only",
        "routing_reason": "Native compute device unverified; using the CPU timeout budget",
        "runtime_backend": None,
        "runtime_device_index": None,
        "runtime_device_name": None,
        "runtime_hardware_family": None,
        "runtime_vram_gb": None,
        "runtime_device_verified": False,
    }
