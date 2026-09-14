"""dots.tts sidecar package (issue #498).

dots.tts is rednote-hilab's 2B fully-continuous autoregressive TTS — widely
cited as among the strongest open zero-shot voice-cloning models. 24
languages, 48 kHz output, Apache-2.0 (code + checkpoints).

It runs in its own subprocess **and its own venv**, isolated from the
OmniVoice parent, for the same ``transformers`` reason as IndexTTS and
MOSS-TTS-v1.5: dots.tts pins ``transformers==4.57.0`` (verified against
``constraints/recommended.txt``), while OmniVoice pins
``transformers>=5.3.0``. The two cannot share one interpreter.

Cross-platform honesty (the strict default-parity rule): dots.tts's
upstream package declares **Linux + macOS** classifiers only — **no
Windows** — and its device code is **CUDA-or-CPU with no MPS branch**
(verified in ``runtime.py``). So:

  * It is **opt-in** (engine-picker selection + a user-provided clone),
    never a default — so it never becomes a broken default on any platform.
  * ``is_available()`` returns ``False`` with a clear reason on **Windows**
    rather than offering an engine that can't run there. Windows users are
    pointed at WSL2 / a Linux or macOS host.
  * ``gpu_compat = ("cuda", "cpu")`` — no MPS claim. On Apple Silicon the
    upstream package runs on CPU (slow but correct); the faster MLX path is
    a community port we deliberately don't auto-wire here.

Three public entry points: ``DotsTTSBackend`` (this module), ``main.py``
(sidecar, runs under dots.tts's ``transformers==4.57`` venv — never imported
by the parent), and ``bootstrap.py`` (venv probe + lazy bootstrap).
"""
from __future__ import annotations

import logging
import math
import os
import sys
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.dots_tts")


