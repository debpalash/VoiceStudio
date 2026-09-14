"""Supertonic-3 TTSBackend (Phase 3 Plan 03-01).

Subclasses Phase 2's :class:`SubprocessBackend` so the engine runs in its
own subprocess. Unlike IndexTTS (which needs a separate venv because of
the ``transformers<5`` pin), Supertonic-3's deps already live happily in
the OmniVoice parent venv ‑‑ ``onnxruntime``, ``numpy``, ``soundfile``,
``huggingface_hub`` are all already at compatible pins. The subprocess
isolation here is for *parity* with the SubprocessBackend pattern (so
crashes / leaks are contained and the rest of OmniVoice never blocks on
the SDK's cold init), not for dependency isolation.

Hardware honesty (TTS-04): Supertonic-3 is pure ONNX on the CPU EP. The
SDK ships no CUDA / MPS path. ``is_available()`` returns a message that
contains ``"cpu"`` and never ``"cuda"`` or ``"mps"`` ‑‑ the smoke test
asserts that. ``gpu_compat = ("cpu",)`` for the engine card.

License gate (TTS-05): first-use is gated behind a license acceptance
boolean persisted in the encrypted SQLite settings store. The frontend
``SupertonicLicenseDialog`` flips the bit via ``POST /settings/license``
once the user reviews the MIT (code) + OpenRAIL-M (model) terms.
``is_available()`` short-circuits to ``(False, "license not accepted ...")``
until acceptance lands.

Threat model (per Plan 03-01 frontmatter):
    T-03-02 ‑‑ HF model tampering: sidecar passes
               ``revision=PINNED_REVISION_SHA`` to ``snapshot_download``.
    T-03-03 ‑‑ token leak via env: SubprocessBackend.start() forwards
               HF_TOKEN/HF_ENDPOINT/HF_HUB_CACHE via os.environ.copy()
               (Phase 2 contract); we add ``SUPERTONIC3_REVISION`` on
               top as a non-secret hint to the sidecar.
    T-03-05 ‑‑ onnxruntime double-install: detected by the smoke test;
               ``supertonic 1.3.1`` declares only ``onnxruntime`` (CPU)
               in its wheel metadata, verified at lock time.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend
from engines.supertonic3 import constants as st3_constants

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.supertonic3")

_VENV_ENV_VAR = "OMNIVOICE_SUPERTONIC3_DIR"


def _own_venv_python() -> "Path | None":
    """The venv the one-click installer made for this engine, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(_VENV_ENV_VAR)


# Absolute path to the sidecar script ‑‑ same pattern as IndexTTS's
# ``INDEXTTS_SIDECAR_SCRIPT``. SubprocessBackend spawns it with the
# resolved venv python.
SUPERTONIC3_SIDECAR_SCRIPT: Path = Path(__file__).parent / "sidecar.py"


