"""MOSS-SoundEffect v2.0 sidecar package (issue #3577) — text to sound effects.

OpenMOSS's 1.3B flow-matching DiT (Apache-2.0 code and weights, bilingual
English/Chinese prompts, mono 48 kHz, up to 30 s per clip). It is NOT a speech
engine: it is deliberately absent from the TTS engine registry
(``tts_backend._BACKEND_REGISTRY``), so no voice or engine picker lists it. Only
``POST /api/sfx/generate`` drives it.

It runs in its own subprocess **and its own venv**: upstream pins
``numpy==1.26.4`` and ``transformers==4.57.1`` (``moss_soundeffect_v2/
pyproject.toml``), which cannot share an interpreter with the app. The venv and
the ~11 GB of weights are installed only when the user clicks Install (never by
default) through the shared sidecar installer.

Host support (verified against the upstream pins, not assumed): upstream ships a
CUDA 12.8 build of PyTorch and no MPS or ROCm path, so the one-click install is
offered on NVIDIA hosts only. Apple Silicon has community MLX conversions
(``mlx-community/MOSS-SoundEffect-v2.0-*``); they are not wired here because no
MLX runtime has been reviewed for this app.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path

from services.subprocess_backend import SubprocessBackend

#: Weights are 11.2 GB on disk (sum of the repository file sizes).
SFX_WEIGHTS_BYTES = 11_230_170_282
#: Longest clip upstream supports per call (model_index.json max_inference_seconds).
MAX_SECONDS = 30.0
MIN_SECONDS = 1.0
DEFAULT_SECONDS = 10.0
#: Longest prompt accepted; the text encoder truncates far below this anyway.
MAX_PROMPT_CHARS = 500


def host_support() -> tuple[bool, str]:
    """(ok, reason): can this machine run the engine at all."""
    try:
        from core.device_caps import detect_host_caps

        family = str(detect_host_caps().family)
    except Exception:  # noqa: BLE001 - a failed probe must not break the status page
        family = "cpu"
    if family == "cuda":
        return True, ""
    return False, (
        "Sound effects need an NVIDIA GPU: the model's upstream release ships a "
        "CUDA build of PyTorch only (no Apple Silicon, AMD or CPU path is "
        "verified). Your other engines are unaffected."
    )


class MossSfxBackend(SubprocessBackend):
    """MOSS-SoundEffect v2.0 behind the shared length-prefixed JSON sidecar."""

    id = "moss-soundeffect"
    display_name = "MOSS-SoundEffect v2.0 (sound effects, 48 kHz, Apache-2.0)"
    supports_voice_design = False
    gpu_compat = ("cuda",)
    _DEFAULT_SAMPLE_RATE = 48000

    @classmethod
    def availability(cls) -> tuple[str, str]:
        """(code, reason). ``code`` is stable and machine-readable ("ok" when usable);
        ``reason`` is English for logs and API clients, never shown as-is by the app."""
        ok, reason = host_support()
        if not ok:
            return "host_unsupported", reason
        from engines.moss_sfx.bootstrap import (
            SFX_SIDECAR_SCRIPT,
            is_sfx_installed,
            sfx_weights_present,
            venv_python,
        )

        if not is_sfx_installed():
            if venv_python() is not None and not sfx_weights_present():
                return "weights_missing", (
                    "The sound-effects weights are missing or incomplete. Use "
                    "Repair to download them; generation never downloads."
                )
            return "not_installed", "Sound effects are not installed yet."
        if not SFX_SIDECAR_SCRIPT.exists():
            return "sidecar_missing", "The sound-effects sidecar script is missing; reinstall VoiceStudio."
        return "ok", "ok"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        code, reason = cls.availability()
        return code == "ok", reason

    @classmethod
    def venv_python(cls) -> Path:
        from engines.moss_sfx.bootstrap import venv_python

        py = venv_python()
        if py is None:
            raise RuntimeError(
                "The sound-effects environment is missing. Install it again from "
                "Tools > Sound effects."
            )
        return py

    @classmethod
    def sidecar_python_hint(cls) -> "Path | None":
        from engines.moss_sfx.bootstrap import venv_python

        return venv_python()

    @classmethod
    def sidecar_script(cls) -> Path:
        from engines.moss_sfx.bootstrap import SFX_SIDECAR_SCRIPT

        return SFX_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        # Cold load of ~11 GB plus 100 flow-matching steps; progress frames
        # re-arm this deadline, so it only bounds silence.
        try:
            v = float(os.environ.get("OMNIVOICE_MOSS_SFX_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        return max(30.0, v) if math.isfinite(v) else 900.0

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]


_backend: "MossSfxBackend | None" = None
_backend_lock = threading.Lock()


def get_backend() -> MossSfxBackend:
    """The process-wide sound-effects backend (one sidecar, spawned lazily)."""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = MossSfxBackend()
        return _backend


__all__ = [
    "DEFAULT_SECONDS", "MAX_PROMPT_CHARS", "MAX_SECONDS", "MIN_SECONDS",
    "MossSfxBackend", "SFX_WEIGHTS_BYTES", "get_backend", "host_support",
]
