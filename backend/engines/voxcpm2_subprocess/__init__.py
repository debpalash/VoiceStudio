"""voxcpm2-subprocess: VoxCPM2 from its own venv (one-click install).

VoxCPM2 used to run only in-process, which meant installing ``voxcpm``, and a
torch of its choosing, into VoiceStudio's own environment. The one-click
installer now gives it a venv under ``DATA_DIR/engines/voxcpm2/``, and this
class runs the model there in a sidecar, so nothing it installs can touch the
app or another engine.

The engine id stays ``voxcpm2``. ``tts_backend._effective_backend_class``
resolves to this class once that venv exists and to the in-process
``VoxCPM2Backend`` otherwise, so an install made with ``pip install voxcpm``
keeps working as it always has. What the app sees is the same: voice design,
48 kHz output, its own mastering, the same languages. The parent still
prepares the reference clip and trims the silent tail, as the in-process
engine does.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

VENV_ENV_VAR = "OMNIVOICE_VOXCPM2_DIR"


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for VoxCPM2, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


class VoxCPM2SubprocessBackend(SubprocessBackend):
    """VoxCPM2 in a killable sidecar running the engine's own venv."""

    id = "voxcpm2"
    display_name = "VoxCPM2 (30 langs, studio 48 kHz, voice design)"
    supports_voice_design = True
    applies_own_mastering = True  # native 48 kHz studio output — skip apply_mastering()
    gpu_compat = ("cuda", "mps", "cpu")
    _DEFAULT_SAMPLE_RATE = 48_000

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, (
                "voxcpm package not installed. Install it from Model Catalogue."
            )
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "VoxCPM2's environment is missing. Reinstall it from "
                "Model Catalogue."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # A cold load downloads several GB of weights; the sidecar heartbeats
        # progress frames meanwhile, and each one re-arms this deadline.
        try:
            v = float(os.environ.get("OMNIVOICE_VOXCPM2_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        from services.tts_backend import VoxCPM2Backend

        return VoxCPM2Backend.supported_languages.fget(self)

    def generate(self, text: str, **kw) -> "torch.Tensor":
        # The same preparation and finishing as VoxCPM2Backend.generate: the
        # reference clip is trimmed and capped here (the model no longer does
        # it), and the output's long silent tail is cut.
        self._check_language(kw.get("language"))
        from services.audio_dsp import trim_trailing_silence
        from services.tts_backend import _prepare_voxcpm_ref

        if kw.get("ref_audio"):
            kw["ref_audio"] = _prepare_voxcpm_ref(kw["ref_audio"])
        wav = super().generate(text, **kw)
        return trim_trailing_silence(wav, self.sample_rate)


__all__ = ["VENV_ENV_VAR", "VoxCPM2SubprocessBackend", "own_venv_python"]
