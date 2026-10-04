"""cosyvoice-subprocess: CosyVoice 3 from its own venv (one-click install).

The in-process engine needs CosyVoice importable from VoiceStudio's own
interpreter, which upstream's setup (its own Python 3.10 environment, pins
that conflict with the app's) never provides. The one-click installer clones a
reviewed CosyVoice commit and the Matcha-TTS code it vendors as a submodule
into ``DATA_DIR/engines/cosyvoice/``, builds a Python 3.10 venv from a trimmed
requirements list (``requirements.txt`` beside this file), downloads the
CosyVoice 3 weights, and this class runs the model there in a sidecar.

The engine id stays ``cosyvoice``. ``tts_backend._effective_backend_class``
resolves to this class once that venv exists, and to the in-process
``CosyVoiceBackend`` otherwise, so an existing source installation keeps
working as it did.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from services.subprocess_backend import SubprocessBackend

VENV_ENV_VAR = "OMNIVOICE_COSYVOICE_DIR"
#: Where the installer puts the CosyVoice 3 weights, inside the checkout.
MANAGED_MODEL_SUBDIR = "pretrained_models/Fun-CosyVoice3-0.5B"


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for CosyVoice, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


class CosyVoiceSubprocessBackend(SubprocessBackend):
    """CosyVoice in a killable sidecar running the engine's own venv."""

    id = "cosyvoice"
    display_name = "CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)"
    gpu_compat = ("cuda", "cpu")
    _DEFAULT_SAMPLE_RATE = 24_000

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        profile = super().runtime_compute_profile(caps)
        if sys.platform != 'win32' or caps.family != 'rocm':
            return profile
        from services.sidecar_install import cosyvoice_rocm_verified

        if not cosyvoice_rocm_verified():
            return profile
        from services.engine_routing import resolve_routing

        gpu_compat = ('cuda', 'rocm', 'cpu')
        return {
            **profile,
            'gpu_compat': gpu_compat,
            **resolve_routing(gpu_compat, caps, cls.min_vram_gb),
        }

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, (
                "cosyvoice package not installed. Install it from "
                "Model Catalogue → Engines."
            )
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "CosyVoice's environment is missing. Reinstall it from "
                "Model Catalogue → Engines."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # Loading the model and its text normalizers takes a while on a cold
        # start; the sidecar heartbeats progress frames meanwhile.
        try:
            v = float(os.environ.get("OMNIVOICE_COSYVOICE_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        # The sidecar resamples to this rate if a model ever reports another.
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        from services.tts_backend import CosyVoiceBackend

        return CosyVoiceBackend.supported_languages.fget(self)

    def model_identity(self) -> str:
        # v1/v2/v3 share the one "cosyvoice" id; the folder name tells them
        # apart, as it does for the in-process engine.
        model_dir = os.environ.get("OMNIVOICE_COSYVOICE_MODEL") or MANAGED_MODEL_SUBDIR
        return os.path.basename(os.path.normpath(model_dir))


__all__ = [
    "CosyVoiceSubprocessBackend",
    "MANAGED_MODEL_SUBDIR",
    "VENV_ENV_VAR",
    "own_venv_python",
]
