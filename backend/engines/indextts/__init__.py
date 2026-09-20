"""IndexTTS 2.5/2 sidecar package (Phase 2 Plan 02-03).

IndexTTS-2 runs in its own subprocess + dedicated venv with
``transformers<5``, isolated from the VoiceStudio parent process which
pins ``transformers>=5.3``. Closes issue #42 — the canonical
``OffloadedCache`` ImportError driven by the transformers v4 ↔ v5
incompatibility — by making the two libraries live in separate OS
processes.

Three public entry points live in this package:

  * ``IndexTTS2Backend`` (this module) — the SubprocessBackend subclass
    that ``services.tts_backend._REGISTRY`` resolves lazily on first
    access. The class is defined HERE rather than inside
    ``services.tts_backend`` because importing ``SubprocessBackend`` at
    that module's top level would cycle with
    ``services.subprocess_backend``'s ``from services.tts_backend import
    TTSBackend`` line. Defining the class in this package breaks the
    cycle: ``services.tts_backend`` finishes loading before anything
    here is imported.
  * ``main.py`` — the sidecar entrypoint (runs under a different venv).
  * ``bootstrap.py`` — the venv-probe + lazy-bootstrap helper.

Do NOT import ``main.py`` from the parent process — it runs under a
different venv and may not have access to the parent's installed
packages. The parent only ever spawns it as a subprocess.
"""
from __future__ import annotations

import logging
import math
import os
import re
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.indextts")


_LANGUAGE_ALIASES = {
    "zh": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "en": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "es": "es",
    "spanish": "es",
    "español": "es",
    "ar": "ar",
    "arabic": "ar",
}


def _normalize_indextts25_language(value, text: str) -> str:
    """Map VoiceStudio locale labels to IndexTTS 2.5's required token."""
    raw = str(value or "").strip().lower().replace("_", "-")
    base = raw.split("-", 1)[0]
    resolved = _LANGUAGE_ALIASES.get(raw) or _LANGUAGE_ALIASES.get(base)
    if resolved:
        return resolved
    # Auto/empty requests still need an explicit 2.5 language. Script
    # detection is deterministic and local; ambiguous Latin text defaults EN.
    if re.search(r"[\u0600-\u06ff\u0750-\u077f]", text):
        return "ar"
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    return "en"


def _duration_factor(text: str, language: str, duration: float) -> float:
    """Map VoiceStudio's absolute duration to IndexTTS 2.5's relative scale."""
    from services.speech_rate import expected_duration

    natural_s = expected_duration(text, language)
    if natural_s <= 0:
        return 1.0
    return max(0.5, min(float(duration) / natural_s, 2.0))


