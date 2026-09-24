"""omnivoice-subprocess: the resident OmniVoice TTS engine in a crash-isolated
sidecar process (#730/#1190).

The ``omnivoice`` engine runs in-process on CUDA, ROCm, and CPU. On MPS it is
resolved to :class:`OmniVoiceMPSSubprocessBackend` so a fatal native allocator
exit cannot take down the local API process.
When a generate or load there exceeds its execution budget the pool is "reset"
but the abandoned worker *thread* cannot be killed (Python cannot interrupt a
native torch/MPS call), so it holds the MPS device until it finishes on its
own, and every later synth contends with the zombie and hangs.

This engine runs the SAME OmniVoice model in a child process via
:class:`SubprocessBackend`. A child process CAN be hard-killed: on a recv
timeout the parent's watchdog calls ``proc.kill()``, reclaiming the child's
VRAM/device, and the next request transparently respawns a fresh sidecar. That
is the one thing the in-process engine structurally cannot do.

The explicit ``omnivoice-subprocess`` id remains available on every host for
operators who want the same containment elsewhere.

Tradeoff vs the in-process engine: identical model, controls, seed behavior,
and quality, with a little extra per-call overhead (one stdio round-trip).

Unlike IndexTTS / dots.tts / Supertonic-3, this sidecar runs under the PARENT
interpreter (``venv_python() -> sys.executable``): the goal here is crash
isolation, not dependency isolation, and the OmniVoice engine uses the host's
own pins.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.omnivoice_subprocess")


class OmniVoiceSubprocessBackend(SubprocessBackend):
    """The resident OmniVoice model in a killable sidecar process."""

    id = "omnivoice-subprocess"
    display_name = "OmniVoice (subprocess-isolated, killable on timeout)"
    _DEFAULT_SAMPLE_RATE = 24000
    gpu_compat = ("cuda", "rocm", "mps", "cpu")
    # Match OmniVoiceBackend: the measured floor below which a render that
    # should take seconds runs for minutes (the #1226/#1222 4 GB reports).
    min_vram_gb = 6.0
    # Packaged Windows hosts can spend more than the base 30 seconds starting
    # the shared Python runtime before this stdlib-only sidecar emits ready.
    # Keep the bound below the 300-second generation budget while avoiding the
    # repeated false kill captured in #1711.
    spawn_ready_timeout_s = 120.0
    # Same model as OmniVoiceBackend, so the same reference-length truth (#2281).
    max_ref_seconds = 20.0
    ref_strategy = "best_window"

    def generate(self, text: str, **kw):
        # The sidecar calls model.generate() directly, so it bypasses the
        # in-process prompt cache where a whole-clip transcript on an over-long
        # reference is dropped. Apply the same rule before the request leaves.
        passage = None
        audio = kw.get("ref_audio")
        if isinstance(audio, str):
            from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS
            from services.tts_backend import (
                _reuse_or_rank_passage,
                omnivoice_ref_text,
                reference_duration_s,
            )

            duration = reference_duration_s(audio)
            if duration is not None and duration > CLONE_REF_TEXT_MAX_SECONDS:
                selected = _reuse_or_rank_passage(audio)
                if selected is not None:
                    kw["ref_audio"], kw["ref_text"] = selected
                    passage = selected[0]
                elif kw.get("ref_text"):
                    kw["ref_text"] = omnivoice_ref_text(audio, kw["ref_text"])
        try:
            return super().generate(text, **kw)
        finally:
            if passage is not None:
                try:
                    os.remove(passage)
                except OSError:
                    pass

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Same probe as OmniVoiceBackend: the package must be importable. The
        # interpreter is the parent's own (sys.executable), so there is no
        # separate venv to validate.
        try:
            import omnivoice.models.omnivoice  # noqa: F401
        except Exception as e:
            return False, f"omnivoice package missing: {e}"
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        # Same interpreter as the parent: this engine isolates for crash
        # recovery, not dependency pins, so it needs no dedicated venv.
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        """Override the base 60s recv timeout.

        Aligns the kill deadline with the generate budget: a long-but-valid
        OmniVoice synth (which can take tens of seconds) is not falsely killed,
        while a genuinely wedged one is hard-killed and its VRAM reclaimed at
        the deadline. That reclaim is the concrete behavior the in-process
        engine lacks (it abandons but never frees the device).
        """
        try:
            return max(30.0, float(os.environ.get("OMNIVOICE_SIDECAR_RECV_TIMEOUT_S", "300")))
        except (ValueError, TypeError):
            return 300.0

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # OmniVoice advertises 600+ zero-shot; "multi" is the honest tag.
        return ["multi"]


class OmniVoiceMPSSubprocessBackend(OmniVoiceSubprocessBackend):
    """Effective ``omnivoice`` implementation on MPS.

    Native torch/MPS allocator failures can terminate the process without a
    catchable Python exception. Keeping the same engine id and model surface in
    a child makes that failure recoverable while Settings, APIs, and saved
    projects continue to refer to ``omnivoice``.
    """

    id = "omnivoice"
    display_name = "VoiceStudio (k2-fsa/OmniVoice, 600+ languages)"
    supports_native_omnivoice_controls = True

    def generate(self, text: str, **kw):
        from services.model_manager import make_room_before_generate

        make_room_before_generate()
        try:
            return super().generate(text, **kw)
        except RuntimeError as exc:
            if "sidecar closed pipe mid-generate" not in str(exc):
                raise
            raise RuntimeError(
                "The isolated OmniVoice engine stopped during generation, "
                "usually because macOS reclaimed it under memory pressure. "
                "The VoiceStudio backend is still running. Close memory-heavy "
                "apps or select a smaller TTS engine, then retry."
            ) from exc


__all__ = ["OmniVoiceMPSSubprocessBackend", "OmniVoiceSubprocessBackend"]
