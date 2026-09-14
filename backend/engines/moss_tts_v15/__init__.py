"""MOSS-TTS-v1.5 sidecar package (issue #498).

MOSS-TTS-v1.5 is OpenMOSS's 8B flagship TTS — a Qwen3-8B language backbone
plus a 1.6B audio codec, 31 languages, zero-shot voice cloning, token-level
duration control and inline ``[pause Ns]`` markers. Apache-2.0.

It runs in its own subprocess **and its own venv**, isolated from the
OmniVoice parent process, for the *same* reason IndexTTS does: a hard
``transformers`` version conflict. MOSS-TTS-v1.5's ``torch-runtime`` extra
pins ``transformers==5.0.0`` (verified against the upstream
``pyproject.toml``), while OmniVoice pins ``transformers>=5.3.0``. The two
cannot share one interpreter — so MOSS lives behind ``SubprocessBackend``
with a dedicated venv, exactly like ``engines.indextts``.

Three public entry points live in this package:

  * ``MossTTSV15Backend`` (this module) — the SubprocessBackend subclass
    that ``services.tts_backend._LAZY_REGISTRY`` resolves on first access.
    Defined HERE (not in ``services.tts_backend``) to break the import
    cycle: ``services.subprocess_backend`` imports ``TTSBackend`` from
    ``services.tts_backend``, so the backend class must live downstream of
    that module finishing its import. Same indirection as IndexTTS /
    Supertonic-3.
  * ``main.py`` — the sidecar entrypoint (runs under MOSS's venv with
    ``transformers==5.0.0``; never imported by the parent).
  * ``bootstrap.py`` — the venv-probe + lazy-bootstrap helper.

Do NOT import ``main.py`` from the parent process — it runs under a
different venv (``transformers==5.0.0``) and importing it in-process would
re-introduce the exact conflict this isolation exists to avoid.

Hardware routing follows the sidecar's runtime-available PyTorch accelerator:
CUDA/ROCm, XPU, or a registered NPU. MPS remains excluded; CPU is the fallback.
XPU/NPU routing is covered with mocked device contracts, not physical-hardware
synthesis certification; users need a compatible torch/vendor runtime in the
isolated engine venv.
"""
from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.moss_tts_v15")

#: 1 second of audio ≈ 12.5 codec tokens (MOSS-TTS-v1.5 model card). Used to
#: translate OmniVoice's ``duration`` (seconds) into the model's ``tokens``
#: duration-control argument.
TOKENS_PER_SECOND: float = 12.5


