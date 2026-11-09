"""Compute configuration supported by pinned omnivoice.cpp (src/backend.h).

The native runtime picks its own GPU, independently of the parent's PyTorch.
For a Vulkan build that choice is now *verified* rather than guessed:
``vulkan_auto`` enumerates the adapters the pinned GGML lists and identifies
the one it opens, so the quant policy and the Compute-device report size
against the real adapter. Every other route (CUDA builds, Metal, a binary
without the Vulkan module, no working Vulkan driver) keeps the conservative
unverified policy.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.device_caps import HostCaps
    from services.tts_backend import TTSBackend

    from .vulkan_auto import VulkanAdapter

BACKEND_ENV = "OMNIVOICE_GGUF_BACKEND"
DEVICE_ENV = "OMNIVOICE_GGUF_DEVICE"


class ComputeConfigError(RuntimeError, ValueError):
    """An invalid GGUF device override.

    A ``RuntimeError`` so generation, the load probe and default-engine
    selection report it the way they report every other engine failure,
    and still a ``ValueError`` for callers that validate input.
    """

    def __init__(self, problem: str):
        """Append how to fix the backend / device variables to ``problem``."""
        super().__init__(
            f"{problem}. Fix or clear {BACKEND_ENV} / {DEVICE_ENV} "
            "(for example OMNIVOICE_GGUF_BACKEND=vulkan OMNIVOICE_GGUF_DEVICE=0) "
            "and restart the backend."
        )


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
            raise ComputeConfigError(f"{DEVICE_ENV} requires {BACKEND_ENV}=cuda or vulkan")
        return env
    if backend not in {"cpu", "cuda", "vulkan"}:
        raise ComputeConfigError(f"{BACKEND_ENV} must be auto, cpu, cuda, or vulkan")
    if index and (not index.isascii() or not index.isdecimal()):
        raise ComputeConfigError(f"{DEVICE_ENV} must be a non-negative device index")
    if backend == "cpu" and index:
        raise ComputeConfigError(f"{DEVICE_ENV} is only supported with cuda or vulkan")
    env["GGML_BACKEND"] = (
        "CPU" if backend == "cpu" else
        ("CUDA" if backend == "cuda" else "Vulkan") + str(int(index or "0"))
    )
    return env


def binary_has_vulkan_module(bin_dir: Optional[Path] = None) -> bool:
    """Whether the bundled runtime ships GGML's Vulkan module.

    Vulkan builds use ``GGML_BACKEND_DL`` (``scripts/lib/omnivoice-vulkan.sh``),
    so the GPU backend is a ``ggml-vulkan`` shared library beside the binary;
    its integrity is checked with the rest of the runtime (``bin/checksums``).
    """
    try:
        if bin_dir is None:
            from .backend import _binary_path

            bin_dir = _binary_path().parent
        return any(
            "ggml-vulkan" in p.name.lower() and p.is_file() and p.stat().st_size > 0
            for p in bin_dir.iterdir()
        )
    except OSError:
        return False


def selected_vulkan_adapter() -> Optional["VulkanAdapter"]:
    """The verified Vulkan adapter the native runtime will open, or None.

    None whenever the route is not a verified Vulkan one: an explicit CPU
    choice, an explicit CUDA backend (or an ambient non-Vulkan
    ``GGML_BACKEND``), a binary without the Vulkan module, or no working
    Vulkan driver. With ``OMNIVOICE_GGUF_BACKEND=vulkan`` the requested
    ``Vulkan<N>`` is used; otherwise GGML's own automatic pick (first
    discrete GPU, else first integrated).
    """
    effective = apply_compute_env(os.environ.copy())
    ggml = effective.get("GGML_BACKEND", "").strip()
    index: Optional[int] = None
    if ggml:
        if not ggml.lower().startswith("vulkan") or not ggml[6:].isdecimal():
            return None
        index = int(ggml[6:])
    if not binary_has_vulkan_module():
        return None
    from .vulkan_auto import detect_vulkan_profile

    profile = detect_vulkan_profile()
    return profile.best() if index is None else profile.by_index(index)


def automatic_compute_class() -> str:
    """The quant tier for the automatic policy.

    A verified Vulkan adapter is sized on its effective memory (shared RAM for
    an APU); anything unverified keeps the smallest tier. BF16 (the high tier)
    needs native bfloat16 shaders: without them ggml-vulkan emulates it and
    runs ~1.8x slower than a quantized model (measured on an RX 6700 XT), so an
    adapter without confirmed support stops at Q8_0. Overrides are still
    validated first, and the parent's PyTorch GPU memory is never used as
    evidence about the native process's adapter.
    """
    adapter = selected_vulkan_adapter()
    if adapter is None:
        return "cpu"
    from .hardware_probe import _bucket

    tier = _bucket(adapter.effective_vram_mb)
    if tier == "high-vram" and adapter.bf16 is not True:
        return "mid-vram"
    return tier


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
    """Report the native device without inferring it from the parent's torch.

    A verified Vulkan adapter is reported as accelerated (so Compute device
    counts the engine as a GPU engine and generation gets the GPU budget).
    Otherwise the pinned runtime cannot say which device it opened, and the
    unverified route keeps the CPU timeout.
    """
    base = {
        "gpu_compat": cls.gpu_compat,
        "min_vram_gb": float(getattr(cls, "min_vram_gb", 0) or 0),
    }
    try:
        adapter = selected_vulkan_adapter()
    except ComputeConfigError:
        adapter = None
    if adapter is not None and _device_proven():
        return {
            **base,
            "effective_device": "vulkan",
            "routing_status": "accelerated",
            "routing_reason": None,
            "runtime_backend": "vulkan",
            "runtime_device_index": adapter.index,
            "runtime_device_name": adapter.name,
            "runtime_hardware_family": adapter.vendor,
            "runtime_vram_gb": adapter.effective_vram_gb,
            "runtime_device_verified": True,
        }
    return {
        **base,
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


def _device_proven() -> bool:
    """Whether a Vulkan driver actually enumerated the adapter.

    The OS-inventory fallback (no ``vulkaninfo``) is good enough to size the
    quant, but it only proves a GPU and a Vulkan loader exist, not that the
    driver can open the GPU. Such an adapter gets neither the GPU timeout
    budget nor the GPU offer: a CPU fallback must keep the CPU budget.
    """
    from .vulkan_auto import detect_vulkan_profile

    return detect_vulkan_profile().source == "vulkaninfo"


def _promotion_evidence() -> bool:
    """Host facts behind the offer: CPU-only PyTorch, a verified adapter, a usable binary."""
    from core.device_caps import detect_host_caps

    if detect_host_caps().family != "cpu":
        return False  # PyTorch has an accelerator: the in-process engine uses it
    try:
        if selected_vulkan_adapter() is None or not _device_proven():
            return False
    except ComputeConfigError:
        return False  # an invalid backend/device pin is reported by generate
    from .backend import _make_backend_class

    ok, _reason = _make_backend_class()._binary_status()
    return ok


def _licence_accepted() -> bool:
    """Whether the GGUF model licence is accepted. Uncached: it can change at any time."""
    from .backend import _make_backend_class
    from services.tts_backend import ensure_engine_licence

    try:
        ensure_engine_licence(_make_backend_class())
    except Exception:  # noqa: BLE001
        return False
    return True


_PROMOTION_CACHE: dict[tuple, bool] = {}


def _promotion_for(device_choice: str, backend: str, device: str, ggml: str, binary: tuple) -> bool:
    """Eligibility for one combination of compute choice, backend/device env
    and runtime binary.

    Only a positive verdict is cached. A refusal can come from a damaged
    runtime library that a repair fixes without touching the executable, so it
    is re-checked on the next call; the binary hash runs only on hosts that
    already have a proven Vulkan adapter.
    """
    key = (device_choice, backend, device, ggml, binary)
    if _PROMOTION_CACHE.get(key):
        return True
    try:
        ok = bool(_promotion_evidence())
    except Exception:  # noqa: BLE001 - a probe bug must never break engine selection
        ok = False
    if ok:
        if len(_PROMOTION_CACHE) >= 8:
            _PROMOTION_CACHE.clear()
        _PROMOTION_CACHE[key] = True
    return ok


_promotion_for.cache_clear = _PROMOTION_CACHE.clear  # type: ignore[attr-defined]


def vulkan_eligible() -> bool:
    """Whether the GPU offer applies: Compute device is Auto, PyTorch has no
    GPU, the bundled runtime ships the Vulkan module and passes its integrity
    checks, and a verified Vulkan adapter is present.

    This only qualifies the host for the one-time offer
    (:mod:`.gpu_offer`); the engine changes only when the user accepts it.
    """
    from core.device_caps import requested_device_override

    choice = requested_device_override()
    if choice != "auto":
        return False
    return _promotion_for(
        choice,
        os.environ.get(BACKEND_ENV, ""),
        os.environ.get(DEVICE_ENV, ""),
        os.environ.get("GGML_BACKEND", ""),
        _binary_identity(),
    )


def _binary_identity() -> tuple:
    """Path, size and mtime of the runtime binary, so a repaired or replaced
    runtime is re-evaluated instead of serving a cached refusal."""
    try:
        from .backend import _binary_path, _file_identity

        path = _binary_path()
        return (str(path), _file_identity(path))
    except Exception:  # noqa: BLE001 - an unresolvable binary is just another key
        return ()
