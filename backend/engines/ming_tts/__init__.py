"""Ming-3.0-tts sidecar package (issue #3793).

Ming-3.0-tts-0.5b (inclusionAI / Ant Group, MIT code and weights) is a 0.5B
Qwen2.5-backbone zero-shot voice-cloning TTS: Chinese, English, Japanese,
Korean, French, German and Spanish, 44.1 kHz output.

It runs in its own subprocess **and its own venv**: upstream pins
``transformers==4.46.3`` and ``torch==2.6.0`` while OmniVoice pins
``transformers>=5.3``, so the two cannot share one interpreter.

Cross-platform honesty (the strict default-parity rule): upstream documents and
tests **NVIDIA CUDA only** (H800 / H20, torch 2.6 + CUDA 12.4) and loads the
model in bf16 straight onto ``cuda:0``. There is no CPU or MPS path in its
reference code that anyone has verified, so:

  * The engine is **opt-in** (clone + env var, or the one-click install), never
    a default, so it cannot become a broken default on any platform.
  * ``is_available()`` returns ``False`` with a clear reason on a host without
    an NVIDIA GPU (macOS, CPU-only, AMD). ``gpu_compat = ("cuda",)`` — no ROCm,
    MPS or CPU claim.
  * The sidecar re-checks ``torch.cuda.is_available()`` and fails with the same
    message rather than silently running somewhere untested.

Three public entry points: ``MingTTSBackend`` (this module), ``main.py``
(sidecar, runs under Ming's own venv — never imported by the parent), and
``bootstrap.py`` (venv probe + lazy bootstrap).
"""
from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING

from pathlib import Path

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.ming_tts")


def _host_has_cuda() -> bool:
    """True when this machine has an NVIDIA GPU the app can see. A probe that
    fails counts as "no": offering an engine that can only fail is worse."""
    try:
        from core.device_caps import detect_host_caps
        return str(detect_host_caps().family) == "cuda"
    except Exception:  # noqa: BLE001
        return False


def _apply_speed(audio: "torch.Tensor", sample_rate: int, rate: float) -> "torch.Tensor":
    """Stretch a (1, n) take by ``rate`` (>1 faster) with ffmpeg ``atempo``."""
    import subprocess

    import numpy as np
    import torch

    from services.ffmpeg_utils import _atempo_chain, find_ffmpeg, local_inputs_only
    from services.tts_backend import TTSInputError

    if not math.isfinite(rate) or rate <= 0:
        raise TTSInputError(f"speed must be a positive number, got {rate!r}.")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("Ming speed control needs ffmpeg, which could not be found.")
    pcm = audio.detach().cpu().to(torch.float32).numpy().reshape(-1).astype(np.float32, copy=False)
    proc = subprocess.run(
        local_inputs_only(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "f32le", "-ar", str(sample_rate),
             "-ac", "1", "-i", "pipe:0", "-af", _atempo_chain(rate), "-f", "f32le", "-ar", str(sample_rate),
             "-ac", "1", "pipe:1"],
            tool="ffmpeg",
        ),
        input=pcm.tobytes(), capture_output=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Ming speed adjustment failed: " + proc.stderr.decode(errors="replace")[:200])
    return torch.from_numpy(np.frombuffer(proc.stdout, dtype=np.float32).copy()).reshape(1, -1)


