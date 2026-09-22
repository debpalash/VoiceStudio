"""Crash-isolated ASR backends (Wave 4.2 / Spec 7).

Native ASR engines (the whisper.cpp / CTranslate2 class) can segfault on GPU
teardown — a process-level crash that takes the whole backend down with it.
Running them in a child process turns that segfault into a *failed job*: the
sidecar dies, the parent surfaces a decorated error, and the next request
respawns a fresh sidecar.

This reuses ``SubprocessBackend``'s wire protocol + lifecycle (spawn, ready
handshake, length-prefixed JSON, GPU-slot acquire, and — critically —
respawn-on-dead-process: ``_spawn`` relaunches whenever the previous child
isn't alive). We add a ``transcribe`` op alongside the TTS ``synthesize`` op;
the TTS ``generate`` surface is stubbed since an ASR sidecar never synthesizes.

The base is engine-agnostic; concrete subclasses point ``sidecar_script()`` at
an engine runner. ``IsolatedFasterWhisperBackend`` wraps faster-whisper (the
CTranslate2 engine with the documented GPU-teardown crash) using the parent
venv — faster-whisper is already a dependency, so no separate venv is needed,
only process isolation.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
from pathlib import Path

from services.subprocess_backend import (
    RECV_TIMEOUT_S,
    SubprocessBackend,
)

logger = logging.getLogger("omnivoice.asr.subprocess")

# A model load + transcription can take a while on CPU for a long clip; give
# the transcribe round-trip more headroom than the TTS default. Configurable via
# OMNIVOICE_ASR_RECV_TIMEOUT_S (#2103).
ASR_RECV_TIMEOUT_S = 600.0


class SubprocessASRBackend(SubprocessBackend):
    """Crash-isolated ASR over the SubprocessBackend protocol.

    Concrete subclasses set ``id`` / ``display_name`` and override
    ``venv_python()`` / ``sidecar_script()``. They are registered in the ASR
    registry (``services.asr_backend._REGISTRY``); the registry uses
    ``is_available()`` + ``transcribe()`` duck-typed, so subclassing the TTS
    ``SubprocessBackend`` is fine.
    """

    # ── TTS surface stubs (an ASR sidecar never synthesizes) ───────────────
    @property
    def sample_rate(self) -> int:  # pragma: no cover - unused
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:  # pragma: no cover - unused
        return ["multi"]

    def generate(self, text: str, **kw):  # pragma: no cover - unused
        raise NotImplementedError("ASR sidecar does not synthesize speech")

    def ensure_loaded(self) -> None:
        """Prove the lazy ASR sidecar is ready for the shared loader."""
        with self._lock:
            self._spawn()

    # ── ASR surface ────────────────────────────────────────────────────────
    @staticmethod
    def _device() -> str:
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
        return "cpu"

    @property
    def recv_timeout_s(self) -> float:
        """Wall-clock timeout in seconds waiting for an ASR sidecar response (#2103)."""
        try:
            v = float(os.environ.get("OMNIVOICE_ASR_RECV_TIMEOUT_S", str(ASR_RECV_TIMEOUT_S)))
        except (ValueError, TypeError):
            return ASR_RECV_TIMEOUT_S
        if not math.isfinite(v):
            return ASR_RECV_TIMEOUT_S
        return max(30.0, v)

    def supports_translation(self) -> bool:
        # Same model resolution as the sidecar (_asr_sidecar/main.py).
        from services.asr_backend import faster_whisper_model_id, whisper_checkpoint_translates
        return whisper_checkpoint_translates(
            os.environ.get("ASR_MODEL_FW") or faster_whisper_model_id()
        )

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, initial_prompt: str | None = None,
                   temperature: float | None = None,
                   task: str = "transcribe") -> dict:
        """Transcribe ``audio_path`` in the sidecar. Returns the engine's
        result dict ({"segments": [...], "language": ...}).

        A sidecar crash mid-transcription raises a RuntimeError decorated with
        the engine id + device (so the failure is attributable, not a bare
        broken-pipe) — and the *next* call respawns a fresh sidecar via
        ``_spawn``'s dead-process check. Acquires a GPU-pool slot for the
        duration, released even if the child dies (the base's try/finally)."""
        # On-pool callers (run_transcribe_guarded dispatches via run_in_executor
        # on the GPU pool) already own a pool slot; re-acquiring would
        # self-deadlock on a 1-worker (MPS) pool, so skip it. Off-pool callers
        # hold a real slot for the whole transcription via _occupy. Mirrors
        # SubprocessBackend.generate()'s path-aware slot block.
        from services.model_manager import running_on_gpu_pool
        _held = None
        slot_future = None
        if not running_on_gpu_pool():
            from services.model_manager import _get_gpu_pool
            pool = _get_gpu_pool()
            _held = threading.Event()
            _acquired = threading.Event()

            def _occupy():
                _acquired.set()
                _held.wait()

            slot_future = pool.submit(_occupy)

        try:
            if _held is not None and not _acquired.wait(timeout=10):
                if slot_future is not None:
                    slot_future.cancel()
                raise TimeoutError("timed out waiting for a free GPU worker")
            timeout_s = self.recv_timeout_s
            with self._lock:
                self._spawn()
                from services.performance_profiles import asr_decode_defaults
                from services.asr_backend import whisper_request_options
                request = whisper_request_options(
                    language, initial_prompt, temperature, task)
                self._send({
                    "op": "transcribe",
                    "audio_path": str(audio_path),
                    "word_timestamps": bool(word_timestamps),
                    "decode_options": asr_decode_defaults(),
                    # Sent only when set, so a frame without them is
                    # byte-identical to before.
                    **({"request_options": request} if request else {}),
                })
                reply = self._recv_with_timeout(timeout_s)
                timed_out = self._last_recv_timed_out
                if not reply:
                    # EOF can arrive before Windows updates poll(); retire the
                    # stale handle so an immediate retry respawns the sidecar.
                    self.shutdown()
                    if timed_out:
                        raise RuntimeError(
                            f"{self.id} ASR sidecar exceeded receive timeout "
                            f"({timeout_s:g}s); killed mid-transcription "
                            f"(device={self._device()}) — retry or raise "
                            f"OMNIVOICE_ASR_RECV_TIMEOUT_S."
                        )
                    # Pipe closed mid-transcription → the child crashed.
                    raise RuntimeError(
                        f"{self.id} ASR sidecar crashed mid-transcription "
                        f"(device={self._device()}); the job failed but the backend "
                        f"stayed up — retry to respawn a fresh sidecar."
                    )
            if reply.get("op") == "error":
                raise RuntimeError(
                    f"{self.id} ASR sidecar error (device={self._device()}): "
                    f"{reply.get('message')!r}"
                )
            if reply.get("op") != "segments":
                raise RuntimeError(
                    f"{self.id} ASR sidecar returned unexpected op: {reply.get('op')!r}"
                )
            return reply.get("result") or {"segments": [], "language": "unknown"}
        finally:
            if _held is not None:
                _held.set()


