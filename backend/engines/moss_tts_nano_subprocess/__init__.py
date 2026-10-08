"""moss-tts-nano-subprocess: MOSS-TTS-Nano from its own venv (one-click install).

The in-process engine needs ``moss_tts_nano`` installed into VoiceStudio's own
environment, with upstream's exact pins (torch 2.7.0, transformers 4.57.1)
landing there too. It also looks for a model class the package no longer
exports: at the commit pinned here, ``moss_tts_nano`` exports only
``__version__``, and the entry point is the top-level
``moss_tts_nano_runtime.NanoTTSService``. The one-click installer clones that
reviewed commit into ``DATA_DIR/engines/moss-tts-nano/`` with its own venv,
and this class runs upstream's runtime there in a sidecar.

The engine id stays ``moss-tts-nano``. ``tts_backend._effective_backend_class``
resolves to this class once that venv exists, and to the in-process
``MossTTSNanoBackend`` otherwise.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from services.subprocess_backend import SubprocessBackend

VENV_ENV_VAR = "OMNIVOICE_MOSS_TTS_NANO_DIR"


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for MOSS-TTS-Nano, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


class MossTTSNanoSubprocessBackend(SubprocessBackend):
    """MOSS-TTS-Nano in a killable sidecar running the engine's own venv."""

    id = "moss-tts-nano"
    display_name = "MOSS-TTS-Nano (20 langs, CPU realtime, 48 kHz)"
    supports_voice_design = False  # strictly reference-cloning
    gpu_compat = ("cuda", "cpu")
    _DEFAULT_SAMPLE_RATE = 48_000

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, (
                "moss_tts_nano package not installed. Install it from "
                "Model Catalogue."
            )
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "MOSS-TTS-Nano's environment is missing. Reinstall it from "
                "Model Catalogue."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # A cold load downloads the model and its audio tokenizer; the sidecar
        # heartbeats progress frames meanwhile, and each re-arms this deadline.
        try:
            v = float(os.environ.get("OMNIVOICE_MOSS_TTS_NANO_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        # The sidecar resamples to this rate if upstream ever returns another.
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        from services.tts_backend import MossTTSNanoBackend

        return MossTTSNanoBackend.supported_languages.fget(self)


__all__ = ["MossTTSNanoSubprocessBackend", "VENV_ENV_VAR", "own_venv_python"]
