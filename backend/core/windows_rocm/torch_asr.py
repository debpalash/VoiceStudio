"""Advertise only the matched opt-in Windows HIP Torch recipe, independent of CT2."""
import os
import sys
from importlib import metadata


def gpu_compatible(default):
    """Add ROCm only for an available host matching the selected native recipe."""
    if sys.platform == "win32":
        default = tuple(device for device in default if device != "rocm")
    if (sys.platform != "win32"
            or os.environ.get("OMNIVOICE_TORCH_VARIANT", "").strip().lower() != "rocm"):
        return default
    from core.device_caps import detect_host_caps, KERNEL_RISK_MARKER
    from .recipe import load_recipe, validate_target

    caps = detect_host_caps()
    if (caps.family != "rocm" or not getattr(caps, "probe_ok", False)
            or any(KERNEL_RISK_MARKER in note for note in caps.notes)):
        return default
    try:
        validate_target()
        recipe = load_recipe()
        pins = recipe["torch"]["packages"] + recipe["runtime"]["packages"]
        if any(metadata.version(name) != version for name, version in (pin.split("==", 1) for pin in pins)):
            return default
    except Exception:  # noqa: BLE001 - uncertain metadata cannot authorize a native GPU claim.
        return default
    return tuple(dict.fromkeys((default[0], "rocm", *default[1:])))