class DotsTTSBackend(SubprocessBackend):
    """dots.tts (rednote-hilab) — 2B, 24 langs, zero-shot clone, CUDA/CPU.

    Runs in a long-lived sidecar over length-prefixed JSON-over-stdio in a
    dedicated venv (``transformers==4.57.0``). First synthesize cold-loads
    the ~9 GB checkpoint (bf16 on CUDA); subsequent calls reuse the process.

    Installation (OmniVoice prefers a user's existing ``${DIR}/.venv``)::

        git clone https://github.com/rednote-hilab/dots.tts.git
        cd dots.tts
        uv venv && uv pip install -e . -c constraints/recommended.txt

    Set ``OMNIVOICE_DOTS_TTS_DIR`` to the clone root. OmniVoice creates
    ``backend/engines/dots_tts/.venv`` lazily on first launch if no venv
    exists yet; the user's existing ``${DIR}/.venv`` is preferred if present.

    Best cloning quality uses the ``dots.tts-soar`` checkpoint (the default)
    and BOTH a reference clip and its exact transcript (continuation
    cloning). License: Apache-2.0.
    """

    id = "dots-tts"
    display_name = (
        "dots.tts (2B, 24 langs, zero-shot clone, CUDA/CPU, 48 kHz, Apache-2.0)"
    )
    supports_voice_design = False  # requires ref audio for timbre cloning
    # dots.tts emits 48 kHz (verified via checkpoint vocoder.sample_rate).
    _DEFAULT_SAMPLE_RATE = 48000
    # CUDA + CPU only; no MPS branch in upstream runtime.py.
    gpu_compat = ("cuda", "cpu")

    # ── availability ───────────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Cross-platform parity: dots.tts upstream is Linux/macOS-only (no
        # Windows classifier, no Windows install path). Refuse cleanly on
        # Windows instead of advertising an engine that can't run.
        if sys.platform == "win32":
            return False, (
                "dots.tts is not supported on Windows — upstream targets "
                "Linux and macOS only. Run VoiceStudio under WSL2, or use a "
                "Linux/macOS host. See docs/engines/dots-tts.md."
            )

        # Do NOT import dots_tts here: it pins transformers==4.57, which can't
        # coexist with the parent's transformers>=5.3 in one interpreter —
        # the reason for the subprocess isolation. Verify the venv on disk
        # only; a real health-check is gated on the user's "Test engine"
        # action in Settings.
        from engines.dots_tts.bootstrap import (
            DOTS_TTS_SIDECAR_SCRIPT,
            is_dots_tts_installed,
        )
        if not is_dots_tts_installed():
            return False, (
                "dots.tts venv not found. Set OMNIVOICE_DOTS_TTS_DIR to your "
                "dots.tts clone (the directory containing pyproject.toml) and "
                "restart VoiceStudio. CUDA or CPU only (no MPS). See "
                "docs/engines/dots-tts.md for the full install walk-through."
            )
        if not DOTS_TTS_SIDECAR_SCRIPT.exists():
            return False, (
                "dots.tts sidecar script missing at "
                f"{DOTS_TTS_SIDECAR_SCRIPT} — reinstall VoiceStudio."
            )
        return True, "ok (CUDA when present, else CPU)"

    @classmethod
    def venv_python(cls):
        from engines.dots_tts.bootstrap import resolve_dots_tts_venv
        return resolve_dots_tts_venv()

    @classmethod
    def sidecar_script(cls):
        from engines.dots_tts.bootstrap import DOTS_TTS_SIDECAR_SCRIPT
        return DOTS_TTS_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        """Receive timeout in seconds for the dots.tts sidecar process (#2103)."""
        # dots.tts is a 2B autoregressive model; synthesis on CPU legitimately
        # outruns the 60s class default. OMNIVOICE_DOTS_TTS_RECV_TIMEOUT_S tunes it (#2103).
        try:
            v = float(os.environ.get("OMNIVOICE_DOTS_TTS_RECV_TIMEOUT_S", "900"))
        except (ValueError, TypeError):
            return 900.0
        if not math.isfinite(v):
            return 900.0
        return max(30.0, v)

    # ── TTSBackend protocol ────────────────────────────────────────────────

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # 24 languages with auto-detect; expose "multi" on the protocol
        # surface and translate the caller's language at synthesize time.
        return ["multi"]

    # ── generate (parent-side arbitration) ─────────────────────────────────

    def generate(self, text: str, **kw) -> "torch.Tensor":
        """Synthesize one utterance through the dots.tts sidecar.

        kwargs honored:
          * ``ref_audio`` — reference clip path → ``prompt_audio_path``
                            (zero-shot cloning). Optional.
          * ``ref_text``  — the reference transcript → ``prompt_text``.
                            Best cloning fidelity ("continuation"). Upstream
                            REQUIRES ``prompt_audio_path`` when ``prompt_text``
                            is set, so we drop a stray ref_text with no
                            ref_audio rather than let the sidecar raise.
          * ``language``  — ISO code / name / None (auto-detect).
          * ``num_step``  — flow-matching steps → ``num_steps`` (default 10;
                            use 4 for the ``dots.tts-mf`` checkpoint).
          * ``guidance_scale`` — CFG (default 1.2; >2 amplifies energy).

        Returns a tensor of shape (1, n_samples) at :attr:`sample_rate`.
        """
        forwarded: dict = {}

        ref_audio = kw.get("ref_audio")
        if ref_audio:
            forwarded["ref_audio"] = ref_audio
            ref_text = kw.get("ref_text")
            if ref_text:
                # continuation cloning — only valid alongside ref_audio.
                forwarded["ref_text"] = ref_text
        elif kw.get("ref_text"):
            logger.info(
                "dots-tts: ref_text supplied without ref_audio; ignoring "
                "(upstream requires prompt_audio_path when prompt_text is set)."
            )

        language = kw.get("language")
        if language:
            forwarded["language"] = str(language)

        # OmniVoice's generic num_step default is 16; dots.tts's own default
        # is 10. Honor an explicit value, else use the dots-appropriate 10.
        num_step = kw.get("num_step")
        forwarded["num_steps"] = int(num_step) if num_step is not None else 10

        # dots.tts's own CFG default is 1.2 (the generic 2.0 over-energises).
        guidance = kw.get("guidance_scale")
        forwarded["guidance_scale"] = float(guidance) if guidance is not None else 1.2

        return super().generate(text, **forwarded)


__all__ = ["DotsTTSBackend"]