class MingTTSBackend(SubprocessBackend):
    """Ming-3.0-tts-0.5b — 7 langs, zero-shot clone, NVIDIA CUDA, MIT.

    Runs in a long-lived sidecar over length-prefixed JSON-over-stdio in a
    dedicated venv. First synthesize cold-loads the ~3 GB checkpoint (bf16, from the local
    cache only);
    subsequent calls reuse the process.

    Installation (the Model Catalogue one-click install does this on Linux)::

        git clone https://github.com/inclusionAI/Ming-3.0-tts.git
        cd Ming-3.0-tts
        uv venv --python 3.10 && uv pip install -r requirements.txt

    Upstream ships no packaging metadata, so the sidecar puts the clone on
    ``sys.path``. Set ``OMNIVOICE_MING_TTS_DIR`` to the clone root and restart.
    ``OMNIVOICE_MING_TTS_MODEL`` may point at a local checkpoint directory.
    """

    id = "ming-tts"
    display_name = (
        "Ming-3.0-tts (0.5B, 7 langs, zero-shot clone, NVIDIA CUDA, 44.1 kHz, MIT)"
    )
    supports_voice_design = False  # timbre comes from a reference clip
    # The sidecar honours cfg only; there is no flow-step control upstream.
    supports_num_step = False
    # CUDA only: upstream is tested on NVIDIA GPUs and nothing else.
    gpu_compat = ("cuda",)
    # Checkpoint audio_tokenizer_config.sample_rate; re-read from the sidecar.
    _DEFAULT_SAMPLE_RATE = 44100

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Verify the venv on disk only — never import the engine here (its
        # transformers pin cannot coexist with the parent's).
        if not _host_has_cuda():
            return False, (
                "Ming-3.0-tts needs an NVIDIA CUDA GPU: upstream is tested on "
                "CUDA only (no CPU, Apple Silicon or AMD path). Pick another "
                "engine on this machine. See docs/engines/ming-tts.md."
            )
        from engines.ming_tts.bootstrap import (
            MING_TTS_SIDECAR_SCRIPT,
            is_ming_tts_installed,
            ming_weights_installed,
        )
        if not is_ming_tts_installed():
            return False, (
                "Ming-3.0-tts venv not found. Install it from Model Catalogue, "
                "or set OMNIVOICE_MING_TTS_DIR to your Ming-3.0-tts clone (the "
                "directory containing requirements.txt) and restart "
                "VoiceStudio. See docs/engines/ming-tts.md."
            )
        if not MING_TTS_SIDECAR_SCRIPT.exists():
            return False, (
                "Ming-3.0-tts sidecar script missing at "
                f"{MING_TTS_SIDECAR_SCRIPT} — reinstall VoiceStudio."
            )
        if not ming_weights_installed():
            return False, (
                "Ming-3.0-tts weights not installed. Download them from Model "
                "Catalogue (Ming-3.0-tts weights row); generation never "
                "downloads. See docs/engines/ming-tts.md."
            )
        return True, "ok (NVIDIA CUDA)"

    @classmethod
    def venv_python(cls):
        from engines.ming_tts.bootstrap import resolve_ming_tts_venv
        return resolve_ming_tts_venv()

    @classmethod
    def sidecar_python_hint(cls) -> "Path | None":
        """Expose only the bootstrap-selected interpreter for routing metadata."""
        from services.subprocess_backend import bootstrap_python_hint

        from engines.ming_tts import bootstrap
        return bootstrap_python_hint(bootstrap)

    @classmethod
    def sidecar_script(cls):
        from engines.ming_tts.bootstrap import MING_TTS_SIDECAR_SCRIPT
        return MING_TTS_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        """Receive timeout in seconds for the Ming-3.0-tts sidecar process."""
        # The first call cold-loads ~3 GB (never downloads); OMNIVOICE_MING_TTS_RECV_TIMEOUT_S tunes it.
        try:
            v = float(os.environ.get("OMNIVOICE_MING_TTS_RECV_TIMEOUT_S", "900"))
        except (ValueError, TypeError):
            return 900.0
        if not math.isfinite(v):
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # Upstream's tts_tasks.py resolves exactly these seven targets. Declaring
        # them lets the base-class ``_check_language`` reject anything else with
        # the standard language error instead of producing garbage audio.
        return ["zh", "en", "ja", "ko", "fr", "de", "es"]

    def generate(self, text: str, **kw) -> "torch.Tensor":
        """Synthesize one utterance through the Ming-3.0-tts sidecar.

        kwargs honored:
          * ``ref_audio`` — reference clip → ``prompt_wav_path``. **Required**
            (zero-shot cloning is the scope of this engine).
          * ``ref_text``  — the clip's transcript → ``prompt_text``; upstream
            uses the acoustic prompt only together with it. Optional.
          * ``language``  — one of zh/en/ja/ko/fr/de/es; anything else raises.
          * ``guidance_scale`` — CFG (upstream default 2.0).
          * ``speed``     — playback-rate multiplier; applied afterwards as an
            ffmpeg ``atempo`` stretch (pitch preserved), as upstream has none.

        Returns a tensor of shape (1, n_samples) at :attr:`sample_rate`.
        """
        declared = self._check_language(kw.get("language"))
        ref_audio = kw.get("ref_audio")
        if not ref_audio:
            from services.tts_backend import TTSInputError

            raise TTSInputError(
                "Ming-3.0-tts requires a reference audio for voice cloning. "
                "Pass ref_audio= with a path to a speaker reference clip."
            )
        forwarded: dict = {"ref_audio": os.path.abspath(os.fspath(ref_audio))}
        if kw.get("ref_text"):
            forwarded["ref_text"] = kw["ref_text"]
        if declared or kw.get("language"):
            forwarded["language"] = str(declared or kw["language"])
        guidance = kw.get("guidance_scale")
        if guidance is not None:
            forwarded["guidance_scale"] = float(guidance)
        # Checked before synthesis: a bad ``speed`` must not cost a full take.
        rate = kw.get("speed")
        if rate is not None:
            from services.tts_backend import TTSInputError

            try:
                rate = float(rate)
            except (TypeError, ValueError):
                raise TTSInputError(f"speed must be a number, got {rate!r}.") from None
            if not math.isfinite(rate) or rate <= 0:
                raise TTSInputError(f"speed must be a positive number, got {rate!r}.")
        audio = super().generate(text, **forwarded)
        # Ming has no speed control of its own: honour the public ``speed`` by
        # a pitch-preserving stretch of the finished take, never by ignoring it.
        if rate is not None and rate != 1.0:
            audio = _apply_speed(audio, self.sample_rate, rate)
        return audio


__all__ = ["MingTTSBackend"]