class IndexTTS2Backend(SubprocessBackend):
    """IndexTTS 2.5 (Bilibili) — isolated subprocess with IndexTTS-2 fallback.

    Plan 02-03 migrated IndexTTS off the in-process import path because
    IndexTTS pins ``transformers<5`` while VoiceStudio pins
    ``transformers>=5.3``. The two cannot share a Python interpreter
    without one of them blowing up at import time (issue #42 — the
    canonical ``OffloadedCache`` ImportError). Running IndexTTS in a
    subprocess with its own venv lets both libraries co-exist.

    Key differentiators preserved from the in-process incarnation:
      * **Emotion decoupling** — clone timbre from one reference, apply
        emotion from a completely separate source (audio, 8-float
        vector, or text).
      * **Duration control** — first AR model to precisely target
        output length (critical for video dubbing lip-sync).
      * **8-float emotion vector** — [happy, angry, sad, afraid,
        disgusted, melancholic, surprised, calm] — each 0.0–1.0.
      * **Text-based emotion** — natural-language emotion descriptions
        via a fine-tuned Qwen3 encoder, ``emo_alpha`` capped at 0.6.

    Installation (transparent to existing v0.2.7 users — ENGINE-07)::

        git clone --branch indextts-2.5 https://github.com/index-tts/index-tts.git
        cd index-tts && uv pip install -e .   # NOT uv sync --all-extras
        hf download IndexTeam/IndexTTS-2.5 --local-dir=checkpoints

    Set ``OMNIVOICE_INDEXTTS_DIR`` to the repo root. VoiceStudio will
    create ``backend/engines/indextts/.venv`` lazily on first launch if
    no venv exists yet — the user's existing
    ``${OMNIVOICE_INDEXTTS_DIR}/.venv`` is preferred if present, so no
    re-install is needed.

    License: bilibili Model Use License. A separate license is required
    above the upstream 100M-MAU or RMB 1B annual-revenue thresholds.
    """

    id = "indextts2"
    display_name = "IndexTTS 2.5 (multilingual emotion-controlled cloning)"
    supports_voice_design = False  # requires ref audio for timbre
    supports_emotion = True  # graded emo_vector / emo_text / emo_alpha (#1208)
    _DEFAULT_SAMPLE_RATE = 24000
    # Explicit so IndexTTS2 stops advertising the inherited CPU-only default:
    # the sidecar runs the IndexTTS PyTorch model on CUDA when present, else
    # CPU. ROCm left unclaimed (the sidecar's own venv would need a ROCm torch);
    # a ROCm host honestly resolves to cpu_fallback.
    gpu_compat = ("cuda", "cpu")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # IMPORTANT: do NOT attempt ``import indextts`` here. The parent's
        # transformers>=5.3 cannot coexist with IndexTTS's transformers<5
        # in one interpreter — that's the entire reason this backend
        # lives in a subprocess. We only verify the venv exists on disk
        # and the sidecar script ships with the install. Health-checking
        # the sidecar is gated on user action (Settings → 'Test engine').
        from engines.indextts.bootstrap import (
            INDEXTTS_SIDECAR_SCRIPT,
            is_indextts_installed,
        )
        if not is_indextts_installed():
            return False, (
                "IndexTTS 2.5 venv not found. Set OMNIVOICE_INDEXTTS_DIR to "
                "your IndexTTS clone (the directory containing checkpoints/) "
                "and restart VoiceStudio. See docs/engines/indextts.md for the "
                "full install walk-through."
            )
        if not INDEXTTS_SIDECAR_SCRIPT.exists():
            return False, (
                "IndexTTS sidecar script missing at "
                f"{INDEXTTS_SIDECAR_SCRIPT} — reinstall VoiceStudio."
            )
        return True, "ok"

    @classmethod
    def venv_python(cls):
        from engines.indextts.bootstrap import resolve_indextts_venv
        return resolve_indextts_venv()

    @property
    def recv_timeout_s(self) -> float:
        # IndexTTS was the only sidecar left on the 60s class default while
        # pockettts and omnivoice-subprocess both raised theirs. infer() is one
        # blocking upstream call, so a long passage legitimately outruns 60s and
        # the parent's watchdog killed a healthy synthesis (#1611). main.py also
        # heartbeats during infer(), which is what actually proves liveness —
        # this deadline is the ceiling for a sidecar that has gone genuinely
        # silent. OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S tunes it.
        try:
            v = float(os.environ.get("OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S", "900"))
        except (ValueError, TypeError):
            return 900.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 900.0
        return max(30.0, v)

    @classmethod
    def sidecar_script(cls):
        from engines.indextts.bootstrap import INDEXTTS_SIDECAR_SCRIPT
        return INDEXTTS_SIDECAR_SCRIPT

    @property
    def sample_rate(self) -> int:
        # Advertised by the sidecar's ready frame; pinned at the class
        # level so callers (engine picker, dub pipeline) can query
        # without spawning the sidecar.
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        configured = os.environ.get("OMNIVOICE_INDEXTTS_DIR")
        if configured and not os.path.isfile(
            os.path.join(configured, "indextts", "infer_v2_5.py")
        ):
            # Preserve truthful metadata for user-managed IndexTTS-2 checkouts.
            return ["zh", "en"]
        return ["zh", "en", "ja", "es", "ar"]

    # ── parent-side emotion / duration arbitration ─────────────────────
    #
    # The sidecar accepts any of: emo_vector, emo_audio_prompt+emo_alpha,
    # emo_text+use_emo_text+emo_alpha+use_random. We do the priority
    # arbitration here so the wire payload is unambiguous and the
    # sidecar's dispatch stays narrow (mirrors the legacy in-process
    # arbitration at the old tts_backend.py:855-907).
    #
    # Override ``generate`` to translate the public ``generate(text,
    # **kw)`` API into the sidecar's synthesize op. The base class's
    # ``generate`` would still work (it forwards every JSON-safe
    # kwarg), but the priority arbitration would land in the sidecar,
    # fragmenting logic. Keeping it parent-side also lets us drop
    # ``description → emo_text`` without the sidecar knowing what
    # ``description`` means.

    def generate(self, text: str, **kw) -> "torch.Tensor":
        self._check_language(kw.get("language"))
        ref_audio = kw.get("ref_audio")
        if not ref_audio:
            raise RuntimeError(
                "IndexTTS2 requires a reference audio for voice cloning "
                "(timbre). Pass ref_audio= with a path to a speaker "
                "reference clip."
            )

        emo_vector = kw.get("emo_vector")
        emo_audio = kw.get("emo_audio")
        emo_text = kw.get("emo_text")
        emo_alpha = float(kw.get("emo_alpha", 1.0))
        use_random = bool(kw.get("use_random", False))

        # Voice-design fallback: if ``description`` came in via the
        # OpenAI-compatible TTS route, treat it as a text emotion prompt.
        description = kw.get("description")
        if description and not emo_text and not emo_vector and not emo_audio:
            emo_text = description

        language = _normalize_indextts25_language(kw.get("language"), text)
        forwarded: dict = {"ref_audio": ref_audio, "lang": language}

        # Duration control — codec frame rate ≈ 21 Hz.
        duration = kw.get("duration")
        if duration is not None:
            target_tokens = int(float(duration) * 21)
            if target_tokens > 0:
                forwarded["target_tokens"] = target_tokens
        duration_factor = kw.get("duration_factor")
        if duration_factor is not None:
            forwarded["duration_factor"] = max(0.5, min(float(duration_factor), 2.0))
        elif duration is not None:
            # IndexTTS 2.5 replaced absolute semantic-token control with a
            # relative duration factor. Use the same language-aware natural
            # reading estimate as the dubbing fit planner so the public
            # ``duration`` control remains effective on 2.5; the sidecar
            # drops this factor when it detects a legacy IndexTTS-2 checkout.
            forwarded["duration_factor"] = _duration_factor(
                text, language, float(duration)
            )

        if (
            emo_vector
            and isinstance(emo_vector, (list, tuple))
            and len(emo_vector) == 8
        ):
            forwarded["emo_vector"] = [float(v) for v in emo_vector]
            forwarded["use_random"] = use_random
            logger.info(
                "IndexTTS2: emotion via vector %s", forwarded["emo_vector"],
            )
        elif emo_audio:
            forwarded["emo_audio_prompt"] = emo_audio
            forwarded["emo_alpha"] = emo_alpha
            logger.info(
                "IndexTTS2: emotion via audio ref (alpha=%.2f)", emo_alpha,
            )
        elif emo_text:
            forwarded["emo_text"] = emo_text
            forwarded["use_emo_text"] = True
            forwarded["emo_alpha"] = min(emo_alpha, 0.6)
            forwarded["use_random"] = use_random
            logger.info(
                "IndexTTS2: emotion via text description: %r (alpha=%.2f)",
                emo_text[:60], forwarded["emo_alpha"],
            )

        # Delegate to SubprocessBackend.generate which handles the JSON
        # round-trip, GPU slot acquire/release, and int16 PCM decode.
        return super().generate(text, **forwarded)


__all__ = ["IndexTTS2Backend"]
