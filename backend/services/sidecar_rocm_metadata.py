"""Cheap, conservative sidecar Torch metadata for ROCm routing."""
import os
from pathlib import Path
import sys


def sidecar_torch_lacks_hip(engine):
    """Return True for non-HIP Torch; None when GPU execution is unverified."""
    if engine.shares_parent_torch():
        return False
    windows_opt_in = (sys.platform == "win32" and
                      os.environ.get("OMNIVOICE_TORCH_VARIANT", "").strip().lower() == "rocm")
    try:
        python = engine.sidecar_python_hint()
        if python is None:
            return True if windows_opt_in else None
        from services.sidecar_install import venv_torch_hip
        hip = venv_torch_hip(Path(python).parent.parent)
    except Exception:  # noqa: BLE001 -- metadata is advisory
        return True if windows_opt_in else None
    if hip is None:
        return True if windows_opt_in else None
    # A HIP wheel does not prove this GPU has usable kernels in that wheel.
    return None if hip else True