class MossTTSV15Backend(SubprocessBackend):
    """MOSS-TTS-v1.5 (OpenMOSS) — 8B, 31 langs, zero-shot clone, CUDA/CPU.

    Runs in a long-lived sidecar over length-prefixed JSON-over-stdio in a
    dedicated venv (``transformers==5.0.0``). The first synthesize cold-loads
    ~16 GB of bf16 weights (CUDA) / fp32 (CPU); subsequent calls reuse the
    process and the in-memory model.

    Installation (transparent to power users who already cloned MOSS-TTS —
    OmniVoice prefers their existing ``${DIR}/.venv``)::

        git clone https://github.com/OpenMOSS/MOSS-TTS.git
        cd MOSS-TTS
        # CUDA host:
        uv venv && uv pip install -e ".[torch-runtime]"
        # non-CUDA host (CPU): install plain torch/transformers instead of +cu128

    Set ``OMNIVOICE_MOSS_TTS_V15_DIR`` to the clone root. OmniVoice creates
    ``backend/engines/moss_tts_v15/.venv`` lazily on first launch if no venv
    exists yet (CUDA hosts only — the upstream ``torch-runtime`` extra is
    ``+cu128``); the user's existing ``${DIR}/.venv`` is preferred if
    present, so no re-install is needed.

    License: Apache-2.0 (code + weights) — no acceptance gate needed.
    """

    id = "moss-tts-v15"
    display_name = (
        "MOSS-TTS-v1.5 (8B, 31 langs, zero-shot clone, Apache-2.0)"
    )
    supports_voice_design = False  # requires ref audio for timbre cloning
    _DEFAULT_SAMPLE_RATE = 24000
    # Accelerator routing requires its matching runtime in the isolated venv.
    # MPS remains untested and is deliberately excluded.
    gpu_compat = ("cuda", "rocm", "xpu", "npu", "cpu")

    # ── availability ───────────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # IMPORTANT: do NOT attempt to import MOSS / its transformers==5.0.0
        # here. The parent pins transformers>=5.3 — co-importing the two in
        # one interpreter is exactly the conflict this subprocess isolation
        # exists to avoid. We only verify the venv exists on disk; a real
        # health-check (spawn + ping) is gated on the user's "Test engine"
        # action in Settings, same as IndexTTS.
        from engines.moss_tts_v15.bootstrap import (
            MOSS_TTS_V15_SIDECAR_SCRIPT,
            is_moss_tts_v15_installed,
        )
        if not is_moss_tts_v15_installed():
            return False, (
                "MOSS-TTS-v1.5 venv not found. Set OMNIVOICE_MOSS_TTS_V15_DIR "
                "to your MOSS-TTS clone (the directory containing pyproject.toml) "
                "and restart VoiceStudio. Install the matching PyTorch runtime. See "
                "docs/engines/moss-tts-v15.md for the full install walk-through."
            )
        if not MOSS_TTS_V15_SIDECAR_SCRIPT.exists():
            return False, (
                "MOSS-TTS-v1.5 sidecar script missing at "
                f"{MOSS_TTS_V15_SIDECAR_SCRIPT} — reinstall VoiceStudio."
            )
        return True, "ok (runtime-available accelerator or CPU; no MPS)"

    @classmethod
    def venv_python(cls):
        from engines.moss_tts_v15.bootstrap import resolve_moss_tts_v15_venv
        return resolve_moss_tts_v15_venv()

    @classmethod
    def sidecar_script(cls):
        from engines.moss_tts_v15.bootstrap import MOSS_TTS_V15_SIDECAR_SCRIPT
        return MOSS_TTS_V15_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        """Receive timeout in seconds for the MOSS-TTS-v1.5 sidecar process (#2103)."""
        # MOSS-TTS-v1.5 is an 8B model; synthesis legitimately outruns the
        # 60s class default. OMNIVOICE_MOSS_TTS_V15_RECV_TIMEOUT_S tunes it (#2103).
        try:
            v = float(os.environ.get("OMNIVOICE_MOSS_TTS_V15_RECV_TIMEOUT_S", "900"))
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
        # 31 languages with multilingual handling; expose "multi" on the
        # protocol surface (same as OmniVoice / CosyVoice / Supertonic-3) and
        # translate the caller's language at synthesize time.
        return ["multi"]

    # ── generate (parent-side arbitration) ─────────────────────────────────

    def generate(self, text: str, **kw) -> "torch.Tensor":
        """Synthesize one utterance through the MOSS-TTS-v1.5 sidecar.

        kwargs honored:
          * ``ref_audio``  — path to a reference clip. When present, MOSS
                              runs zero-shot voice cloning (``reference=``).
                              Optional: without it the model uses its own
                              default voice.
          * ``ref_text``   — accepted but unused in clone mode (MOSS's
                              zero-shot path needs only the audio); kept in
                              the signature so the common call-site doesn't
                              need engine-specific knowledge.
          * ``language``   — ISO code or name; mapped to a MOSS language name
                              in the sidecar, omitted (auto-detect) if unknown.
          * ``duration``   — target seconds → ``tokens`` (1 s ≈ 12.5 tokens).
          * ``max_new_tokens`` — generation cap (default 4096).

        Returns a tensor of shape (1, n_samples) at :attr:`sample_rate`.
        """
        forwarded: dict = {}

        ref_audio = kw.get("ref_audio")
        if ref_audio:
            forwarded["ref_audio"] = ref_audio
        ref_text = kw.get("ref_text")
        if ref_text:
            forwarded["ref_text"] = ref_text

        language = kw.get("language")
        if language:
            forwarded["language"] = str(language)

        duration = kw.get("duration")
        if duration is not None:
            target_tokens = int(float(duration) * TOKENS_PER_SECOND)
            if target_tokens > 0:
                forwarded["tokens"] = target_tokens

        max_new_tokens = kw.get("max_new_tokens")
        if max_new_tokens is not None:
            forwarded["max_new_tokens"] = int(max_new_tokens)

        return super().generate(text, **forwarded)


__all__ = ["MossTTSV15Backend", "TOKENS_PER_SECOND"]