class IsolatedFasterWhisperBackend(SubprocessASRBackend):
    """faster-whisper (CTranslate2) in a child process — opt-in.

    CTranslate2's GPU teardown can segfault (the endemic faster-whisper crash);
    running it isolated keeps that from killing the backend. Uses the PARENT
    venv (faster-whisper is already installed) — only the process boundary is
    new. Select with ``OMNIVOICE_ASR_BACKEND=faster-whisper-isolated``.
    """

    id = "faster-whisper-isolated"
    display_name = "Faster-Whisper (crash-isolated subprocess)"
    # Same engine as FasterWhisperBackend, so the same device support — the
    # sidecar picks cuda/cpu itself via `_device()`. Without this the registry
    # default ("cpu",) would dishonestly report cpu_only routing on CUDA hosts.
    gpu_compat = ("cuda", "cpu")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from core.execstack import ensure_ctranslate2_loadable

        ok, detail = ensure_ctranslate2_loadable()
        if not ok:
            return False, f"faster-whisper cannot load CTranslate2: {detail}"
        try:
            import faster_whisper  # noqa: F401
        except Exception as e:
            return False, f"faster-whisper not installed: {e}"
        if not cls.sidecar_script().is_file():
            return False, f"ASR sidecar script missing at {cls.sidecar_script()}"
        # Same CTranslate2 engine, same cuDNN 8 requirement (#1371). Crash
        # isolation means a missing cuDNN 8 kills only the child — so instead of
        # a dead backend the user gets a sidecar that fails every transcribe
        # with no explanation. Report it here, where Model Catalogue shows it.
        from services.asr_backend import _ctranslate2_cudnn_ok

        return _ctranslate2_cudnn_ok()

    @classmethod
    def venv_python(cls) -> Path:
        # faster-whisper lives in the parent venv — isolation is process-only.
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "engines" / "_asr_sidecar" / "main.py"
