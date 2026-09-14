"""Confucius4-TTS sidecar package (issue #590).

Confucius4-TTS (netease-youdao) is an LLM-based multilingual / cross-lingual
zero-shot voice-cloning TTS: 14 languages, **no reference transcript required**,
cross-lingual voice transfer, Apache-2.0 (https://github.com/netease-youdao/Confucius4-TTS).

Like IndexTTS / MOSS-TTS-v1.5 / dots.tts it runs in its **own subprocess venv**
(upstream: Python 3.10 + CUDA 12.6 + its own deps), isolated from the OmniVoice
parent. It is **opt-in** — selected in the engine picker and enabled only when
the user points ``OMNIVOICE_CONFUCIUS4_TTS_DIR`` at a clone — so it can never
become a broken default on any platform (the strict default-parity rule).

Status (#590): **validated end-to-end** (2026-07-02, Apple Silicon, CPU) — the
synthesis API (``confuciustts.cli.inference.ConfuciusTTS`` →
``.generate(text, lang, prompt_wav)`` → tensor, ``model.sample_rate``) produced
audible speech at 22 050 Hz; the sidecar's pure logic is unit-tested
(``tests/test_confucius4_sidecar.py``). CPU inference is slow (~17× realtime),
so CUDA is the recommended path. Gated off by default, so this affects no one
until they opt in.

Three entry points: ``Confucius4Backend`` (this module), ``main.py`` (the sidecar,
runs under the Confucius4 venv — never imported by the parent), and
``bootstrap.py`` (venv probe + lazy bootstrap).
"""
from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.confucius4")


class Confucius4Backend(SubprocessBackend):
    """Confucius4-TTS (netease-youdao) — LLM-based, 14 langs, zero-shot clone.

    Runs in a long-lived sidecar over length-prefixed JSON-over-stdio in a
    dedicated venv. First synthesize cold-loads the checkpoint; subsequent calls
    reuse the process.

    Installation::

        git clone https://github.com/netease-youdao/Confucius4-TTS.git
        cd Confucius4-TTS
        uv venv --python 3.10 && uv pip install -r requirements.txt

    (Upstream ships no pyproject.toml/setup.py, so there is nothing to
    ``pip install -e`` — the sidecar sys.path-inserts the clone instead.)
    Then set ``OMNIVOICE_CONFUCIUS4_TTS_DIR`` to the clone root and restart.
    License: Apache-2.0. CUDA recommended; CPU validated but ~17× realtime.
    """

    id = "confucius4-tts"
    display_name = (
        "Confucius4-TTS (LLM, 14 langs, cross-lingual zero-shot clone, Apache-2.0)"
    )
    supports_voice_design = False  # timbre comes from a reference clip
    # Upstream vocoder rate (config target_sample_rate) — confirmed 22 050 Hz by
    # a live run (2026-07-02); still re-read from the sidecar's ready/audio frames.
    _DEFAULT_SAMPLE_RATE = 22050
    # Match device propagation into upstream .to(device). XPU/NPU routing is
    # contract-tested, not a claim of physical-hardware synthesis validation.
    gpu_compat = ("cuda", "rocm", "xpu", "npu", "cpu")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Verify the venv on disk only — do NOT import the engine here (separate
        # interpreter). A real health-check runs on the user's "Test engine"
        # action in Settings.
        from engines.confucius4.bootstrap import (
            CONFUCIUS4_SIDECAR_SCRIPT,
            is_confucius4_installed,
        )
        if not is_confucius4_installed():
            return False, (
                "Confucius4-TTS venv not found. Set OMNIVOICE_CONFUCIUS4_TTS_DIR "
                "to your Confucius4-TTS clone (the directory containing "
                "requirements.txt) and restart VoiceStudio. CUDA GPU recommended "
                "(CPU works but is slow). See docs/engines/confucius4-tts.md."
            )
        if not CONFUCIUS4_SIDECAR_SCRIPT.exists():
            return False, (
                "Confucius4-TTS sidecar script missing at "
                f"{CONFUCIUS4_SIDECAR_SCRIPT} — reinstall VoiceStudio."
            )
        return True, "ok"

    @classmethod
    def venv_python(cls):
        from engines.confucius4.bootstrap import resolve_confucius4_venv
        return resolve_confucius4_venv()

    @classmethod
    def sidecar_script(cls):
        from engines.confucius4.bootstrap import CONFUCIUS4_SIDECAR_SCRIPT
        return CONFUCIUS4_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        """Receive timeout in seconds for the Confucius4 sidecar process (#2103)."""
        # Confucius4 is an LLM-based TTS (~17x realtime on CPU); synthesis legitimately
        # outruns the 60s class default. OMNIVOICE_CONFUCIUS4_RECV_TIMEOUT_S tunes it (#2103).
        try:
            v = float(os.environ.get("OMNIVOICE_CONFUCIUS4_RECV_TIMEOUT_S", "900"))
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
        # 14 languages with the caller's language passed through at synthesize
        # time; "multi" on the protocol surface.
        return ["multi"]

    def generate(self, text: str, **kw) -> "torch.Tensor":
        """Synthesize one utterance through the Confucius4 sidecar.

        kwargs honored:
          * ``ref_audio`` — reference clip path → ``prompt_wav`` (zero-shot
            cloning). Optional but recommended for a specific voice.
          * ``language`` — ISO code / name → ``lang`` (cross-lingual transfer).
          * ``ref_text`` is intentionally ignored — Confucius4 is unconstrained
            cloning (no reference transcript needed).

        Returns a tensor of shape (1, n_samples) at :attr:`sample_rate`.
        """
        forwarded: dict = {}
        ref_audio = kw.get("ref_audio")
        if ref_audio:
            forwarded["ref_audio"] = ref_audio
        language = kw.get("language")
        if language:
            forwarded["language"] = str(language)
        return super().generate(text, **forwarded)


__all__ = ["Confucius4Backend"]