class Supertonic3Backend(SubprocessBackend):
    """Supertonic-3 ‑‑ 31-language ONNX TTS, CPU-only, ~99M params.

    Runs in a long-lived sidecar over length-prefixed JSON-over-stdio.
    First synthesize cold-downloads ~400 MB of model weights pinned to
    :data:`PINNED_REVISION_SHA` (TTS-03). Subsequent calls reuse the
    process and the in-memory ONNX session.

    Licence: MIT (SDK code) / OpenRAIL-M (model weights). First use is
    gated behind a license-acceptance boolean (TTS-05).
    """

    id = "supertonic3"
    display_name = "Supertonic-3 (31 langs, CPU ONNX, 7 preset voices, OpenRAIL-M)"
    supports_voice_design = False  # preset voices only
    supports_cloning = False  # preset voices only; generate() never reads ref_audio
    # TTS-04: honest hardware reporting. Supertonic-3 has no CUDA / MPS
    # path in the SDK ‑‑ ONNX Runtime CPU EP only.
    gpu_compat: tuple[str, ...] = ("cpu",)
    _DEFAULT_SAMPLE_RATE = st3_constants.SAMPLE_RATE

    # ── SubprocessBackend contract ─────────────────────────────────────

    @classmethod
    def venv_python(cls) -> Path:
        """Its own venv when the one-click installer made one. Otherwise the
        parent interpreter, the same Python ``uv sync --extra supertonic``
        populated.
        """
        return _own_venv_python() or Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return SUPERTONIC3_SIDECAR_SCRIPT

    @property
    def recv_timeout_s(self) -> float:
        """Receive timeout in seconds for the Supertonic-3 sidecar process (#2103)."""
        # Supertonic-3 runs ONNX on CPU; cold load downloads ~400MB and long
        # synthesis benefits from more headroom than 60s. OMNIVOICE_SUPERTONIC3_RECV_TIMEOUT_S (#2103).
        try:
            v = float(os.environ.get("OMNIVOICE_SUPERTONIC3_RECV_TIMEOUT_S", "300"))
        except (ValueError, TypeError):
            return 300.0
        if not math.isfinite(v):
            return 300.0
        return max(30.0, v)

    # ── availability ───────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # 1. Optional-dep gate (TTS-02). The ``supertonic`` wheel is only
        #    installed when the user opted in via ``--extra supertonic``.
        #    Its own venv (made by the one-click installer, which verified the
        #    import there) or the app's environment (`uv sync --extra`).
        if _own_venv_python() is None:
            try:
                import supertonic  # type: ignore[import-not-found]  # noqa: F401
            except ImportError:
                return False, (
                    "supertonic package not installed. Install it from "
                    "Model Catalogue."
                )

        # 2. License acceptance gate (TTS-05). Defence in depth: the
        #    settings_store helper handles the read; we just refuse
        #    activation until the bit is True.
        try:
            from services import settings_store
            accepted = settings_store.get_license_accepted(cls.id)
        except Exception as exc:  # SQLite read failure shouldn't crash
            logger.warning(
                "supertonic3: settings_store.get_license_accepted raised %s — "
                "treating as not-accepted",
                exc,
            )
            accepted = False
        if not accepted:
            return False, (
                "Supertonic-3 license not accepted. Open Model Catalogue → "
                "Supertonic-3 and click Accept to enable. "
                "(MIT code license + OpenRAIL-M model license.)"
            )

        # 3. Honest hardware report (TTS-04). No CUDA / MPS path in the
        #    upstream SDK ‑‑ we say so plainly.
        return True, "ready (CPU-only via onnxruntime)"

    # ── TTSBackend protocol ────────────────────────────────────────────

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # 31 ISO codes + "na" fallback per the SDK; we expose "multi" on
        # the protocol surface (same approach as OmniVoice / CosyVoice)
        # and translate the caller's language at synthesize time.
        return ["multi"]

    # ── extra env for the sidecar (T-03-02 mitigation) ─────────────────

    @property
    def _sidecar_env(self) -> dict[str, str]:
        """Defence in depth: pass the pinned SHA to the sidecar via env
        even though the sidecar reads the same constant from the
        in-tree module. If a future SubprocessBackend.start() supports
        ``extra_env``, this property is the surface to extend.
        """
        return {"SUPERTONIC3_REVISION": st3_constants.PINNED_REVISION_SHA}

    # ── generate ───────────────────────────────────────────────────────

    def generate(self, text: str, **kw) -> "torch.Tensor":
        """Synthesize one utterance.

        kwargs honored:
          * ``voice``        ‑‑ one of :data:`VOICE_PRESETS` (str). Default
                                ``DEFAULT_VOICE``. Unknown ids log a warning
                                and fall back.
          * ``language``     ‑‑ ISO 639-1 code or ``"auto"`` / ``None``.
                                ``"auto"`` and ``None`` map to ``"na"`` so
                                the SDK's multilingual fallback engages.
          * ``speed``        ‑‑ float, clamped to [0.7, 2.0].
          * ``num_step``     ‑‑ int (SDK ``total_steps``), clamped to
                                [5, 12].

        Returns a tensor of shape ``(1, n_samples)`` at
        :attr:`sample_rate`. Delegates to
        :meth:`SubprocessBackend.generate` which handles the JSON
        round-trip, GPU-slot acquire/release, and int16 PCM decode.
        """
        # Set the revision env on the parent process before the sidecar
        # spawns ‑‑ SubprocessBackend.start() captures parent env at
        # spawn time via os.environ.copy(). This way, if the sidecar is
        # not yet running, the spawn picks up our pin; if it's already
        # running, the sidecar's _resolve_pinned_sha() already read the
        # right value at boot. Idempotent + safe.
        os.environ.setdefault(
            "SUPERTONIC3_REVISION", st3_constants.PINNED_REVISION_SHA,
        )

        voice = kw.get("voice") or st3_constants.DEFAULT_VOICE
        if voice not in st3_constants.VOICE_PRESETS:
            logger.info(
                "supertonic3: unknown voice %r, falling back to %r. Valid: %s",
                voice, st3_constants.DEFAULT_VOICE, st3_constants.VOICE_PRESETS,
            )
            voice = st3_constants.DEFAULT_VOICE

        language = kw.get("language")
        speed = float(kw.get("speed", 1.0))
        speed = max(0.7, min(2.0, speed))

        total_steps = int(kw.get("num_step", 8))
        total_steps = max(5, min(12, total_steps))

        # Forward through SubprocessBackend.generate. The base class
        # filters kwargs through ``_is_jsonable`` and forwards JSON-safe
        # ones verbatim ‑‑ ``voice``, ``language``, ``speed``,
        # ``total_steps`` all qualify.
        forwarded = {
            "voice": voice,
            "lang": language if language is None else str(language),
            "speed": speed,
            "total_steps": total_steps,
        }
        return super().generate(text, **forwarded)


__all__ = ["Supertonic3Backend", "SUPERTONIC3_SIDECAR_SCRIPT"]
