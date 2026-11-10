"""MOSS-SoundEffect v2.0 install probe (issue #3577).

The one-click installer (``services.sidecar_install``, engine id
``moss-soundeffect``) owns the venv and the weights; this module only answers
"where are they" without importing torch or spawning anything, so a status
poll stays cheap.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

#: Absolute path to the sidecar entrypoint.
SFX_SIDECAR_SCRIPT: Path = Path(__file__).resolve().parent / "main.py"

#: Install directory variable the installer persists (see SPECS).
SFX_ENV_VAR = "OMNIVOICE_MOSS_SFX_DIR"

#: Weights directory inside the install (SidecarSpec.weights_subdir).
SFX_WEIGHTS_SUBDIR = "checkpoints"


def venv_python() -> Optional[Path]:
    """The interpreter of the installer-made venv, or None when not installed."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(SFX_ENV_VAR)


def sfx_weights_present() -> bool:
    """The pinned checkpoints are complete on disk (local check, no network)."""
    from services.sidecar_install import SPECS, _weights_present

    return _weights_present(SPECS["moss-soundeffect"])


def is_sfx_installed() -> bool:
    """Cheap file probe: venv interpreter, install marker and the pinned weights.

    Missing weights count as not installed so the page offers Install/Repair
    (the installer resumes the weights download) instead of a dead Generate.
    """
    return venv_python() is not None and sfx_weights_present()


__all__ = [
    "SFX_ENV_VAR",
    "SFX_SIDECAR_SCRIPT",
    "SFX_WEIGHTS_SUBDIR",
    "is_sfx_installed",
    "sfx_weights_present",
    "venv_python",
]
