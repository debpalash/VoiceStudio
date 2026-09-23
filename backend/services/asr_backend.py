"""
ASR adapter interface — Phase 3.3 (ROADMAP.md).

One protocol, multiple engines. Today we ship:

    • FasterWhisperBackend — CTranslate2-based (the engine WhisperX uses).
                            Default on Linux, Windows, mac-Intel. Also fast
                            on mac-ARM so we use it as the cross-platform
                            baseline and only prefer MLX on mac-ARM when
                            explicitly installed.
    • MLXWhisperBackend   — mlx-whisper on Apple Silicon. Optional speedup,
                            only available when mlx wheels install (mac-ARM).
    • PyTorchWhisperBackend — last-resort fallback using the existing
                            `_asr_pipe` on the TTS model.

Both return the raw Whisper output dict so `services.segmentation.
segment_transcript(...)` can keep working unchanged — new backends normalise
their output to the `{"chunks": [{"text", "timestamp": (start, end)}]}`
shape the segmenter expects.

Selection via `OMNIVOICE_ASR_BACKEND` (default: auto-detect, prefers
faster-whisper because it's available on every platform we ship to).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import contextlib
import threading
import time
import weakref
from urllib.parse import urlsplit
from utils.containment import contain_system_exit

from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("omnivoice.asr")

# A single ASR transcribe must never block a request indefinitely. The chunked
# dub pipeline already bounds each chunk (OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S);
# the *whole-file* paths (dub QC re-transcribe, dictation, OpenAI-compat) ran
# unbounded, so a slow/stuck transcribe — e.g. large-v3 on a VRAM-starved GPU
# where the resident TTS model contends for memory — hung the request *and* tied
# up a GPU-pool worker, surfacing in the UI as the misleading "can't reach the
# local backend" (TamKieu / Vietnam report). Bound them so a hang becomes a fast,
# actionable error instead. Generous default (whole-file large-v3 on CPU is slow
# but valid); override with the env var for very long single files.
ASR_TRANSCRIBE_TIMEOUT_S = float(os.environ.get("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "300.0"))


class ASRTimeoutError(TimeoutError):
    """Raised when a whole-file transcribe exceeds ASR_TRANSCRIBE_TIMEOUT_S.

    Carries a user-actionable message: the backend is alive (this is not a
    connection failure) — the ASR model is too heavy for the available compute.
    """


def reset_pool_after_wedge(executor, *, what: str = "ASR") -> bool:
    """Abandon a GPU pool whose worker is wedged on a timed-out transcribe (#730).

    Python can't kill the stuck thread, but dropping the poisoned pool means the
    next submit (a retry, the next chunk, or a concurrent TTS generate) gets a
    fresh worker instead of queueing behind the wedged one. This is the ONE
    recovery mechanism shared by every transcribe path — the whole-file guards
    (via :func:`run_transcribe_guarded`) and the chunked dub stream both route
    through it, so the semantics can't drift between them again.

    Best-effort: an executor without ``reset()`` (a plain ThreadPoolExecutor in
    tests) is a no-op, and a failing reset never raises — this runs on the very
    failure path it's trying to recover from. Returns True when a reset ran.
    """
    _reset = getattr(executor, "reset", None)
    if not callable(_reset):
        return False
    try:
        _reset()
        logger.warning(
            "%s transcribe wedged — abandoned the GPU-pool worker to restore "
            "capacity (#730).", what,
        )
        return True
    except Exception:
        logger.exception("GPU pool reset after %s timeout failed", what)
        return False


# ── Consecutive-timeout streak → recommend the crash-isolated engine ────────
# A timed-out CTranslate2/whisperx thread keeps its worker and VRAM until the
# native call exits. When guarded transcribes keep timing out back-to-back in
# one session, the durable fix is the crash-isolated sidecar engine
# (services.subprocess_asr, #393), whose child process CAN be hard-killed to
# reclaim the hung call and its VRAM. We only *recommend* it (log + error
# message); we never switch engines automatically (owner rule: no silent
# behavior divergence).
_TIMEOUT_STREAK_FOR_ISOLATED_HINT = 2
_timeout_streak = 0
_timeout_streak_lock = threading.Lock()


def _note_transcribe_timeout() -> int:
    global _timeout_streak
    with _timeout_streak_lock:
        _timeout_streak += 1
        return _timeout_streak


def _note_transcribe_success() -> None:
    global _timeout_streak
    with _timeout_streak_lock:
        _timeout_streak = 0


def _isolated_engine_hint(streak: int) -> str:
    """User-facing recommendation once resets stop recovering (streak ≥ 2).

    Empty when the streak is below the threshold, or when the user is already
    on the isolated engine (recommending it to itself would be noise — the
    base message's smaller-model/CPU guidance is all that's left)."""
    if streak < _TIMEOUT_STREAK_FOR_ISOLATED_HINT:
        return ""
    try:
        if active_backend_id() == "faster-whisper-isolated":
            return ""
    except Exception:  # noqa: BLE001 — the hint must never break the error path
        pass
    logger.warning(
        "%d consecutive ASR transcribe timeouts this session — pool resets are "
        "not recovering the hang. Recommend switching the ASR engine to "
        "'Faster-Whisper (crash-isolated subprocess)' [faster-whisper-isolated] "
        "in Model Catalogue. Not switching automatically (#730).", streak,
    )
    return (
        f"This is {streak} transcribe timeouts in a row this session, so pool "
        "resets aren't recovering the underlying hang. Recommended: switch the "
        "ASR engine to 'Faster-Whisper (crash-isolated subprocess)' "
        "(faster-whisper-isolated) in Model Catalogue — it runs "
        "transcription in a separate process that can be force-killed to "
        "reclaim a hung transcribe and its VRAM. VoiceStudio never switches "
        "engines automatically."
    )


async def run_transcribe_guarded(executor, fn, *, what: str = "ASR",
                                 timeout: float = ASR_TRANSCRIBE_TIMEOUT_S,
                                 timeout_env: str = "OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S",
                                 reset_on_timeout: bool = False,
                                 on_abandon=None):
    """Run a blocking transcribe ``fn`` in ``executor`` with a hard wall-clock
    bound. On timeout, raise :class:`ASRTimeoutError` with guidance instead of
    letting the request hang forever.

    A future cannot cancel the underlying thread, so a timed-out
    in-process CTranslate2/whisperx call still owns its model and device. The
    default deliberately leaves that worker accounted for: swapping in a fresh
    pool and immediately retrying the same backend overlaps two native calls,
    which produced the Windows access violation in #1669. A caller backed by a
    genuinely killable process may opt into ``reset_on_timeout``.

    ``on_abandon`` is called once after a timed-out or cancelled worker can no
    longer access its inputs. Queued work cancelled before it starts calls it
    immediately; running work calls it from the worker finalizer. Normal
    completion leaves cleanup with the caller.
    """
    from services.inference_cancellation import InferenceCancellation
    cancellation = InferenceCancellation()
    loop = asyncio.get_running_loop()
    # Same SystemExit containment as the TTS pool (#1133 class): an ASR
    # dependency written as a CLI must not be able to shut the backend down.
    inner = contain_system_exit(fn, what)
    abandon_lock = threading.Lock()
    abandon_state = {
        "requested": False,
        "finished": False,
        "callback_called": False,
    }

    def _fire_abandon_callback() -> None:
        if on_abandon is None:
            return
        with abandon_lock:
            if abandon_state["callback_called"]:
                return
            abandon_state["callback_called"] = True
        try:
            on_abandon()
        except Exception:  # noqa: BLE001 — cleanup cannot hide the ASR result
            logger.exception("%s abandon cleanup failed", what)

    def _job():
        try:
            with cancellation.activate():
                return inner()
        finally:
            with abandon_lock:
                abandon_state["finished"] = True
                abandoned = abandon_state["requested"]
            if abandoned:
                _fire_abandon_callback()

    concurrent_fut = executor.submit(_job)
    fut = asyncio.wrap_future(concurrent_fut, loop=loop)

    def _abandon() -> None:
        cancellation.cancel()
        cancelled_before_start = concurrent_fut.cancel()
        with abandon_lock:
            abandon_state["requested"] = True
            finished = abandon_state["finished"]
        fut.cancel()
        if cancelled_before_start or finished:
            _fire_abandon_callback()

    try:
        # Shield the wrapper so timeout does not discard our ability to tell a
        # queued cancellation from a native thread that is still running.
        result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.CancelledError:
        _abandon()
        raise
    except asyncio.TimeoutError:
        _abandon()
        if reset_on_timeout:
            reset_pool_after_wedge(executor, what=what)
        streak = _note_transcribe_timeout()
        msg = (
            f"{what} transcription exceeded {timeout:.0f}s and was abandoned — "
            "the backend is running, but the ASR model is too heavy for the "
            "available compute. Most often the GPU is VRAM-starved: the resident "
            "TTS model and a large ASR model (large-v3) contend for memory. "
            "The native call cannot be killed safely, so its capacity remains "
            "reserved until it exits. For a durable fix Flush the "
            "TTS model to free VRAM, pick a smaller ASR model in "
            f"the engine's Weights list in Model Catalogue, or set ASR to CPU. (Raise {timeout_env} "
            "for very long transcribes.)"
        )
        hint = _isolated_engine_hint(streak)
        if hint:
            msg += " " + hint
        raise ASRTimeoutError(msg)
    # A completed transcribe (even a failed-but-returned one) proves the pool
    # isn't hung — only genuine timeouts count toward the consecutive streak.
    _note_transcribe_success()
    return result


def _compute_type_candidates(device: str) -> list[str]:
    """Per-device compute_type fallback chain. int8 is supported by every
    CTranslate2 CUDA+CPU build; float16/int8_float16 only on GPUs with efficient
    fp16 — so degrade rather than crash (#551). Honors an ASR_COMPUTE_TYPE env
    override (power users on exotic hardware can pin int8/float32)."""
    import os
    override = os.environ.get("ASR_COMPUTE_TYPE")
    if override:
        return [override]
    return ["float16", "int8_float16", "int8"] if device == "cuda" else ["int8", "float32"]


def _is_compute_type_error(msg: str) -> bool:
    low = msg.lower()
    return "compute type" in low or "efficient float16" in low


def _ctranslate2_cudnn_ok() -> tuple[bool, str]:
    """Availability gate for the two CTranslate2 engines (WhisperX, faster-whisper).

    Importing them proves nothing about cuDNN 8: CTranslate2 only reaches for it
    when it builds a CUDA model, and if it is missing the library prints
    ``Could not locate cudnn_ops_infer64_8.dll`` and ``__fastfail``s — taking the
    whole backend down with 0xC0000409, no exception, no traceback, nothing to
    fall back from (#1371). The shell restarts the backend, the user retries,
    and it dies again.

    So ask *before* selecting the engine, and let ``_auto_detect`` fall through
    to pytorch-whisper — which runs on torch's own cuDNN 9 stack and exists for
    exactly this case. Same shape as the #692 exec-stack handling: a native
    library we cannot load makes the engine unavailable, not fatal.
    """
    try:
        from core.cudnn8 import ctranslate2_cudnn_status

        return ctranslate2_cudnn_status()
    except Exception as e:  # noqa: BLE001 — a broken probe must not block ASR
        logger.debug("cuDNN 8 probe unavailable (%s) — assuming usable", e)
        return True, "ready"


def _ctranslate2_execstack_ok() -> tuple[bool, str]:
    """Make CTranslate2 importable on kernels that refuse an executable stack.

    ctranslate2 ≤4.4.0 — the version whisperx 3.4.5 pins, and 3.4.5 is the
    newest release that supports the Python 3.11 we ship — marks its native
    library's stack ``RWE``. Kernels that refuse the request fail the dlopen
    with "cannot enable executable stack", killing whisperx, faster-whisper
    *and* Argos translation (#692). :mod:`core.execstack` clears that one bit
    in place, so call this BEFORE importing either engine; it cheaply rechecks the library and
    only writes when the library would otherwise refuse to load.
    """
    try:
        from core.execstack import ensure_ctranslate2_loadable

        return ensure_ctranslate2_loadable()
    except Exception as e:  # noqa: BLE001 — a broken repair must not block ASR
        logger.debug("exec-stack repair unavailable (%s) — continuing", e)
        return True, "repair probe unavailable"


def _decode_audio_16k_mono(audio_path: str):
    """Decode `audio_path` to a 16 kHz mono float32 waveform using VoiceStudio's
    *validated* ffmpeg, instead of whisperx.load_audio's bare ``"ffmpeg"`` PATH
    lookup.

    whisperx (and openai-whisper) shell out to a literal ``"ffmpeg"`` resolved
    against the OS PATH. On Windows that resolves to whatever the system finds
    first — a WindowsApps alias stub or a corrupt/wrong-arch download — which
    passes `which` but explodes at spawn with ``[WinError 193] %1 is not a valid
    Win32 application``. whisperx only catches `CalledProcessError`, so the
    spawn-time `OSError` escapes and the dub/batch path reports the opaque
    "Transcription produced no segments" (#479). ``find_ffmpeg()`` probes each
    candidate with ``-version`` and returns a runnable binary (the bundled
    imageio-ffmpeg / Tauri sidecar) — or None, so we can raise an actionable
    error. This also fixes the imageio case a PATH-prepend can't: its binary is
    named ``ffmpeg-<plat>-vN.exe``, not ``ffmpeg``, so bare lookup never finds
    it. Mirrors whisperx.audio.load_audio's command exactly (16 kHz, mono, s16le).
    """
    import subprocess

    import numpy as np

    from services.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "Cannot transcribe: ffmpeg is missing or not runnable. Install "
            "ffmpeg (or let VoiceStudio's bundled binary download), then retry. "
            "On Windows a '[WinError 193]' here means the ffmpeg binary is "
            "corrupt or the wrong architecture — reinstall it or clear the "
            "imageio-ffmpeg cache."
        )
    cmd = [
        ffmpeg, "-nostdin", "-threads", "0", "-i", audio_path,
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except OSError as e:
        # Belt-and-suspenders: find_ffmpeg() already -version-validated this
        # binary, so a WinError 193 here is unexpected — surface it clearly
        # rather than letting it become "no segments".
        raise RuntimeError(
            f"ffmpeg at {ffmpeg!r} could not be executed ({e}). Reinstall "
            "ffmpeg or clear the imageio-ffmpeg cache."
        ) from e
    except subprocess.CalledProcessError as e:
        from services.ffmpeg_utils import raise_for_audio_extract_failure

        # A file with no audio stream gets the shared actionable error, not
        # ffmpeg's stream dump (NoAudioTrackError; raw stderr goes to the log).
        raise_for_audio_extract_failure(e.stderr or b"", audio_path)
        stderr = (e.stderr or b"").decode(errors="replace")[:500]
        raise RuntimeError(f"Failed to decode audio for transcription: {stderr}") from e
    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0


# ── Protocol ────────────────────────────────────────────────────────────────


#: Per-request decode options a backend may accept as keyword arguments on
#: ``transcribe()``. Callers exposing OpenAI's transcription parameters
#: (``/v1/audio/transcriptions`` and ``/translations``) pass only the ones a
#: backend's signature declares, so an engine that cannot honour one is never
#: handed it and never fails on an unexpected keyword.
TRANSCRIBE_REQUEST_OPTIONS = ("language", "initial_prompt", "temperature", "task")


def whisper_request_options(language, initial_prompt, temperature, task) -> dict:
    """Whisper-family decode kwargs for the options that were actually set."""
    opts: dict = {}
    if language:
        opts["language"] = language
    if initial_prompt:
        opts["initial_prompt"] = initial_prompt
    if temperature is not None:
        opts["temperature"] = float(temperature)
    if task and task != "transcribe":
        opts["task"] = task
    return opts


def whisper_checkpoint_translates(model_name: str | None) -> bool:
    """Whether a Whisper checkpoint was trained for speech→English translation.

    Turbo (large-v3-turbo) was fine-tuned on transcription data only and
    returns the source language even with ``task="translate"``; English-only
    ``*.en`` models and Distil-Whisper checkpoints are English-only too. Those
    must be refused, not answered with untranslated text labelled English.
    """
    name = (model_name or "").lower()
    return not ("turbo" in name or name.endswith(".en") or "distil" in name)


class ASRBackend(ABC):
    id: str = "base"
    display_name: str = "Base ASR"
    # Backends normally receive bounded chunks from the dub stream. Set this
    # when speaker labels are clustered only within one transcribe() call: the
    # caller must then submit the full recording or identical numeric labels
    # from separate chunks can refer to different people.
    requires_full_audio_for_speaker_consistency: bool = False
    # Accelerator families this backend can use, in preference order; always
    # includes a fallback. Subset of {cuda, rocm, mps, xpu, cpu}. Mirrors the
    # TTSBackend.gpu_compat contract so engine_routing.resolve_routing() can
    # surface the effective device per host (no silent CPU fallback). The
    # conservative default is CPU-only; subclasses declare what they really run
    # on. (ROCm is intentionally NOT claimed yet for any ASR engine — see the
    # per-engine notes; an unverified `rocm` claim would route ROCm hosts to a
    # broken GPU path, strictly worse than the honest `cpu_fallback`.)
    gpu_compat: tuple[str, ...] = ("cpu",)

    def execution_evidence_loaded(self) -> bool:
        """Whether this instance has live model state worth reporting."""
        if getattr(self, "runs_out_of_process", False):
            proc = getattr(self, "_proc", None)
            return proc is not None and proc.poll() is None
        return any(
            getattr(self, attr, None) is not None
            for attr in ("_model", "_asr", "_pipeline", "_pipe", "_transcriber", "_rec")
        )

    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        ...

    def supports_translation(self) -> bool:
        """Whether ``transcribe(task="translate")`` really yields English.
        Default False; Whisper backends answer from their loaded checkpoint."""
        return False

    @abstractmethod
    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        """Return the raw Whisper output dict. Callers (`segment_transcript`)
        know how to read it — this stays deliberately untyped so new engines
        that already speak the shape plug in with zero adapter work.
        """

    def ensure_loaded(self) -> None:
        """Eagerly load the model weights, raising the real cause on failure.

        Backends load lazily inside ``transcribe()`` by default, so a load
        failure (missing weights, CUDA/cuDNN mismatch, torch-2.6 weights-only
        VAD regression, import error) first surfaces buried in per-chunk
        errors — and is retried on *every* chunk. The transcribe preflight
        calls this so the genuine cause is surfaced once, up front, as a clean
        terminal error event instead of N cryptic per-chunk failures (#578).

        Default is a no-op; backends that hold a heavy model override it to
        trigger their lazy loader. It MUST raise the underlying exception (not
        swallow it) so the caller can classify and surface it.
        """
        pass

    def unload(self) -> None:
        """Release the model from memory."""
        pass


# ── WhisperX (cross-platform default — forced-alignment word timing) ────────


def _harden_speechbrain_lazy_imports() -> None:
    """Make speechbrain 1.x's lazy-import guard fire on Windows too (#630/#611/#647).

    speechbrain 1.x exposes optional integrations (``k2_fsa``, ``numba`` losses,
    ``spacy``/``flair`` nlp) as ``LazyModule`` redirects living in ``sys.modules``.
    Stray introspection — PyTorch's op-registration machinery, pickling, a
    ``dir()``/``hasattr`` walk — touches one of these during ``whisperx.load_model``
    (pyannote → speechbrain), which would *actually* import the optional package.
    speechbrain guards against that by suppressing the import when the triggering
    frame is the stdlib ``inspect`` module — but the check is
    ``filename.endswith("/inspect.py")``, a hardcoded POSIX separator. On Windows
    the frame filename uses backslashes (``...\\Lib\\inspect.py``), so the guard
    misses, the redirect imports ``speechbrain.integrations.k2_fsa`` → ``import k2``
    → k2 isn't installed → ``ImportError: Lazy import of LazyModule(...k2_fsa...)
    failed``. That bubbles out of WhisperX and aborts transcription with zero
    segments. WhisperX is the *default* ASR, so this is a Windows-only break of a
    cross-platform-default feature (P0 parity).

    Fix the whole class — every optional-integration redirect, not just k2 — by
    re-implementing ``LazyModule.ensure_module`` with an ``os.sep``-agnostic
    basename check. Idempotent and a no-op on macOS/Linux (basename match is a
    strict superset of the old forward-slash check) and when speechbrain is
    absent. A genuine access from real user code with k2 missing still raises
    ImportError unchanged — only inspect-triggered spurious imports are
    suppressed, on every platform.
    """
    try:
        from speechbrain.utils import importutils as _iu
    except Exception:  # speechbrain not installed / import side-effect — nothing to harden
        return
    if getattr(_iu.LazyModule, "_omnivoice_xplat_guard", False):
        return
    import importlib as _importlib
    import inspect as _inspect
    import sys as _sys
    import warnings as _warnings

    def ensure_module(self, stacklevel):
        importer_frame = None
        try:
            importer_frame = _inspect.getframeinfo(_sys._getframe(stacklevel + 1))
        except AttributeError:
            _warnings.warn(
                "Failed to inspect frame to check if we should ignore importing a "
                "module lazily (VoiceStudio cross-platform guard)."
            )
        if importer_frame is not None:
            # Normalise BOTH separators explicitly (not os.path.basename, which is
            # host-dependent) so the guard is correct regardless of which os.path
            # flavour is active. Upstream's `.endswith("/inspect.py")` matched only
            # POSIX paths — that is the Windows-only bug (#630/#611/#647).
            base = importer_frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if base == "inspect.py":
                raise AttributeError()
        if self.lazy_module is None:
            try:
                if self.package is None:
                    self.lazy_module = _importlib.import_module(self.target)
                else:
                    self.lazy_module = _importlib.import_module(f".{self.target}", self.package)
            except Exception as e:  # noqa: BLE001 — match upstream: wrap as ImportError
                raise ImportError(f"Lazy import of {repr(self)} failed") from e
        return self.lazy_module

    _iu.LazyModule.ensure_module = ensure_module
    _iu.LazyModule._omnivoice_xplat_guard = True
    logger.debug("speechbrain LazyModule guard hardened for cross-platform inspect.py check")


#: wav2vec2 aligners, keyed by (language, device). Shared across backends: the
#: aligner is independent of whatever produced the segments, so MLX (which
#: transcribes on the GPU) reuses exactly the aligner WhisperX would have used.
_ALIGN_CACHE: dict[tuple[str, str], object] = {}

#: Forced alignment is torch/wav2vec2 (not CTranslate2), so unlike Whisper itself
#: it *can* run on MPS — measured on an M2: 20.3 s vs 28.4 s for a 30 s chunk, with
#: byte-identical word timings. So MPS is preferred, but torchaudio's MPS coverage
#: is uneven across aligner models, and a failure here would silently cost us the
#: ±10-30 ms timing that lip-sync depends on. Hence: try MPS, fall back to **CPU**,
#: and only then give up and keep Whisper's own looser timestamps.
_ALIGN_DEVICE_ENV = "OMNIVOICE_ALIGN_DEVICE"


def load_align_model(language_code: str, device: str):
    """Lazy-load (and cache) the wav2vec2 aligner for a language.

    Returns ``(model, metadata)``, or ``None`` when no aligner exists for the
    language — WhisperX bundles them for ~20 major languages only, and the
    caller then keeps Whisper's own (looser) word timestamps."""
    key = (language_code, device)
    if key in _ALIGN_CACHE:
        return _ALIGN_CACHE[key]
    try:
        import whisperx

        model, metadata = whisperx.load_align_model(
            language_code=language_code, device=device,
        )
        _ALIGN_CACHE[key] = (model, metadata)
    except Exception as e:  # noqa: BLE001 — missing aligner is normal, not fatal
        logger.info(
            "no wav2vec2 aligner for language=%r (%s); "
            "falling back to Whisper's native word timestamps",
            language_code, e,
        )
        _ALIGN_CACHE[key] = None
    return _ALIGN_CACHE[key]


def forced_align(segments: list, audio, language_code: str, device: str | None = None) -> list:
    """Snap word boundaries to the audio with wav2vec2 forced alignment.

    This is what buys the dub pipeline its ±10-30 ms word timing (vs Whisper's
    own ±100-300 ms), and lip-sync quality depends on it. It takes *plain
    segments*, so it is deliberately independent of which engine transcribed
    them — which is what lets the MLX backend transcribe on the GPU and still
    get WhisperX-grade timing.

    Returns the aligned segments, or the originals unchanged if alignment isn't
    available (no aligner for the language, whisperx not installed, or the
    alignment itself failed). Never raises: worse timing beats no transcript.
    """
    if not segments:
        return segments

    pinned = device or os.environ.get(_ALIGN_DEVICE_ENV)
    if pinned:
        devices = [pinned]
    elif _mps_available():
        devices = ["mps", "cpu"]  # fast path, then the always-works path
    else:
        devices = ["cpu"]

    for i, dev in enumerate(devices):
        align = load_align_model(language_code, dev)
        if align is None:
            return segments  # no aligner for this language — not a device problem
        model_a, metadata = align
        try:
            import whisperx

            result = whisperx.align(
                segments, model_a, metadata, audio, dev, return_char_alignments=False,
            )
            return result.get("segments", segments)
        except Exception as e:  # noqa: BLE001
            last = i == len(devices) - 1
            if last:
                logger.warning(
                    "forced alignment failed on %s: %s — using native word timestamps", dev, e,
                )
                return segments
            logger.info("forced alignment failed on %s (%s) — retrying on %s", dev, e, devices[i + 1])
    return segments


class WhisperXBackend(ASRBackend):
    id = "whisperx"
    display_name = "WhisperX (faster-whisper + wav2vec2 forced alignment)"
    # CTranslate2 backend: CUDA fp16 or CPU int8 (see _pick_device). ROCm not
    # claimed — CTranslate2 has no upstream HIP build, so a ROCm host honestly
    # gets cpu_fallback rather than a false GPU promise.
    gpu_compat = ("cuda", "cpu")

    def __init__(self):
        self._model_name = os.environ.get("ASR_MODEL_WHISPERX", "large-v3")
        self._asr = None
        self._align_cache = {}  # language_code → (align_model, metadata)
        self._device, self._compute_type = self._pick_device()

    @staticmethod
    def _pick_device() -> tuple[str, str]:
        # CUDA fp16 when available; otherwise CPU int8 (fastest CPU path,
        # negligible WER regression vs fp32 for whisper-large-v3).
        # _ctranslate2_cuda_ok, not torch.cuda.is_available: ROCm torch also
        # answers True there, and CTranslate2 has no HIP backend (#1529).
        if _ctranslate2_cuda_ok():
            return "cuda", "float16"
        return "cpu", "int8"

    # Peak VRAM (GB) to load *and transcribe* whisper large-v3 per CTranslate2
    # compute type (weights + encoder/decoder workspace, with headroom). #723:
    # on an 8 GB card with the TTS model resident, loading fp16 large-v3 dies
    # as a *native* CUDA OOM abort — the process is killed, no Python
    # exception ever fires, and the UI reports "Can't reach the local
    # backend". The only defense is to never start that load, so the device
    # pick is re-checked against actually-free VRAM right before loading.
    _CUDA_VRAM_BUDGET_GB = {"float16": 5.0, "int8_float16": 3.5, "int8": 3.0}

    #: Budget multiplier by model size (budgets above are for large-v3).
    _MODEL_VRAM_SCALE = (
        ("large", 1.0), ("turbo", 0.55), ("medium", 0.5),
        ("small", 0.25), ("base", 0.15), ("tiny", 0.1),
    )

    @staticmethod
    def _free_vram_gb():
        """Device-wide free VRAM in GB (counts other processes), or None."""
        try:
            import torch
            if torch.cuda.is_available():
                free, _total = torch.cuda.mem_get_info()
                return free / 1024**3
        except Exception:  # noqa: BLE001 — preflight must never block ASR
            pass
        return None

    @classmethod
    def _model_scale(cls, model_name: str) -> float:
        name = (model_name or "").lower()
        for key, scale in cls._MODEL_VRAM_SCALE:
            if key in name:
                return scale
        return 1.0  # unknown → assume large

    def _degrade_for_vram(self, device: str, compute_type: str) -> tuple[str, str]:
        """Downgrade the CUDA compute type (or fall to CPU) if free VRAM can't
        hold the model — preventing the un-catchable native OOM abort (#723).
        Opt-out: OMNIVOICE_ASR_VRAM_PREFLIGHT=0."""
        if device != "cuda" or os.environ.get(
            "OMNIVOICE_ASR_VRAM_PREFLIGHT", "1"
        ).strip().lower() in ("0", "false", "no"):
            return device, compute_type
        free = self._free_vram_gb()
        if free is None:
            return device, compute_type
        scale = self._model_scale(self._model_name)
        candidates = list(self._CUDA_VRAM_BUDGET_GB)
        start = candidates.index(compute_type) if compute_type in candidates else 0
        for ct in candidates[start:]:
            if free >= self._CUDA_VRAM_BUDGET_GB[ct] * scale:
                if ct != compute_type:
                    logger.warning(
                        "whisperx VRAM preflight: %.1f GB free < %.1f GB needed "
                        "for %s %s — degrading to %s (#723)",
                        free, self._CUDA_VRAM_BUDGET_GB[compute_type] * scale,
                        self._model_name, compute_type, ct,
                    )
                return device, ct
        logger.warning(
            "whisperx VRAM preflight: %.1f GB free is too little for %s on CUDA "
            "(needs ≥%.1f GB even at int8) — using CPU int8 instead. Free VRAM "
            "(flush the TTS model, or close other GPU apps) for GPU-speed ASR, or "
            "set OMNIVOICE_ASR_VRAM_PREFLIGHT=0 to skip this check. (#723)",
            free, self._model_name,
            self._CUDA_VRAM_BUDGET_GB["int8"] * scale,
        )
        return "cpu", "int8"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        ct2_ok, ct2_detail = _ctranslate2_execstack_ok()
        if not ct2_ok:
            return False, f"whisperx cannot load CTranslate2: {ct2_detail}"
        try:
            import whisperx  # noqa: F401
        except ImportError as e:
            return False, f"whisperx not installed: {e}"
        except Exception as e:  # noqa: BLE001
            # The import can fail while loading a native dep — CTranslate2's .so
            # is rejected by hardened kernels / newer glibc with "cannot enable
            # executable stack" (#692), an OSError, not an ImportError. An
            # availability probe must REPORT 'unusable here', never raise, so
            # engine selection falls back instead of crashing the ASR preflight.
            return False, f"whisperx failed to load ({type(e).__name__}): {e}"
        return _ctranslate2_cudnn_ok()

    def ensure_loaded(self) -> None:
        # Surface a whisperx/CTranslate2/torch load failure at preflight (once,
        # with the real cause) instead of buried per-chunk and retried N times
        # (#578). Re-raises whatever `_ensure_asr` raises after its fp16→int8
        # and OOM→CPU fallbacks are exhausted.
        self._ensure_asr()

    def _ensure_asr(self):
        if self._asr is not None:
            return
        # Patch speechbrain's lazy-import guard BEFORE whisperx pulls in pyannote
        # → speechbrain, or a stray k2_fsa redirect import aborts ASR on Windows
        # (#630/#611/#647). No-op on macOS/Linux and when speechbrain is absent.
        _harden_speechbrain_lazy_imports()
        # #692: repair CTranslate2's exec-stack request before the import that
        # would be rejected by it. Memoized, so this is free after the probe.
        ct2_ok, ct2_detail = _ctranslate2_execstack_ok()
        if not ct2_ok:
            # ImportError (not RuntimeError): this IS a native-import failure,
            # and the sentinel lets load_active_asr_backend degrade to the next
            # engine instead of failing ASR wholesale (#1185).
            raise ImportError(f"whisperx cannot load CTranslate2: {ct2_detail}")
        import whisperx
        # #723: re-check the CUDA pick against *currently free* VRAM — the TTS
        # model may have claimed the card since __init__. A too-big load dies
        # as a native abort (whole process, no exception), so it must be
        # avoided up front rather than caught below.
        self._device, self._compute_type = self._degrade_for_vram(
            self._device, self._compute_type
        )
        logger.info(
            "whisperx loading ASR %s on %s (%s)",
            self._model_name, self._device, self._compute_type,
        )
        # PyTorch 2.6 flipped `torch.load(weights_only=True)` to default,
        # which breaks pyannote 3.x's VAD checkpoint (that whisperx ships):
        # each load surfaces a different missing global — `omegaconf.*`,
        # `typing.Any`, etc. The fix is to allowlist the pickle globals the
        # VAD file contains via `torch.serialization.add_safe_globals` so
        # the secure `weights_only=True` load path succeeds *without* us
        # disabling it.
        #
        # An earlier defensive layer (monkey-patching `torch.load` to force
        # `weights_only=False` for the duration of `whisperx.load_model`)
        # was removed in P0 Wave 1: it defeated PyTorch's secure unpickler
        # globally for any code that ran during that window, which is the
        # opposite of what the surrounding comment claimed. If a downstream
        # callee deserialised an attacker-controlled pickle in that window
        # it would have executed arbitrary code with no warning. The
        # allowlist below is the only correct mitigation; if pyannote ever
        # ships a checkpoint with a new pickle class, the load fails loudly
        # and we extend `_allow_vad_pickle_globals()`.
        self._allow_vad_pickle_globals()
        try:
            self._asr = whisperx.load_model(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
                # vad_method="silero" is the default; keep it so short gaps
                # get cleaned up before transcription.
            )
        except (ValueError, RuntimeError) as e:
            # #551: GPUs without efficient fp16 (older Maxwell/Pascal, GTX 16xx)
            # or a CTranslate2/cuDNN binary mismatch raise a *ValueError*
            # ("Requested float16 compute type, but the target device or backend
            # do not support efficient float16 computation") at load — not an
            # OOM, not a RuntimeError. Retry on the SAME device with the next
            # compute_type candidate (cuda: int8_float16 → int8) before touching
            # the OOM→CPU path, so we degrade rather than crash every chunk.
            if _is_compute_type_error(str(e)):
                candidates = _compute_type_candidates(self._device)
                try:
                    nxt = candidates[candidates.index(self._compute_type) + 1:]
                except ValueError:
                    nxt = [c for c in candidates if c != self._compute_type]
                for ct in nxt:
                    logger.warning(
                        "whisperx %s unsupported on %s — retrying with %s. Detail: %s",
                        self._compute_type, self._device, ct, e,
                    )
                    self._compute_type = ct
                    try:
                        self._asr = whisperx.load_model(
                            self._model_name,
                            device=self._device,
                            compute_type=self._compute_type,
                        )
                        return
                    except (ValueError, RuntimeError) as e2:
                        if _is_compute_type_error(str(e2)):
                            e = e2
                            continue
                        raise
                # Exhausted compute-type candidates on this device — re-raise.
                raise
            # CUDA OOM: a resident TTS model + the GPU worker pool can starve
            # VRAM on small (e.g. 8 GB laptop) GPUs, so loading large-v3 on
            # CUDA dies here — which previously surfaced as a bare 500 from
            # /dub/transcribe with no guidance. Fall back to CPU (slower, but
            # dubbing still works and keeps the same model/accuracy) instead.
            # Only triggers on a CUDA OOM, so the MPS/CPU paths are untouched.
            if self._device == "cuda" and "out of memory" in str(e).lower():
                logger.warning(
                    "whisperx CUDA OOM loading %s — retrying on CPU (slower). "
                    "Free VRAM (Flush the TTS model) for GPU-speed ASR. Detail: %s",
                    self._model_name, e,
                )
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 — cache clear is best-effort
                    pass
                self._device, self._compute_type = "cpu", "int8"
                self._asr = whisperx.load_model(
                    self._model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                )
            else:
                raise

    @staticmethod
    def _allow_vad_pickle_globals():
        """Register the pickle classes that pyannote's VAD checkpoint contains.

        Without this, PyTorch 2.6's secure unpickler refuses to load the file
        even if the call explicitly passes `weights_only=False` later — the
        allowlist is per-process and harmless to re-apply. Each class we add
        is one that has surfaced in the wild from pyannote/omegaconf/pytorch-
        lightning pickles; extending the list is safe.
        """
        try:
            import torch.serialization as _ts
        except Exception:
            return
        add = getattr(_ts, "add_safe_globals", None)
        if add is None:
            return  # older torch — secure unpickler didn't exist

        allow = []
        # omegaconf config containers + every node wrapper type the library
        # exposes. pyannote's VAD checkpoint pickles `ListConfig` /
        # `DictConfig` trees whose leaves are `AnyNode`/`ValueNode`/etc., so
        # allowlist the whole family in one pass rather than waiting for
        # users to hit each one in turn. All of these are pure metadata
        # containers — no executable side effects.
        try:
            import omegaconf.nodes as _ocn
            import omegaconf.base as _ocb
            from omegaconf.listconfig import ListConfig
            from omegaconf.dictconfig import DictConfig
            allow += [ListConfig, DictConfig]
            for _modname in ("nodes", "base"):
                _mod = _ocn if _modname == "nodes" else _ocb
                for _name in dir(_mod):
                    _obj = getattr(_mod, _name, None)
                    if isinstance(_obj, type) and _obj.__module__ == f"omegaconf.{_modname}":
                        allow.append(_obj)
        except Exception:
            pass
        # `EnumNode` references real enum classes at unpickle time; allow
        # the base Enum/IntEnum/Flag types so configs using enums load.
        try:
            import enum
            allow += [enum.Enum, enum.IntEnum, enum.Flag, enum.IntFlag]
        except Exception:
            pass
        # torch utility types that aren't in the secure unpickler's
        # default allowlist. `TorchVersion` is a `str` subclass that
        # pyannote/lightning serialise as metadata; `Size` is the shape
        # tuple type used in tensor metadata. Both are inert data.
        try:
            from torch.torch_version import TorchVersion
            import torch as _torch
            allow += [TorchVersion, _torch.Size]
        except Exception:
            pass
        # PyTorch Lightning serialises `hyper_parameters` as
        # `argparse.Namespace` (or an AttributeDict subclass thereof) so
        # configs roundtrip. Allowlist the Namespace constructor — it is
        # just an attribute bag with no executable side effects.
        try:
            import argparse
            allow += [argparse.Namespace]
        except Exception:
            pass
        # pyannote-specific metadata classes that travel with the VAD
        # checkpoint. Only the inert data-only types are allowlisted —
        # the `Model` / `Task` / `Dataset` classes from the same modules
        # do real work in `__init__` and stay off the allowlist.
        try:
            from pyannote.audio.core.model import Introspection, Output
            from pyannote.audio.core.task import Problem, Resolution, Specifications
            allow += [Introspection, Output, Problem, Resolution, Specifications]
        except Exception:
            pass
        # Python typing primitives that show up in config annotations.
        try:
            import typing
            allow += [typing.Any]
        except Exception:
            pass
        # pytorch-lightning's OrderedDict-backed state dict helpers.
        try:
            from collections import OrderedDict, defaultdict
            allow += [OrderedDict, defaultdict]
        except Exception:
            pass
        # Plain-data builtins. pyannote's VAD checkpoint pickles config
        # entries that resolve to bare builtin constructors (`GLOBAL list`,
        # `GLOBAL int`, …) and the secure unpickler refuses each one
        # without an explicit allowlist. These constructors only build
        # inert data primitives — no side effects, no code paths — so the
        # full set is safe to allowlist together, which avoids users
        # hitting them one-at-a-time as the checkpoint deserialises.
        allow += [
            list, dict, tuple, set, frozenset,
            int, float, bool, str, bytes, bytearray, complex,
            type(None), slice, range,
        ]
        # numpy scalar/array constructors that show up in pyannote configs
        # (sample rates, hop sizes saved as numpy ints/floats). Each is a
        # pure data type — safe to allowlist.
        try:
            import numpy as _np
            allow += [
                _np.ndarray, _np.dtype,
                _np.int8, _np.int16, _np.int32, _np.int64,
                _np.uint8, _np.uint16, _np.uint32, _np.uint64,
                _np.float16, _np.float32, _np.float64,
                _np.bool_, _np.complex64, _np.complex128,
            ]
            # numpy.core was renamed to numpy._core in 1.25+. Both modules
            # expose the same reconstruct helpers; allowlist whichever ships.
            for _modname in ("numpy._core.multiarray", "numpy.core.multiarray"):
                try:
                    _mod = __import__(_modname, fromlist=["_reconstruct", "scalar"])
                    for _attr in ("_reconstruct", "scalar"):
                        _fn = getattr(_mod, _attr, None)
                        if _fn is not None:
                            allow.append(_fn)
                except Exception:
                    pass
        except Exception:
            pass
        # pathlib types — config files sometimes save cache directories as
        # Path objects so the checkpoint can be relocated.
        try:
            import pathlib
            allow += [
                pathlib.PurePath, pathlib.PurePosixPath, pathlib.PureWindowsPath,
                pathlib.Path, pathlib.PosixPath, pathlib.WindowsPath,
            ]
        except Exception:
            pass
        if allow:
            try:
                add(allow)
            except Exception as e:
                logger.debug("add_safe_globals failed (harmless): %s", e)

    def _get_align(self, language_code: str):
        """Lazy-load the wav2vec2 alignment model for this language. WhisperX
        bundles aligners for ~20 major languages; for the others we fall back
        to faster-whisper's native word timestamps (already in result)."""
        return load_align_model(language_code, self._device)

    def supports_translation(self) -> bool:
        return whisper_checkpoint_translates(self._model_name)

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, task: str = "transcribe") -> dict:
        import whisperx  # used for whisperx.align() below
        self._ensure_asr()
        logger.info("whisperx transcribing %s (word_timestamps=%s)", audio_path, word_timestamps)
        # Decode via VoiceStudio's validated ffmpeg, NOT whisperx.load_audio's bare
        # "ffmpeg" PATH lookup which yields [WinError 193] -> "no segments" on
        # Windows (#479). Same 16 kHz mono s16le array whisperx expects.
        audio = _decode_audio_16k_mono(audio_path)
        try:
            # whisperx's pipeline takes language/task per call; prompt and
            # temperature are fixed load-time asr_options, so this backend
            # does not advertise them (the OpenAI route passes only what a
            # backend's signature accepts).
            result = self._asr.transcribe(
                audio, **whisper_request_options(language, None, None, task),
            )
        except IndexError:
            # WhisperX pipeline crashes with IndexError if VAD produces 0 segments
            logger.info("whisperx transcribe threw IndexError (likely 0 VAD segments). Returning empty result.")
            result = {"segments": [], "language": language or "en"}
            
        lang = result.get("language", "en")

        # Forced alignment when available — drastically improves word boundary
        # accuracy (±10-30 ms vs Whisper's ±100-300 ms). Skip for rare-language
        # audio where no wav2vec2 aligner exists.
        if word_timestamps:
            align = self._get_align(lang)
            if align is not None:
                model_a, metadata = align
                try:
                    result = whisperx.align(
                        result["segments"], model_a, metadata, audio,
                        self._device, return_char_alignments=False,
                    )
                except Exception as e:
                    logger.warning("whisperx alignment failed: %s — using raw timestamps", e)

        # Normalise to the shape segment_transcript(...) expects: chunks +
        # segments + language metadata. whisperx's post-align result has
        # `segments` with `words: [{word, start, end, score}]`.
        segments = result.get("segments", [])
        chunks = [
            {"text": seg.get("text", ""),
             "timestamp": (seg.get("start"), seg.get("end"))}
            for seg in segments
        ]
        return {
            "chunks": chunks,
            "segments": [
                {
                    "text": seg.get("text", ""),
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "words": seg.get("words", []) if word_timestamps else [],
                }
                for seg in segments
            ],
            "language": lang,
        }

    def unload(self) -> None:
        self._asr = None
        self._align_cache.clear()
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

# ── Faster-Whisper (cross-platform fallback) ────────────────────────────────


class FasterWhisperBackend(ASRBackend):
    id = "faster-whisper"
    display_name = "Faster-Whisper (CTranslate2 — Linux/Windows/macOS)"
    # CTranslate2: CUDA or CPU (no upstream ROCm/HIP build — see WhisperX note).
    gpu_compat = ("cuda", "cpu")

    def __init__(self, model_name: str | None = None):
        # Defaulting to the CTranslate2-converted large-v3 repo. Matches
        # KNOWN_MODELS in api/routers/setup.py so the first-run wizard
        # downloads what the backend will actually load.
        self._model_name = model_name or faster_whisper_model_id()
        self._model = None  # lazy — first transcribe() loads weights
        # Set by _ensure_model() to the device/compute_type that actually loaded
        # (after the #551 compute_type / #255 OOM→CPU fallback chain).
        self._device: str | None = None
        self._compute_type: str | None = None
        self._fallback_reason: str | None = None
        self._fallback_stage: str | None = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        ct2_ok, ct2_detail = _ctranslate2_execstack_ok()
        if not ct2_ok:
            return False, f"faster-whisper cannot load CTranslate2: {ct2_detail}"
        try:
            import faster_whisper  # noqa: F401
        except ImportError as e:
            return False, f"faster-whisper not installed: {e}"
        except Exception as e:  # noqa: BLE001
            # faster-whisper pulls in CTranslate2, whose .so is rejected by
            # hardened kernels / newer glibc ("cannot enable executable stack",
            # #692) — an OSError. Report unavailable so we fall back, not crash.
            return False, f"faster-whisper failed to load ({type(e).__name__}): {e}"
        return _ctranslate2_cudnn_ok()

    def _ensure_model(self):
        if self._model is not None:
            return
        ct2_ok, ct2_detail = _ctranslate2_execstack_ok()  # #692, see WhisperX
        if not ct2_ok:
            raise ImportError(f"faster-whisper cannot load CTranslate2: {ct2_detail}")
        from faster_whisper import WhisperModel
        # Device / compute-type auto-pick:
        #   - CUDA present → GPU fp16
        #   - Apple Silicon / CPU → CPU int8 (fastest on CPU, negligible
        #     WER regression vs fp32 for whisper-large-v3)
        device, compute_type = "cpu", "int8"
        # _ctranslate2_cuda_ok, not torch.cuda.is_available: ROCm torch also
        # answers True there, and CTranslate2 has no HIP backend (#1529).
        if _ctranslate2_cuda_ok():
            device, compute_type = "cuda", "float16"
        logger.info(
            "faster-whisper loading %s on %s (%s)",
            self._model_name, device, compute_type,
        )
        # Try the per-device compute_type chain (cuda: float16 → int8_float16 →
        # int8; cpu: int8 → float32). A GPU without efficient fp16 (older
        # Maxwell/Pascal, GTX 16xx, or a CTranslate2/cuDNN mismatch) raises a
        # *ValueError* at construction (#551) — degrade to the next candidate
        # instead of failing every chunk. A genuine CUDA OOM falls back to CPU
        # (slower, same model/accuracy), preserving the existing #255 behaviour.
        candidates = _compute_type_candidates(device)
        if compute_type in candidates:
            candidates = candidates[candidates.index(compute_type):]
        last_err: Exception | None = None
        while True:
            for ct in candidates:
                try:
                    self._model = WhisperModel(
                        self._model_name, device=device, compute_type=ct
                    )
                    self._device, self._compute_type = device, ct
                    return
                except (ValueError, RuntimeError) as e:
                    last_err = e
                    if _is_compute_type_error(str(e)):
                        logger.warning(
                            "faster-whisper %s unsupported on %s — trying next "
                            "compute_type. Detail: %s", ct, device, e,
                        )
                        continue
                    if device == "cuda" and "out of memory" in str(e).lower():
                        # Stop scanning GPU candidates; fall back to CPU below.
                        break
                    raise
            # Exhausted candidates for this device. If we were on CUDA and the
            # last failure was an OOM, retry on CPU with its candidates (#255).
            if device == "cuda" and last_err is not None and (
                "out of memory" in str(last_err).lower()
            ):
                logger.warning(
                    "faster-whisper CUDA OOM loading %s — retrying on CPU "
                    "(slower). Free VRAM (Flush the TTS model) for GPU-speed "
                    "ASR. Detail: %s", self._model_name, last_err,
                )
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 — cache clear is best-effort
                    pass
                device = "cpu"
                self._fallback_reason = "CUDA memory was exhausted while loading the engine"
                self._fallback_stage = "model_load"
                candidates = _compute_type_candidates(device)
                compute_type = candidates[0]
                continue
            # All candidates exhausted (and no OOM→CPU retry available) — surface
            # the last error.
            raise last_err

    def supports_translation(self) -> bool:
        return whisper_checkpoint_translates(self._model_name)

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, initial_prompt: str | None = None,
                   temperature: float | None = None,
                   task: str = "transcribe") -> dict:
        self._ensure_model()
        logger.info(
            "faster-whisper transcribing %s (word_timestamps=%s)",
            audio_path, word_timestamps,
        )
        # faster-whisper returns a generator of Segment objects + an Info
        # struct. Materialise the generator so downstream consumers can
        # index / re-iterate.
        from services.performance_profiles import asr_decode_defaults

        segments_iter, info = self._model.transcribe(
            audio_path,
            word_timestamps=word_timestamps,
            vad_filter=True,  # built-in Silero VAD — cleaner segment starts
            **asr_decode_defaults(),
            **whisper_request_options(language, initial_prompt, temperature, task),
        )
        segments = list(segments_iter)
        # Normalise to the shape segment_transcript(...) expects: a dict with
        # `chunks` (for backwards compat with mlx output) AND `segments` +
        # `language` (so callers that peek at language metadata keep working).
        chunks = [
            {"text": seg.text, "timestamp": (seg.start, seg.end)}
            for seg in segments
        ]
        out = {
            "chunks": chunks,
            "segments": [
                {
                    "text": seg.text,
                    "start": seg.start,
                    "end": seg.end,
                    "words": (
                        [
                            {
                                "word": w.word,
                                "start": w.start,
                                "end": w.end,
                                "probability": w.probability,
                            }
                            for w in (seg.words or [])
                        ]
                        if word_timestamps
                        else []
                    ),
                }
                for seg in segments
            ],
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
        }
        return out

    def unload(self) -> None:
        # #memory: this cleared self._asr — an attribute FasterWhisperBackend
        # never assigns — so the actual model in self._model was never freed and
        # a warm faster-whisper stayed resident for the life of the process.
        # Clear the real handle so the model is released.
        self._model = None
        self._asr = None  # harmless if a subclass ever used it; keeps idempotence
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# ── MLX Whisper (Apple Silicon optional) ────────────────────────────────────

# Default model for general transcription (dub pipeline etc.)
_MLX_MODEL_DEFAULT = "mlx-community/whisper-large-v3-mlx"
# Turbo model for dictation / capture — 5× faster, 0.8B params vs 1.5B.
_MLX_MODEL_TURBO = "mlx-community/whisper-large-v3-turbo"


class MLXWhisperBackend(ASRBackend):
    id = "mlx-whisper"
    display_name = "MLX Whisper (Apple Silicon CoreML)"
    gpu_compat = ("mps", "cpu")

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.environ.get(
            "ASR_MODEL", _MLX_MODEL_DEFAULT,
        )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # #390: shared platform gate FIRST — one rule for MLX-Audio + MLX-Whisper.
        # Returns False on Linux/Windows/mac-Intel before any package import, so
        # a stray mlx-whisper wheel never reports available or advertises `mps`.
        from core.device_caps import mlx_supported
        ok, why = mlx_supported()
        if not ok:
            return False, why
        try:
            import mlx_whisper  # noqa: F401
            return True, "ready"
        # Catch OSError/RuntimeError too, not just ImportError: in a
        # PyInstaller bundle mlx's native dylib/metallib can fail to load
        # even when the package imports, raising OSError/RuntimeError. We must
        # report unavailable (so the picker falls back) rather than crash the
        # registry scan (Wave 4.4).
        except (ImportError, OSError, RuntimeError) as e:
            return False, f"mlx-whisper unavailable: {e}"

    def supports_translation(self) -> bool:
        return whisper_checkpoint_translates(self._model_name)

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, initial_prompt: str | None = None,
                   temperature: float | None = None,
                   task: str = "transcribe") -> dict:
        import mlx_whisper
        logger.info(
            "MLX Whisper transcribing %s (model=%s, word_timestamps=%s)",
            audio_path, self._model_name, word_timestamps,
        )
        # Decode once here, rather than handing mlx_whisper a path. Given a
        # path it calls whisper.audio.load_audio, which shells out to a bare
        # "ffmpeg" PATH lookup -- the same lookup the WhisperX backend was
        # moved off in #479, and one that cannot find the bundled
        # imageio-ffmpeg binary because that is named ffmpeg-<plat>-vN. On a
        # clean from-source install with no system ffmpeg this fails the whole
        # request with [Errno 2] No such file or directory: ffmpeg.
        #
        # The aligner below already used the validated decoder, so this was one
        # call site out of two in the same method. Reusing that decode also
        # stops the file being decoded twice per transcription.
        audio = _decode_audio_16k_mono(audio_path)
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._model_name,
            word_timestamps=word_timestamps,
            **whisper_request_options(language, initial_prompt, temperature, task),
        )
        # Forced alignment, same as WhisperX (#1127). On Apple Silicon this
        # backend replaces WhisperX for dubbing — CTranslate2 has no Metal
        # build, so WhisperX transcribes on the CPU while this runs the *same*
        # whisper-large-v3 on the GPU. But lip-sync accuracy depends on
        # wav2vec2 word boundaries, not just on being fast, so we keep them:
        # Whisper's own timestamps are ±100-300 ms, the aligner's are ±10-30 ms.
        # Degrades gracefully — a language with no aligner keeps MLX's native
        # word timings rather than failing.
        if word_timestamps and result.get("segments"):
            result["segments"] = forced_align(
                result["segments"],
                audio,
                result.get("language", "en"),
            )
        # Normalise to the `chunks` shape the rest of the pipeline expects.
        if "segments" in result:
            result["chunks"] = [
                {"text": seg.get("text", ""),
                 "timestamp": (seg.get("start"), seg.get("end"))}
                for seg in result["segments"]
            ]
        return result

    def warmup(self) -> None:
        """Eagerly load model weights into memory so first transcribe is instant.

        mlx_whisper internally caches via a class-level ModelHolder singleton.
        Calling ``load_model`` triggers the download (if needed) and loads
        weights onto the GPU — subsequent transcribe() calls hit the warm cache.
        """
        import time
        t0 = time.perf_counter()
        try:
            from mlx_whisper.transcribe import ModelHolder
            import mlx.core as mx
            # load_model populates the class-level singleton; after this call
            # the model is resident in unified memory.
            ModelHolder.get_model(self._model_name, dtype=mx.float16)
            dt = time.perf_counter() - t0
            logger.info("MLX Whisper model '%s' warmed up in %.1fs", self._model_name, dt)
        except Exception as e:
            dt = time.perf_counter() - t0
            logger.warning("MLX Whisper warmup failed after %.1fs: %s", dt, e)


# ── PyTorch Whisper fallback (CUDA / CPU via pipeline) ─────────────────────


class PyTorchWhisperBackend(ASRBackend):
    id = "pytorch-whisper"
    display_name = "PyTorch Whisper (CUDA / CPU via transformers pipeline)"
    # Pure transformers pipeline → runs wherever torch does (CUDA, MPS, CPU).
    # ROCm-via-HIP would also work but is left unclaimed pending verification.
    gpu_compat = ("cuda", "mps", "cpu")

    def __init__(self, asr_pipe=None):
        # Reuses the `_asr_pipe` attached to the TTS model when available.
        self._pipe = asr_pipe

    # Free VRAM needed on CUDA, where _ensure_pipe loads fp16 weights: the
    # weights (parameters x 2 bytes), the batch-16 decode workspace, and
    # headroom. Loading onto a nearly full card works, then the first
    # transcribe fails with a CUDA OOM and yields zero segments, so the device
    # pick checks this against actually-free VRAM first.
    #
    # #2041: this was a flat 5.0 GB for every model, sized for full large-v3.
    # The default is large-v3-turbo (0.81B parameters, about 1.6 GB in fp16),
    # so a 6 GB card with nothing else resident reported 5.0 GB free and was
    # sent to CPU every time, although CUDA transcribed the same audio in 37 s.
    _CUDA_VRAM_BUDGET_GB = 5.0  # full large-v3, and any model not listed below
    # fp16 weights (GB) of the OpenAI Whisper checkpoints, by exact repo id.
    # Anything else, including a fine-tune or a custom repo whose name happens
    # to contain "small" or "turbo", keeps the conservative 5.0 GB budget.
    _FP16_WEIGHTS_GB = {
        "openai/whisper-large-v3-turbo": 1.6,
        "openai/whisper-large-v3": 3.1,
        "openai/whisper-large-v2": 3.1,
        "openai/whisper-large": 3.1,
        "openai/whisper-medium": 1.5,
        "openai/whisper-medium.en": 1.5,
        "openai/whisper-small": 0.5,
        "openai/whisper-small.en": 0.5,
        "openai/whisper-base": 0.15,
        "openai/whisper-base.en": 0.15,
        "openai/whisper-tiny": 0.08,
        "openai/whisper-tiny.en": 0.08,
    }
    _CUDA_WORKSPACE_GB = 1.5  # batch 16 x 15 s chunks
    _CUDA_HEADROOM_GB = 0.5

    @classmethod
    def _cuda_budget_gb(cls, model_name: str) -> float:
        """Free VRAM (GB) this model needs on CUDA; never above the 5.0 GB
        that full large-v3 was measured to need."""
        weights_gb = cls._FP16_WEIGHTS_GB.get((model_name or "").strip().lower())
        if weights_gb is None:
            return cls._CUDA_VRAM_BUDGET_GB
        return min(
            cls._CUDA_VRAM_BUDGET_GB,
            weights_gb + cls._CUDA_WORKSPACE_GB + cls._CUDA_HEADROOM_GB,
        )

    @staticmethod
    def _model_name() -> str:
        return os.environ.get(
            "OMNIVOICE_PYTORCH_ASR_MODEL", "openai/whisper-large-v3-turbo"
        )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import transformers  # noqa: F401
            return True, "ready"
        except ImportError as e:
            return False, f"transformers not installed: {e}"

    @classmethod
    def _pick_device(cls, model_name: str | None = None) -> str:
        from services.model_manager import get_best_device

        device = str(get_best_device())
        if not device.startswith("cuda") or os.environ.get(
            "OMNIVOICE_ASR_VRAM_PREFLIGHT", "1"
        ).strip().lower() in ("0", "false", "no"):
            return device
        try:
            import torch

            free, _total = torch.cuda.mem_get_info()
            free_gb = free / 1024**3
        except Exception:  # noqa: BLE001 — an unavailable probe must not block ASR
            return device
        model_name = model_name or cls._model_name()
        budget_gb = cls._cuda_budget_gb(model_name)
        if free_gb >= budget_gb:
            return device
        logger.warning(
            "PyTorch Whisper VRAM preflight: %.1f GB free < %.1f GB needed "
            "for %s on CUDA — using CPU instead. Close other GPU apps or Flush "
            "models to restore GPU-speed ASR, or set "
            "OMNIVOICE_ASR_VRAM_PREFLIGHT=0 to skip this check.",
            free_gb,
            budget_gb,
            model_name,
        )
        return "cpu"

    def ensure_loaded(self) -> None:
        # Unlike the CTranslate2 backends, this fallback used to inherit the
        # protocol's no-op loader. Import/model failures therefore appeared on
        # every chunk as the misleading "produced no segments" result. Load it
        # once during the stream preflight so the real failure is reported once.
        self._ensure_pipe()

    def _ensure_pipe(self):
        if self._pipe is not None:
            return
        # Build a standalone transformers Whisper pipeline on demand. This runs
        # on PyTorch's own stack (cuDNN 9 ships with torch), so it works as a
        # fallback on machines where WhisperX / faster-whisper can't load
        # cuDNN 8 (the `cudnn_ops_infer64_8.dll` failure, issue #255) — and it
        # needs neither OMNIVOICE_PRELOAD_TTS_ASR=1 nor a loaded TTS model.
        # When the TTS model already has an ASR head, dub_core passes it via the
        # constructor and this path is skipped.
        import torch
        from transformers import pipeline as hf_pipeline
        model_name = self._model_name()
        device = self._pick_device(model_name)
        asr_dtype = torch.float16 if str(device).startswith("cuda") else torch.float32
        logger.info(
            "PyTorchWhisperBackend: loading standalone ASR pipeline %s on %s",
            model_name, device,
        )
        try:
            self._pipe = hf_pipeline(
                "automatic-speech-recognition",
                model=model_name,
                dtype=asr_dtype,
                # `device_map="cpu"` only controls weight placement; the
                # pipeline can still choose CUDA as its execution device.
                # `device` is the pipeline-level contract and keeps the
                # low-VRAM fallback entirely on CPU.
                device=device,
            )
        except Exception as e:
            # Pipeline construction can fail for OOM, an unreadable checkpoint,
            # missing packages or networking. Do not inject AutoFeatureExtractor
            # into every exception: that makes the failure classifier invent a
            # dependency mismatch and recommend an unrelated reinstall (#2128).
            raise RuntimeError(
                f"PyTorch Whisper ASR initialization failed: {type(e).__name__}: {e}"
            ) from e

    #: Batch sizes to try on CUDA, largest first. The VRAM preflight only sizes
    #: the *weights*; generation adds an encoder/decoder workspace that scales
    #: with the batch, and `return_timestamps="word"` keeps every layer's
    #: cross-attention for the whole batch — gigabytes at batch 16. A card with
    #: room for the model can therefore still OOM at the first transcribe, which
    #: used to lose that chunk entirely (the dub retried the same batch size and
    #: gave up, leaving a hole in the transcript). Step down, then use CPU.
    _CUDA_BATCH_LADDER = (16, 4, 1)
    _CUDA_BATCH_LADDER_WORD_TS = (8, 2, 1)

    @staticmethod
    def _is_oom(exc: BaseException) -> bool:
        try:
            import torch

            if isinstance(exc, torch.cuda.OutOfMemoryError):
                return True
        except Exception:  # noqa: BLE001 — classification must not raise
            pass
        return "out of memory" in str(exc).lower()

    def _rebuild_on_cpu(self) -> None:
        """Move the existing pipeline to CPU without resolving any model files."""
        import torch

        # Keep the loaded checkpoint, tokenizer and feature extractor. Looking
        # up the default model here could download a different model offline.
        self._pipe.model.to(device="cpu", dtype=torch.float32)
        self._pipe.device = torch.device("cpu")
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass  # Some builds have no CUDA cache to release.

    def supports_translation(self) -> bool:
        # A reused TTS-model ASR head may differ from the env default: ask the
        # loaded pipeline first.
        loaded = getattr(getattr(self._pipe, "model", None), "name_or_path", None)
        return whisper_checkpoint_translates(loaded or self._model_name())

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, task: str = "transcribe") -> dict:
        import soundfile as sf
        self._ensure_pipe()
        # transformers' Whisper generate() takes language/task; a prompt needs
        # tokenizer-specific prompt_ids, so it is not advertised here.
        generate_kwargs = whisper_request_options(language, None, None, task)
        # #2039: libsndfile cannot open MP4/M4A (AAC), which /transcribe and
        # the MCP tool both accept. Those decode through the validated ffmpeg
        # path, which resamples to 16 kHz properly. Anything soundfile can
        # read keeps its native rate, so the pipeline's band-limited
        # resampler does the conversion rather than a linear interpolation.
        try:
            audio_np, sr = sf.read(audio_path, dtype="float32")
        except Exception:
            audio_np, sr = _decode_audio_16k_mono(audio_path), 16000
        if audio_np.ndim > 1:
            audio_np = audio_np.mean(axis=1)

        def _run(batch_size: int):
            return self._pipe(
                {"array": audio_np, "sampling_rate": sr},
                return_timestamps="word" if word_timestamps else True,
                chunk_length_s=15,
                batch_size=batch_size,
                **({"generate_kwargs": generate_kwargs} if generate_kwargs else {}),
            )

        if self._on_cuda():
            ladder = (
                self._CUDA_BATCH_LADDER_WORD_TS if word_timestamps
                else self._CUDA_BATCH_LADDER
            )
            for i, bs in enumerate(ladder):
                try:
                    result = _run(bs)
                    break
                except Exception as e:  # noqa: BLE001 — only OOM is retryable
                    if not self._is_oom(e):
                        raise
                    try:
                        import torch

                        torch.cuda.empty_cache()
                    except Exception:  # noqa: BLE001
                        pass
                    if i + 1 < len(ladder):
                        logger.warning(
                            "PyTorch Whisper CUDA OOM at batch_size=%d — "
                            "retrying at %d. Free VRAM (Flush models, close "
                            "other GPU apps) for full-speed ASR.",
                            bs, ladder[i + 1],
                        )
                        continue
                    # Smallest batch still OOMs: finish on CPU rather than
                    # return an empty chunk the caller cannot distinguish
                    # from silence.
                    logger.warning(
                        "PyTorch Whisper CUDA OOM even at batch_size=1 — "
                        "transcribing on CPU (slower, same model). Detail: %s", e,
                    )
                    self._rebuild_on_cpu()
                    result = _run(2)
        else:
            result = _run(2)
        return result if isinstance(result, dict) else {"chunks": [], "raw": result}

    def _on_cuda(self) -> bool:
        """Whether the built pipeline actually sits on a CUDA device.

        `torch.cuda.is_available()` is the wrong question: `_pick_device()` may
        have chosen CPU on a CUDA host (low free VRAM), and a CPU pipeline must
        not be handed a CUDA-sized batch.
        """
        try:
            device = getattr(self._pipe, "device", None)
            return "cuda" in str(device).lower()
        except Exception:  # noqa: BLE001
            return False


# ── NeMo Parakeet TDT (NVIDIA — Open ASR Leaderboard SOTA, 25 langs) ────────


class NeMoASRBackend(ASRBackend):
    """NVIDIA Parakeet TDT via NeMo toolkit.

    FastConformer encoder + Token-and-Duration Transducer decoder.
    Beats Whisper large-v3 on English benchmarks (~6% WER).
    Supports 25 (mostly European) languages with auto language detection.
    CUDA or CPU — parakeet-tdt-0.6b-v3 measured RTF 0.08–0.23 on an Apple
    Silicon M2 *CPU* (2026-07-02), ~20× faster than faster-whisper large-v3
    int8 on the same host, so the old hard CUDA gate was a false claim.
    """
    id = "nemo-parakeet"
    gpu_compat = ("cuda", "cpu")
    display_name = "Parakeet TDT (NVIDIA NeMo — 25 langs, CUDA/CPU)"

    def __init__(self):
        self._model_name = os.environ.get(
            "ASR_MODEL_NEMO", "nvidia/parakeet-tdt-0.6b-v3"
        )
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # No CUDA gate: the 0.6B TDT model is comfortably faster than realtime
        # on CPU (see class docstring), so availability is a pure dependency
        # check and engine_routing picks the effective device from gpu_compat.
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, "PyTorch not installed"
        try:
            import nemo.collections.asr  # noqa: F401
            return True, "ready"
        except ImportError as e:
            return False, f"nemo_toolkit[asr] not installed: {e}"

    def _ensure_model(self):
        if self._model is not None:
            return
        import nemo.collections.asr as nemo_asr
        logger.info("NeMo loading %s", self._model_name)
        self._model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=self._model_name
        )

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        self._ensure_model()
        logger.info(
            "NeMo Parakeet transcribing %s (word_timestamps=%s)",
            audio_path, word_timestamps,
        )
        outputs = self._model.transcribe(
            [audio_path], timestamps=word_timestamps
        )
        # NeMo returns a list of Hypothesis objects with .text and optional
        # .timestep / .alignments. Normalise to VoiceStudio's expected shape.
        hyp = outputs[0] if outputs else None
        if hyp is None:
            return {"chunks": [], "segments": [], "language": "en"}

        text = hyp.text if hasattr(hyp, "text") else str(hyp)

        # Extract word-level timestamps if available
        words = []
        segments_out = []
        if word_timestamps and hasattr(hyp, "timestep") and hyp.timestep:
            try:
                # NeMo timestep format varies by model version
                ts = hyp.timestep
                if isinstance(ts, dict) and "word" in ts:
                    for w in ts["word"]:
                        words.append({
                            "word": w.get("char", w.get("word", "")),
                            "start": w.get("start_offset", 0),
                            "end": w.get("end_offset", 0),
                        })
            except Exception as e:
                logger.debug("NeMo timestamp extraction: %s", e)

        # Build a single segment from the full transcription
        # (NeMo doesn't natively split into VAD segments like Whisper)
        if text.strip():
            segments_out.append({
                "text": text,
                "start": words[0]["start"] if words else 0.0,
                "end": words[-1]["end"] if words else None,
                "words": words,
            })

        chunks = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments_out
        ]
        return {
            "chunks": chunks,
            "segments": segments_out,
            "language": "en",  # Parakeet v3 auto-detects but doesn't expose it cleanly
        }

    def unload(self) -> None:
        self._model = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# ── Parakeet TDT v3 via MLX (Apple Silicon — the mac Parakeet tier) ─────────

# Default model for the parakeet-mlx backend. ~1.2 GB download, ~2 GB unified
# memory at runtime, 25 European languages, TDT token/word timestamps.
_PARAKEET_MLX_DEFAULT = "mlx-community/parakeet-tdt-0.6b-v3"


class ParakeetMLXBackend(ASRBackend):
    """NVIDIA Parakeet TDT v3 on Apple Silicon via MLX (senstella/parakeet-mlx).

    Gives macs the Parakeet tier that CUDA/CPU users already have through
    sherpa-onnx / NeMo: 25 European languages, TDT token timestamps (so word
    timing comes from the decoder itself — no wav2vec2 alignment pass needed),
    ~2 GB unified memory, dictation-grade speed on the GPU. Unlike the
    nemo-parakeet backend it needs no nemo_toolkit (whose transformers pin
    conflicts with ours) — parakeet-mlx is a small pure-Python package on top
    of mlx, installed by default on Apple Silicon source installs.
    """
    id = "parakeet-mlx"
    display_name = "Parakeet TDT v3 (MLX — Apple Silicon, 25 langs)"
    # MLX runs on the unified-memory GPU only; there is no meaningful CPU tier
    # (is_available hard-gates on Apple Silicon via mlx_supported()).
    gpu_compat = ("mps",)

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.environ.get(
            "ASR_MODEL_PARAKEET_MLX", _PARAKEET_MLX_DEFAULT,
        )
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Shared platform gate FIRST — one rule for every MLX engine (#390).
        # Returns False on Linux/Windows/mac-Intel before any package import.
        from core.device_caps import mlx_supported
        ok, why = mlx_supported()
        if not ok:
            return False, why
        try:
            import parakeet_mlx  # noqa: F401
            return True, "ready"
        # OSError/RuntimeError too, not just ImportError: in a PyInstaller
        # bundle mlx's native dylib/metallib can fail to load even when the
        # package imports (same guard as MLXWhisperBackend).
        except (ImportError, OSError, RuntimeError) as e:
            return False, f"parakeet-mlx unavailable: {e}"

    def _ensure_model(self):
        if self._model is not None:
            return
        import parakeet_mlx
        logger.info("parakeet-mlx loading %s", self._model_name)
        self._model = parakeet_mlx.from_pretrained(self._model_name)

    def ensure_loaded(self) -> None:
        self._ensure_model()

    @staticmethod
    def _tokens_to_words(tokens) -> list[dict]:
        """Merge parakeet-mlx AlignedTokens (subword pieces; a leading space
        marks a word start) into whisper-shaped word dicts."""
        words: list[dict] = []
        for tok in tokens:
            text = tok.text or ""
            if not text.strip():
                continue
            if text.startswith(" ") or not words:
                words.append({
                    "word": text,
                    "start": float(tok.start),
                    "end": float(tok.end),
                })
            else:
                words[-1]["word"] += text
                words[-1]["end"] = float(tok.end)
        for w in words:
            w["word"] = w["word"].strip()
        return words

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None) -> dict:
        self._ensure_model()
        logger.info(
            "parakeet-mlx transcribing %s (model=%s, word_timestamps=%s)",
            audio_path, self._model_name, word_timestamps,
        )
        # chunk_duration bounds unified-memory use on long files (the
        # upstream-recommended long-audio setting); short capture buffers and
        # bounded dub chunks are unaffected.
        result = self._model.transcribe(audio_path, chunk_duration=120.0)

        # Map AlignedResult (sentences → subword tokens with start/end) to the
        # repo's standard shape: segments/words dicts + `chunks`, like the
        # other backends.
        segments_out = []
        for sent in result.sentences:
            text = (sent.text or "").strip()
            if not text:
                continue
            seg = {
                "text": text,
                "start": float(sent.start),
                "end": float(sent.end),
                "words": self._tokens_to_words(sent.tokens) if word_timestamps else [],
            }
            segments_out.append(seg)

        chunks = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments_out
        ]
        return {
            "text": (result.text or "").strip(),
            "chunks": chunks,
            "segments": segments_out,
            # Parakeet v3 auto-detects among its 25 languages but does not
            # expose the pick — report the caller's requested language when
            # given, else None. Never hardcode 'en': consumers treat this
            # value as detected truth (aligner pick, UI badge), and this
            # backend serves 25 languages, not one.
            "language": language,
        }

    def unload(self) -> None:
        self._model = None
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()  # release MLX's unified-memory buffer cache
        except Exception:  # noqa: BLE001 — best-effort; absent on older mlx
            pass


# ── Moonshine (edge-optimized, variable-length — from ASR Leaderboard) ─────


class MoonshineASRBackend(ASRBackend):
    """Moonshine ASR via moonshine-voice or ONNX runtime.

    Optimized for edge/CPU deployment. Variable-length processing
    (no 30s padding waste like Whisper). Sub-200ms latency.
    Great for live capture and CPU-only environments.
    """
    id = "moonshine"
    gpu_compat = ("cpu",)  # edge/CPU-optimized by design
    display_name = "Moonshine (edge-optimized, ONNX)"

    def __init__(self):
        self._model_name = os.environ.get(
            "ASR_MODEL_MOONSHINE", "moonshine/base"
        )
        self._transcriber = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import moonshine_onnx  # noqa: F401
            return True, "ready (moonshine_onnx)"
        except ImportError:
            pass
        try:
            from moonshine_voice import Transcriber  # noqa: F401
            return True, "ready (moonshine_voice)"
        except ImportError:
            pass
        return False, (
            "moonshine not installed. Install with: "
            "uv pip install moonshine-onnx  (or moonshine-voice)"
        )

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        logger.info(
            "Moonshine transcribing %s (model=%s)",
            audio_path, self._model_name,
        )
        # Try moonshine_onnx first (lighter), then moonshine_voice
        try:
            import moonshine_onnx
            text = moonshine_onnx.transcribe(audio_path, model=self._model_name)
            if isinstance(text, list):
                text = " ".join(text)
        except ImportError:
            from moonshine_voice import Transcriber
            if self._transcriber is None:
                self._transcriber = Transcriber(model=self._model_name)
            text = self._transcriber.transcribe_file(audio_path)
            if isinstance(text, list):
                text = " ".join(text)

        # Moonshine returns plain text without timestamps in basic mode.
        # Build minimal segments structure.
        segments_out = []
        if text and text.strip():
            # Get audio duration for rough segment bounds
            try:
                import soundfile as sf
                info = sf.info(audio_path)
                duration = info.duration
            except Exception:
                duration = None

            segments_out.append({
                "text": text.strip(),
                "start": 0.0,
                "end": duration,
                "words": [],
            })

        chunks = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments_out
        ]
        return {
            "chunks": chunks,
            "segments": segments_out,
            "language": "en",
        }

    def unload(self) -> None:
        self._transcriber = None


# ── sherpa-onnx live dictation (ONNX, CPU, streaming + offline) ─────────────


def _load_audio_16k_mono_f32(audio_path: str):
    """Decode any audio file to 16 kHz mono float32 in [-1, 1] for sherpa.

    Prefers soundfile (WAV/FLAC — the dictation buffers are already WAV) and
    resamples to 16 kHz when needed; falls back to VoiceStudio's validated ffmpeg
    for containers soundfile can't read (WebM/Opus). 16 kHz is sherpa's cheapest
    feed; it resamples internally too, but doing it here keeps the contract tight.
    """
    import numpy as np
    try:
        import soundfile as sf
        data, sr = sf.read(audio_path, dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        data = np.ascontiguousarray(data, dtype=np.float32)
        if sr != 16000:
            # Lightweight linear resample — adequate for ASR features.
            n = int(round(len(data) * 16000 / sr))
            if n > 0:
                xp = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
                x = np.linspace(0.0, 1.0, num=n, endpoint=False)
                data = np.interp(x, xp, data).astype(np.float32)
            sr = 16000
        return data, sr
    except Exception:
        # Container soundfile can't read (WebM/Opus) — use the validated ffmpeg
        # path, which already yields 16 kHz mono float32.
        return _decode_audio_16k_mono(audio_path), 16000


class SherpaDictationBackend(ASRBackend):
    """k2-fsa/sherpa-onnx ONNX dictation engine (CPU, live + offline).

    One :class:`ASRBackend` instance is bound to one of the seven sherpa
    dictation models (see :mod:`services.sherpa_dictation`). For the offline
    ``transcribe(path)`` contract it runs an ``OfflineRecognizer`` for offline
    models and a one-shot ``OnlineRecognizer`` decode for streaming models
    (so ``POST /transcribe`` works for every sherpa model). The *live* WS path
    drives the streaming recognizer incrementally — see ``capture_ws.py``.

    CPU provider only (cross-platform default-parity rule); no CUDA dep.
    """
    id = "sherpa-onnx-asr"
    display_name = "Sherpa-ONNX dictation (live, CPU — streaming + offline)"
    gpu_compat = ("cpu",)

    def __init__(self, model_id: str | None = None):
        from services import sherpa_dictation as _sd
        mid = model_id or sherpa_engine_model_id()
        spec = _sd.get_spec(mid)
        if spec is None:
            raise ValueError(
                f"Unknown sherpa dictation model {mid!r}. Known: "
                f"{[s.id for s in _sd.list_specs()]}"
            )
        self._spec = spec
        from services.performance_profiles import requested_tier
        self.performance_tier = requested_tier("dictation")
        self._rec = None  # lazy OfflineRecognizer / OnlineRecognizer
        # One backend is shared across live-dictation WS sessions (see
        # get_sherpa_dictation_backend), so guard the one-time recognizer build
        # against two sessions racing to construct it concurrently. Each session
        # still owns its own decode stream — only the recognizer is shared.
        self._rec_lock = threading.Lock()

    @property
    def spec(self):
        return self._spec

    @property
    def streaming(self) -> bool:
        return self._spec.streaming

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from services.sherpa_dictation import sherpa_available
        return sherpa_available()

    def ensure_loaded(self) -> None:
        self._ensure_rec()

    def warmup(self) -> None:
        """Eagerly build the recognizer so the FIRST live-dictation session
        doesn't pay the 1.3–2.5s ONNX-session load (#888 'instant first
        dictation'). Called by the background capture-ASR preload; idempotent,
        and the built recognizer is reused across sessions via
        get_sherpa_dictation_backend (the same singleton the preload warms)."""
        self._ensure_rec()

    def _ensure_rec(self):
        if self._rec is not None:
            return
        with self._rec_lock:
            if self._rec is not None:
                return
            from services import sherpa_dictation as _sd
            if self._spec.streaming:
                self._rec = _sd.build_online_recognizer(self._spec)
            else:
                self._rec = _sd.build_offline_recognizer(self._spec)

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        self._ensure_rec()
        logger.info(
            "sherpa-onnx dictation transcribing %s (model=%s, kind=%s)",
            audio_path, self._spec.id, self._spec.kind,
        )
        samples, sr = _load_audio_16k_mono_f32(audio_path)
        if self._spec.streaming:
            text = self._decode_online_oneshot(samples, sr)
        else:
            text = self._decode_offline(samples, sr)
        return _sherpa_result(text, samples, sr)

    def _decode_offline(self, samples, sr) -> str:
        s = self._rec.create_stream()
        s.accept_waveform(sr, samples)
        self._rec.decode_stream(s)
        return (s.result.text or "").strip()

    def _decode_online_oneshot(self, samples, sr) -> str:
        """One-shot decode of a whole buffer through the streaming recognizer
        (for the non-streaming ``transcribe()`` / partial re-decode path)."""
        import numpy as np
        s = self._rec.create_stream()
        s.accept_waveform(sr, samples)
        tail = np.zeros(int(0.5 * sr), dtype=np.float32)
        s.accept_waveform(sr, tail)
        s.input_finished()
        while self._rec.is_ready(s):
            self._rec.decode_stream(s)
        return (self._rec.get_result(s) or "").strip()

    def unload(self) -> None:
        self._rec = None
        import gc
        gc.collect()


def _sherpa_result(text: str, samples, sr) -> dict:
    """Normalise a sherpa decode to VoiceStudio's ``{chunks, segments, language,
    text}`` contract. sherpa gives plain text (no VAD split), so emit a single
    segment spanning the buffer — same shape Moonshine uses."""
    text = (text or "").strip()
    try:
        duration = round(len(samples) / float(sr), 3)
    except Exception:
        duration = None
    segments = []
    if text:
        segments.append({"text": text, "start": 0.0, "end": duration, "words": []})
    chunks = [{"text": s["text"], "timestamp": (s["start"], s["end"])} for s in segments]
    return {"chunks": chunks, "segments": segments, "language": "auto", "text": text}


# ── Registry ────────────────────────────────────────────────────────────────


# ── FunASR (SenseVoice — all-in-one multilingual, opt-in alternative, #182) ──

# SenseVoice emits rich tokens like `<|en|><|NEUTRAL|><|Speech|>` around the
# text; strip them when no postprocessor is applied.
_FUNASR_TAG_RE = re.compile(r"<\|[^|>]*\|>")


def _ms_to_s(value):
    """Milliseconds → seconds (FunASR reports ms). None on bad input."""
    try:
        return round(float(value) / 1000.0, 3)
    except (TypeError, ValueError):
        return None


def _clean_funasr_text(text):
    return _FUNASR_TAG_RE.sub("", str(text or "")).strip()


def _normalize_funasr(res) -> dict:
    """Normalise FunASR ``generate()`` output → VoiceStudio's
    ``{chunks, segments, language}`` shape (the same one the Whisper backends
    return, consumed by ``services.segmentation``). Defensive about FunASR's
    output variations: prefers VAD ``sentence_info`` (ms timestamps + optional
    ``spk`` speaker id) and falls back to a single utterance from ``text``.
    Pure — testable without funasr installed.
    """
    item = (res[0] if isinstance(res, (list, tuple)) and res else res) or {}
    if not isinstance(item, dict):
        item = {"text": str(item)}
    language = item.get("language") or item.get("lang") or None

    segments = []
    for s in item.get("sentence_info") or []:
        if not isinstance(s, dict):
            continue
        txt = _clean_funasr_text(s.get("text") or s.get("sentence", ""))
        if not txt:
            continue
        seg = {"text": txt, "start": _ms_to_s(s.get("start", 0)) or 0.0, "end": _ms_to_s(s.get("end"))}
        spk = s.get("spk")
        if spk is not None:
            seg["speaker"] = f"Speaker {int(spk) + 1}" if isinstance(spk, (int, float)) else str(spk)
        segments.append(seg)

    if not segments:
        txt = _clean_funasr_text(item.get("text", ""))
        if txt:
            ts = item.get("timestamp") or []  # [[start_ms, end_ms], ...]
            start = _ms_to_s(ts[0][0]) if ts else 0.0
            end = _ms_to_s(ts[-1][1]) if ts else None
            segments.append({"text": txt, "start": start or 0.0, "end": end})

    chunks = [{"text": seg["text"], "timestamp": (seg["start"], seg.get("end"))} for seg in segments]
    return {"chunks": chunks, "segments": segments, "language": language}


class FunASRBackend(ASRBackend):
    """FunASR — SenseVoiceSmall + FSMN-VAD. All-in-one multilingual ASR:
    transcription + punctuation across 50+ languages, with optional speaker
    diarization via the cam++ model. Opt-in alternative to WhisperX (issue
    #182); WhisperX remains the cross-platform default.
    """
    id = "funasr"
    gpu_compat = ("cuda", "cpu")  # FunASR: CUDA or CPU
    display_name = "FunASR (SenseVoice — 50+ languages, all-in-one)"

    def __init__(self):
        self._model_name = os.environ.get("ASR_MODEL_FUNASR", "iic/SenseVoiceSmall")
        self._vad_model = os.environ.get("ASR_FUNASR_VAD", "fsmn-vad")
        # cam++ speaker model → inline diarization (Phase 2). Set ASR_FUNASR_SPK=""
        # to disable and fall back to the dub pipeline's pyannote/heuristic path.
        self._spk_model = os.environ.get("ASR_FUNASR_SPK", "cam++")
        self._model = None

    @property
    def requires_full_audio_for_speaker_consistency(self) -> bool:
        # CAM++ assigns cluster IDs per generate() call. Let FunASR's internal
        # VAD split long recordings so one call retains global voice identity.
        return bool(self._spk_model)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import funasr  # noqa: F401
            return True, "ready"
        except ImportError:
            return False, "funasr not installed. Install with: uv pip install funasr"

    def _ensure_model(self):
        if self._model is not None:
            return
        from funasr import AutoModel
        kwargs = {"model": self._model_name, "vad_model": self._vad_model, "disable_update": True}
        if self._spk_model:
            kwargs["spk_model"] = self._spk_model
            # FunASR 1.3.1 defaults to punc_segment, which requires a separate
            # punc_model and crashes when SenseVoice is loaded without one.
            kwargs["spk_mode"] = "vad_segment"
        logger.info("FunASR loading %s (vad=%s, spk=%s)", self._model_name, self._vad_model, self._spk_model or "off")
        self._model = AutoModel(**kwargs)

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        self._ensure_model()
        logger.info("FunASR transcribing %s", audio_path)
        kwargs = {"input": audio_path, "cache": {}, "language": "auto", "use_itn": True}
        if self._spk_model:
            # vad_segment reads SenseVoice's timestamps to build sentence_info.
            kwargs["output_timestamp"] = True
        res = self._model.generate(**kwargs)
        return _normalize_funasr(res)

    def unload(self) -> None:
        self._model = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# ── OpenAI-compatible remote transcription (#877 — Qwen3-ASR / FunASR / any
#    compatible server, today, without waiting on transformers to catch up) ──
#
# transformers doesn't yet ship a stable Qwen3-ASR integration (issue #877),
# but a self-hosted Qwen3-ASR/FunASR/SenseVoice server exposing an
# OpenAI-compatible `POST /v1/audio/transcriptions` endpoint — or OpenAI's own
# Whisper API — is usable right now. This backend is a pure network client:
# no model runs locally, so it needs no install and claims no GPU.
#
# Settings mirror the LLM-providers convention exactly (services/
# llm_providers.py): base_url/model are plain settings_store text rows; the
# API key is Fernet-encrypted via settings_store.set_secret/get_secret — never
# a .env row, never echoed back to the client. Optional: some self-hosted
# servers (vLLM, LM Studio-style) don't check the key at all.

_ASR_OPENAI_COMPAT_BASE_URL_KEY = "asr.openai_compat.base_url"
_ASR_OPENAI_COMPAT_MODEL_KEY = "asr.openai_compat.model"
_ASR_OPENAI_COMPAT_SECRET_NAME = "asr_openai_compat_key"


def normalize_openai_compat_asr_base_url(value: str) -> str:
    """Normalize a safe ASR endpoint, allowing plain HTTP only on loopback."""
    base = (value or "").strip().rstrip("/")
    if not base:
        return ""
    try:
        parsed = urlsplit(base)
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid OpenAI-compatible ASR base URL") from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OpenAI-compatible ASR base URL must be a credential-free HTTP(S) URL"
        )
    host = parsed.hostname.lower()
    loopback = host == "localhost"
    if not loopback:
        try:
            address = ipaddress.ip_address(host)
            address = getattr(address, "ipv4_mapped", None) or address
            loopback = address.is_loopback
        except ValueError:
            loopback = False
    if scheme == "http" and not loopback:
        raise ValueError("Non-loopback OpenAI-compatible ASR endpoints require HTTPS")
    return base


def resolve_openai_compat_asr_base_url() -> str:
    from services import settings_store
    return (
        os.environ.get("ASR_OPENAI_COMPAT_BASE_URL")
        or settings_store.get_text(_ASR_OPENAI_COMPAT_BASE_URL_KEY)
        or ""
    )


def resolve_openai_compat_asr_model() -> str:
    from services import settings_store
    return (
        os.environ.get("ASR_OPENAI_COMPAT_MODEL")
        or settings_store.get_text(_ASR_OPENAI_COMPAT_MODEL_KEY)
        or "whisper-1"
    )


def resolve_openai_compat_asr_api_key() -> Optional[str]:
    """Env → encrypted stored key → None. Unlike LLM providers, no 'local'
    sentinel: many self-hosted transcription servers accept an empty/omitted
    Authorization header outright, so the OpenAI SDK is constructed with
    ``api_key="not-needed"`` (a non-empty placeholder the SDK requires) when
    this returns None, rather than treating a keyless server as unconfigured.
    """
    from services import settings_store
    return os.environ.get("ASR_OPENAI_COMPAT_API_KEY") or settings_store.get_secret(
        _ASR_OPENAI_COMPAT_SECRET_NAME
    )


def openai_compat_asr_has_key() -> bool:
    """Whether a key is configured, without ever decrypting it — mirrors
    llm_providers.has_key()'s no-plaintext-round-trip contract."""
    from services import settings_store
    if os.environ.get("ASR_OPENAI_COMPAT_API_KEY"):
        return True
    return _ASR_OPENAI_COMPAT_SECRET_NAME in settings_store.list_secret_names()


def probe_openai_compat_server(
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    *,
    timeout_s: float = 8.0,
) -> dict:
    """Cheap reachability probe for the Settings "Test connection" button.

    ``GET {base_url}/models`` — no audio is uploaded, no transcription runs.
    The Settings route probes the PERSISTED config (the panel saves first,
    then tests — same stale-config contract as /llm-providers/{id}/test);
    the optional arguments override it for programmatic/test use: ``None``
    falls back to the persisted setting, and for ``api_key`` an explicit
    ``""`` probes without a key (many self-hosted servers need none). Never
    raises, never logs or echoes the key; ``detail`` is passed through
    core.scrub so a leaked token or home path can't reach the UI.

    Returns ``{ok, status, latency_ms, http_status, models_count,
    model_found, detail}`` where ``status`` is a machine code the frontend
    maps to a translated message:

      not_configured   no base URL anywhere
      invalid_url      malformed URL or non-loopback HTTP endpoint
      ok               2xx — ``model_found`` says whether the configured
                       model appears in the server's list (None = unknown)
      ok_no_models     404/405/501 — reachable, but no /models endpoint
                       (some minimal transcription servers); transcription
                       may still work
      auth_failed      401/403 — the server rejected the key
      http_error       any other status (see ``http_status``)
      timeout          no answer within ``timeout_s``
      unreachable      connection failed (wrong port, server down, DNS…)
    """
    from time import perf_counter

    from core.scrub import scrub_text

    configured_base = base_url if base_url is not None else resolve_openai_compat_asr_base_url()
    mdl = (model if model is not None else resolve_openai_compat_asr_model()).strip()
    if api_key is None:
        key = resolve_openai_compat_asr_api_key()
    else:
        key = api_key.strip() or None

    out: dict = {
        "ok": False,
        "status": "not_configured",
        "latency_ms": None,
        "http_status": None,
        "models_count": None,
        "model_found": None,
        "detail": None,
    }
    if not configured_base.strip():
        return out
    try:
        base = normalize_openai_compat_asr_base_url(configured_base)
    except ValueError:
        out["status"] = "invalid_url"
        return out

    import httpx

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            follow_redirects=False,
        ) as client:
            resp = client.get(f"{base}/models", headers=headers)
    except httpx.TimeoutException as exc:
        out.update(
            status="timeout",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=scrub_text(f"{type(exc).__name__}: {exc}"),
        )
        return out
    except Exception as exc:  # noqa: BLE001 — ConnectError, UnsupportedProtocol, SSL…
        out.update(
            status="unreachable",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=scrub_text(f"{type(exc).__name__}: {exc}"),
        )
        return out

    out["latency_ms"] = round((perf_counter() - t0) * 1000.0, 1)
    out["http_status"] = resp.status_code

    if 200 <= resp.status_code < 300:
        out.update(ok=True, status="ok")
        try:
            data = resp.json()
            entries = data.get("data") if isinstance(data, dict) else data
            if isinstance(entries, list):
                ids = [e.get("id") for e in entries if isinstance(e, dict) and e.get("id")]
            else:
                ids = None
        except Exception:  # noqa: BLE001 — non-JSON 200 still proves reachability
            ids = None
        if ids is not None:
            out["models_count"] = len(ids)
            out["model_found"] = mdl in ids if mdl else None
        return out

    if resp.status_code in (401, 403):
        out["status"] = "auth_failed"
    elif resp.status_code in (404, 405, 501):
        # Reachable server without a /models endpoint — the transcription
        # route may still work, so this is a (qualified) success.
        out.update(ok=True, status="ok_no_models")
    else:
        out["status"] = "http_error"
        out["detail"] = scrub_text((resp.text or "")[:300]) or None
    return out


class OpenAICompatASRBackend(ASRBackend):
    """Remote transcription via any OpenAI-compatible server.

    Adapts whatever the server returns into this module's expected shape.
    Prefers `response_format="verbose_json"` for real per-segment timestamps
    (OpenAI's own API and most compatible servers support it); falls back to
    plain text with rough single-segment bounds — mirroring
    MoonshineASRBackend's degraded shape — for minimal servers that reject it.
    """
    id = "openai-compat-asr"
    display_name = "OpenAI-compatible (remote server)"
    gpu_compat = ("cpu",)  # network client only — no local compute

    def __init__(self):
        self._base_url = normalize_openai_compat_asr_base_url(
            resolve_openai_compat_asr_base_url()
        )
        self._model = resolve_openai_compat_asr_model()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        base_url = resolve_openai_compat_asr_base_url()
        if not base_url:
            return False, "Configure a server endpoint in Model Catalogue"
        try:
            normalize_openai_compat_asr_base_url(base_url)
        except ValueError as exc:
            return False, str(exc)
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "openai package not installed. Install with: uv pip install openai"
        return True, "ready"

    def _client(self):
        from openai import DefaultHttpxClient, OpenAI
        api_key = resolve_openai_compat_asr_api_key() or "not-needed"
        # max_retries=0: mirrors llm_skills.resolve_skill_client — a
        # rate-limited/slow server retrying inside the SDK would blow past
        # whatever bounded timeout the caller (dub transcribe, dictation)
        # expects from a single call.
        return OpenAI(
            base_url=self._base_url,
            api_key=api_key,
            max_retries=0,
            http_client=DefaultHttpxClient(follow_redirects=False),
        )

    def supports_translation(self) -> bool:
        # The remote server's own /audio/translations decides (and errors).
        return True

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True,
                   language: str | None = None, initial_prompt: str | None = None,
                   temperature: float | None = None,
                   task: str = "transcribe") -> dict:
        logger.info(
            "OpenAI-compat ASR transcribing %s (base_url=%s, model=%s)",
            audio_path, self._base_url, self._model,
        )
        client = self._client()
        extra: dict = {}
        if initial_prompt:
            extra["prompt"] = initial_prompt
        if temperature is not None:
            extra["temperature"] = temperature
        if task == "translate":
            endpoint = client.audio.translations
        else:
            endpoint = client.audio.transcriptions
            if language:
                extra["language"] = language
        try:
            with open(audio_path, "rb") as f:
                try:
                    resp = endpoint.create(
                        file=f, model=self._model, response_format="verbose_json",
                        **extra,
                    )
                except Exception:
                    # Minimal/older compatible servers reject verbose_json
                    # outright — retry plain before treating it as a real
                    # failure. Re-open: the SDK may have partially consumed
                    # the file handle on the first attempt.
                    f.seek(0)
                    resp = endpoint.create(
                        file=f, model=self._model, response_format="json", **extra,
                    )
        except Exception as exc:
            # Never leak a raw SDK/httpx exception object (auth headers,
            # connection internals) straight into a user-facing message —
            # same convention as generation.py's _safe_exc_text (#977 class).
            raise RuntimeError(
                f"OpenAI-compatible ASR server at {self._base_url!r} failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        out = self._adapt_response(resp)
        if language and not getattr(resp, "language", None):
            out["language"] = language
        return out

    @staticmethod
    def _adapt_response(resp) -> dict:
        segments_out = []
        # verbose_json: resp.segments is a list of objects with start/end/text.
        raw_segments = getattr(resp, "segments", None)
        if raw_segments:
            for seg in raw_segments:
                seg_dict = seg if isinstance(seg, dict) else seg.model_dump()
                segments_out.append({
                    "text": (seg_dict.get("text") or "").strip(),
                    "start": seg_dict.get("start", 0.0),
                    "end": seg_dict.get("end", 0.0),
                    "words": [],  # word-level timing isn't part of this API
                })
        else:
            # Plain text response (json/text format) — single-segment shape,
            # matching MoonshineASRBackend's degraded fallback exactly.
            text = (getattr(resp, "text", None) or "").strip()
            if text:
                segments_out.append({"text": text, "start": 0.0, "end": None, "words": []})
        chunks = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments_out
        ]
        language = getattr(resp, "language", None) or "en"
        return {"chunks": chunks, "segments": segments_out, "language": language}


def _isolated_faster_whisper():
    """Lazy import so the subprocess_asr → subprocess_backend chain isn't
    pulled in at registry definition time."""
    from services.subprocess_asr import IsolatedFasterWhisperBackend
    return IsolatedFasterWhisperBackend


class _LazyASRRegistry(dict):
    """Registry with one lazily-resolved entry (Wave 4.2). Mirrors the TTS
    registry's lazy pattern so listing/selecting the crash-isolated ASR
    backend doesn't import the subprocess stack unless it's used."""

    _LAZY = {"faster-whisper-isolated": _isolated_faster_whisper}

    def __contains__(self, key):
        return dict.__contains__(self, key) or key in self._LAZY

    def __getitem__(self, key):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key in self._LAZY:
            cls = self._LAZY[key]()
            self[key] = cls
            return cls
        raise KeyError(key)

    def __iter__(self):
        seen = set()
        # Snapshot the live keys before yielding — see _LazyRegistry.__iter__ in
        # tts_backend.py. A concurrent lazy __getitem__ inserts into self, and
        # list_backends() runs in a FastAPI threadpool, so a *live* dict iterator
        # held open across the per-engine is_available() probes would raise
        # "dictionary changed size during iteration". list() consumes it
        # atomically under the GIL, closing the window.
        for k in list(dict.__iter__(self)):
            seen.add(k)
            yield k
        for k in self._LAZY:
            if k not in seen:
                yield k

    def items(self):
        for k in self:
            yield k, self[k]


_REGISTRY: dict[str, type[ASRBackend]] = _LazyASRRegistry({
    "whisperx":        WhisperXBackend,
    "faster-whisper":  FasterWhisperBackend,
    "mlx-whisper":     MLXWhisperBackend,
    "pytorch-whisper": PyTorchWhisperBackend,
    "nemo-parakeet":   NeMoASRBackend,
    "parakeet-mlx":    ParakeetMLXBackend,
    "moonshine":       MoonshineASRBackend,
    "funasr":          FunASRBackend,
    "sherpa-onnx-asr": SherpaDictationBackend,
    "openai-compat-asr": OpenAICompatASRBackend,
    # "faster-whisper-isolated": resolved lazily (crash-isolated subprocess).
})


# Short install hints surfaced as tooltips on the Model Catalogue UI
# (parity with tts_backend._INSTALL_HINTS).
_INSTALL_HINTS: dict[str, str] = {
    "whisperx":        "pip install whisperx  (CTranslate2 + wav2vec2 alignment; CUDA or CPU)",
    "faster-whisper":  "pip install faster-whisper  (CTranslate2; cross-platform, CUDA or CPU)",
    "mlx-whisper":     "pip install mlx-whisper  (Apple Silicon only)",
    "pytorch-whisper": "Bundled with transformers — no extra install (CUDA/MPS/CPU)",
    "nemo-parakeet":   (
        "No safe install path in this app yet — nemo_toolkit's ASR extras pin "
        "transformers>=4.57,<4.58, which conflicts with VoiceStudio's own "
        "transformers>=5.3 requirement and WILL break the backend "
        "(ImportError on startup) if installed into this shared venv. Do NOT "
        "install nemo_toolkit here. If you want to try Parakeet TDT, set it "
        "up in a separate/dedicated Python environment — not the one "
        "VoiceStudio manages; in-app isolation for this engine is tracked "
        "separately."
    ),
    "parakeet-mlx":    (
        "uv add parakeet-mlx  (Apple Silicon only — installed by default on "
        "mac-ARM source installs since 0.3.22. Parakeet TDT v3 on the GPU via "
        "MLX: 25 European languages, word timestamps, ~2 GB unified memory.)"
    ),
    "moonshine":       "uv pip install moonshine-onnx  (or moonshine-voice; edge/CPU-optimized ASR)",
    "funasr":          "pip install funasr  (SenseVoiceSmall + FSMN-VAD; CUDA or CPU)",
    "sherpa-onnx-asr": "uv add sherpa-onnx  (ONNX live dictation; CPU, cross-platform)",
    "openai-compat-asr": (
        "No install needed — configure a server endpoint in "
        "Model Catalogue. Points VoiceStudio at any OpenAI-compatible "
        "server (a self-hosted Qwen3-ASR/FunASR/SenseVoice server, OpenAI's "
        "own Whisper API, or similar) — a path to Qwen3-ASR today, without "
        "waiting on a direct transformers integration."
    ),
    "faster-whisper-isolated": (
        "No extra install (reuses faster-whisper). Escape hatch for hanging "
        "transcribes: runs ASR in a separate process that can be force-killed "
        "to reclaim a hung transcribe and its VRAM (#730). Slightly slower per "
        "call than in-process faster-whisper."
    ),
}

# Most-recent failure per backend, so a transient probe error survives between
# Settings refreshes (parity with tts_backend._LAST_ERRORS).
_LAST_ERRORS: dict[str, str] = {}

# Backends whose *deep* import chain proved broken at load time (#1185).
# ``is_available()`` is deliberately shallow — ``import whisperx`` succeeds
# even when a transitive dep of ``whisperx.load_model()`` is missing (the
# reported case: whisperx → pyannote.audio → pytorch_lightning →
# ``lightning_fabric``, which ships *inside* the pytorch_lightning wheel and
# only imports at load time). A module missing that deep is env rot — a
# partial/broken install (interrupted sync, antivirus quarantine): every
# uv.lock we ever shipped resolves it — so it can't be repaired from inside
# the process. Record it here so probes report the backend unavailable (with
# the repair hint) and selection falls through to the next engine instead of
# failing ASR wholesale. Per-process by design: repairing the env requires a
# reinstall / ``uv sync --reinstall`` and an app restart anyway.
_DEEP_IMPORT_BROKEN: dict[str, str] = {}
_RUNTIME_EVIDENCE: dict[str, dict] = {}
_RUNTIME_INSTANCES: weakref.WeakValueDictionary[str, "ASRBackend"] = weakref.WeakValueDictionary()


def _deep_import_reason(cls: type["ASRBackend"], exc: ImportError) -> str:
    """User-facing reason for a load-time import failure: names the missing
    module and the repair command (the ``install_hint`` contract of #1185)."""
    missing = getattr(exc, "name", None)
    what = (
        f"its Python dependency {missing!r} is missing"
        if missing else f"a Python dependency is broken ({exc})"
    )
    return (
        f"{cls.display_name} failed to load: {what}. The app environment "
        "looks partially installed — reinstall VoiceStudio (or run "
        "`uv sync --reinstall` on a source checkout; plain `uv sync` "
        "trusts the intact package metadata and skips the broken files) "
        "to repair it."
    )


def list_backends() -> list[dict]:
    """Enumerate every ASR backend with the **same 11-key shape as TTS** so the
    Engine Compatibility Matrix renders all families uniformly.

    Per-entry: id, display_name, available, reason (scrubbed), install_hint,
    last_error, isolation_mode, gpu_compat, effective_device, routing_status,
    routing_reason. A backend whose ``is_available()`` raises is reported
    ``available: false`` (never a 500), exactly like TTS.
    """
    from core.device_caps import detect_host_caps
    from core.scrub import scrub_text
    from services.engine_evidence import snapshot as execution_snapshot
    from services.engine_routing import routing_fields
    caps = detect_host_caps()

    out: list[dict] = []
    for bid, cls in _REGISTRY.items():
        broken = _DEEP_IMPORT_BROKEN.get(bid)
        if broken is not None:
            # Loading this backend already proved a missing transitive module
            # (#1185) — the shallow probe below would wrongly report "ready",
            # so surface the recorded truth (which carries the repair hint).
            ok, msg = False, broken
        else:
            try:
                ok, msg = cls.is_available()
            except Exception:
                ok = False
                msg = "Availability probe failed; check the backend log."
                logger.warning("asr list_backends: availability probe failed for registered backend %s", bid)
        if ok:
            _LAST_ERRORS.pop(bid, None)
        else:
            _LAST_ERRORS[bid] = scrub_text(msg)
        isolation = "subprocess" if getattr(cls, "_is_subprocess_isolated", False) else "in-process"
        gpu_compat = getattr(cls, "gpu_compat", ("cpu",))
        routing = routing_fields(gpu_compat, caps)
        # Cached load-time facts are valid only while their exact backend still
        # owns live model state. Recompute from that instance so unload/reaping
        # cannot leave ghost GPU/provider evidence in diagnostics.
        instance = (
            _ISOLATED_INSTANCES.get(bid)
            if isolation == "subprocess"
            else _RUNTIME_INSTANCES.get(bid)
        )
        execution_evidence = execution_snapshot(
            engine_id=bid,
            engine_cls=cls,
            instance=instance,
            routing=routing,
            caps=caps,
        )
        if execution_evidence["evidence_state"] == "not_loaded":
            _RUNTIME_EVIDENCE.pop(bid, None)
        out.append({
            "id": bid,
            "display_name": cls.display_name,
            "available": ok,
            # ASR previously emitted `reason` UNMASKED — scrub it now (closes a
            # pre-existing token-leak gap, matching TTS's redaction guarantee).
            "reason": None if ok else scrub_text(msg),
            "install_hint": _INSTALL_HINTS.get(bid),
            "last_error": _LAST_ERRORS.get(bid),
            "isolation_mode": isolation,
            "gpu_compat": list(gpu_compat),
            **routing,
            "execution_evidence": execution_evidence or execution_snapshot(
                engine_id=bid,
                engine_cls=cls,
                instance=None,
                routing=routing,
                caps=caps,
            ),
        })
    return out


def _probe_available(cls) -> bool:
    """``is_available()`` that never raises. A probe that explodes (e.g. a native
    lib that refuses to load — CTranslate2's exec-stack rejection, #692) means the
    engine is unusable on this host, so treat it as unavailable and fall through
    to the next candidate rather than crash engine selection."""
    if getattr(cls, "id", None) in _DEEP_IMPORT_BROKEN:
        # A previous load proved this backend's deep import chain is broken
        # (#1185) — the shallow probe would succeed, so consult the record
        # and let auto-detect fall through to the next engine.
        return False
    try:
        ok, _ = cls.is_available()
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.warning(
            "ASR auto-detect: %s.is_available() raised — treating as unavailable",
            cls.__name__, exc_info=True,
        )
        return False


def _mps_available() -> bool:
    try:
        import torch

        return bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    except Exception:  # noqa: BLE001 — no torch / no MPS
        return False


def _cuda_reported_available() -> bool:
    """``torch.cuda.is_available()`` verbatim — True on real CUDA *and* HIP."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — no torch
        return False


def _rocm_torch() -> bool:
    """True when torch is the ROCm (HIP) build.

    ROCm torch masquerades as CUDA: ``torch.cuda.is_available()`` answers True
    and tensors live on ``"cuda"`` devices, but the CUDA *runtime libraries*
    other packages ship are still NVIDIA-only. ``torch.version.hip`` is the
    one honest tell.
    """
    try:
        import torch

        return getattr(torch.version, "hip", None) is not None
    except Exception:  # noqa: BLE001 — no torch
        return False


def _ctranslate2_cuda_ok() -> bool:
    """Whether CTranslate2 (whisperx / faster-whisper) may use ``"cuda"``.

    CTranslate2 has NO HIP backend. On a ROCm host torch says cuda is
    available (HIP), the device string is handed to CTranslate2, and its
    NVIDIA CUDA runtime dies with "CUDA driver version is insufficient for
    CUDA runtime version" — the #1529 report, an AMD RX 7900 XTX in the
    :rocm Docker image. Real CUDA only; ROCm hosts take the CPU path here
    (auto-detect prefers pytorch-whisper there, which does use HIP).

    Also honors the user compute-device override (Settings → Performance /
    ``OMNIVOICE_DEVICE``): a host pinned to cpu (or any non-cuda family)
    must not hand CTranslate2 a CUDA device — the probe applies the
    override, so gating on its family covers every CT2 loader at once.
    """
    try:
        from core.device_caps import detect_host_caps

        if detect_host_caps().family != "cuda":
            return False
    except Exception:  # noqa: BLE001 — fail SAFE, not fast
        # Without a working probe we can't know whether an override or a
        # ROCm build is in play — guessing "cuda" from torch here is exactly
        # the #1529 crash. CPU always works.
        logger.warning("device probe failed — CTranslate2 taking the CPU path", exc_info=True)
        return False
    return _cuda_reported_available() and not _rocm_torch()


def _auto_detect() -> str:
    """Pick the best available ASR engine **for this hardware**.

    The order used to be whisperx-first, unconditionally — and that quietly cost
    Apple Silicon users a 4.4x slowdown on every dub (#1127). WhisperX and
    faster-whisper are CTranslate2, which has **no Metal backend**: on a Mac they
    transcribe on the *CPU*, no matter what GPU is sitting there. Measured on an
    M2, one 30 s dub chunk, whisper-large-v3: **90.4 s on WhisperX (CPU) vs 20.5 s
    on MLX (GPU)** — 3x slower than realtime, which is how a 16-minute video turned
    into a ~48-minute transcribe and looked like a hang.

    So the pick is device-aware:

      1. mlx-whisper    — **Apple Silicon only.** Runs the *same* whisper-large-v3
                          on the GPU, and we layer WhisperX's wav2vec2 forced
                          alignment on top (see MLXWhisperBackend.transcribe), so
                          word timing — and therefore lip-sync — is unchanged.
                          Same model, same alignment, ~4x the speed.
      2. whisperx       — everywhere else: faster-whisper + wav2vec2 forced
                          alignment (±10-30 ms word timing). On CUDA it uses the
                          GPU, so it remains the right default there.
      3. faster-whisper — transcription only (no forced alignment); safe fallback
                          when whisperx isn't installed.
      4. pytorch-whisper — last resort; requires the TTS model to be loaded so it
                          can reuse `_asr_pipe`.

    Auto-detect only. An explicit ``OMNIVOICE_ASR_BACKEND`` or the ``asr_backend``
    pref still wins, so anyone who pinned an engine keeps it.
    """
    if _mps_available() and _probe_available(MLXWhisperBackend):
        return "mlx-whisper"
    # Same class as the Apple case, on the ROCm axis (#1529): whisperx and
    # faster-whisper are CTranslate2, which has no HIP backend — on a ROCm
    # host they run on the CPU while the GPU sits idle (and before
    # _ctranslate2_cuda_ok they died outright trying NVIDIA's runtime).
    # pytorch-whisper is a pure transformers pipeline riding torch itself,
    # so it genuinely uses the HIP GPU there.
    if _rocm_torch() and _cuda_reported_available() and _probe_available(PyTorchWhisperBackend):
        return "pytorch-whisper"
    if _probe_available(WhisperXBackend):
        return "whisperx"
    if _probe_available(FasterWhisperBackend):
        return "faster-whisper"
    return "pytorch-whisper"


def active_backend_id() -> str:
    explicit = os.environ.get("OMNIVOICE_ASR_BACKEND")
    if explicit:
        # #1582's public spelling predates the registry name. Keep it as a
        # compatibility alias for the PyTorch-native Whisper implementation
        # that can use ROCm/HIP; every ASR consumer resolves through here.
        return "pytorch-whisper" if explicit == "omnivoice" else explicit
    from core import prefs
    picked = prefs.get("asr_backend")
    if picked:
        return picked
    return _auto_detect()


# Subprocess-isolated backends must be process-wide singletons: their
# ``__init__`` registers an atexit shutdown hook and the instance owns the
# sidecar child process, so a fresh instance per request would leak handler
# entries and respawn the sidecar (reloading its model) on every transcribe.
# Same rationale as api.routers.engines._ENGINE_INSTANCES.
_ISOLATED_INSTANCES: dict[str, "ASRBackend"] = {}


def get_active_asr_backend(*, asr_pipe=None) -> ASRBackend:
    bid = active_backend_id()
    if bid == "pytorch-whisper":
        return PyTorchWhisperBackend(asr_pipe=asr_pipe)
    if bid == "mlx-whisper":
        return MLXWhisperBackend()
    if bid == "faster-whisper":
        return FasterWhisperBackend()
    if bid == "whisperx":
        return WhisperXBackend()
    if bid not in _REGISTRY:
        raise ValueError(f"Unknown ASR backend: {bid!r}. Known: {list(_REGISTRY)}")
    cls = _REGISTRY[bid]
    if getattr(cls, "_is_subprocess_isolated", False):
        inst = _ISOLATED_INSTANCES.get(bid)
        if inst is None:
            inst = cls()
            _ISOLATED_INSTANCES[bid] = inst
        return inst
    return cls()


def _asr_backend_pinned() -> bool:
    """True when the user explicitly pinned an ASR engine (env var or pref) —
    a pinned engine is honored, never silently swapped (see _auto_detect)."""
    if os.environ.get("OMNIVOICE_ASR_BACKEND"):
        return True
    from core import prefs
    return bool(prefs.get("asr_backend"))


class ASRModelMissingError(RuntimeError):
    """A fallback ASR selection has no installed weights (see
    :func:`load_active_asr_backend`). Carries the typed ``asr_model_missing``
    ``payload`` so consumers render the same one-click download CTA as the
    initial preflight instead of a generic load failure — and, critically, so
    ``ensure_loaded()`` is never reached for that candidate (loading would
    silently auto-download multi-GB weights, violating the local-first
    no-download-without-consent guarantee)."""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(asr_model_missing_detail(payload))


def load_active_asr_backend(*, asr_pipe=None, require_installed: bool = False) -> ASRBackend:
    """:func:`get_active_asr_backend` + eager ``ensure_loaded()``, degrading
    past backends whose deep import chain is broken (#1185).

    ``is_available()`` is a shallow probe (``import whisperx`` succeeds even
    with broken transitive deps, because pyannote/pytorch_lightning only
    import inside ``load_model``), so auto-detect can pick a backend that then
    dies at load with ``No module named 'lightning_fabric'`` — which used to
    fail ASR init wholesale even though the next engine in line works fine.
    Instead: record the backend as broken (Model Catalogue shows why),
    re-select, and load the next candidate — mirroring how
    :func:`_probe_available` already swallows broken natives at probe time.

    An *explicitly pinned* backend (``OMNIVOICE_ASR_BACKEND`` / the
    ``asr_backend`` pref) is never silently swapped: the enriched error —
    naming the missing module and the repair command — is raised instead.

    Callers run the no-download :func:`asr_model_missing_error` preflight for
    the *initial* selection only, so every re-selected fallback gets the same
    preflight here, BEFORE its ``ensure_loaded()`` — otherwise a broken
    primary would let the fallback silently auto-download multi-GB weights.
    A fallback without installed weights raises :class:`ASRModelMissingError`
    (typed payload → the caller's download CTA).
    """
    from core.scrub import scrub_text
    tried: set[str] = set()
    while True:
        backend = get_active_asr_backend(asr_pipe=asr_pipe)
        bid = getattr(backend, "id", "?")
        if tried or require_installed:
            # Preflight the SPECIFIC candidate about to load — not the global
            # selection, which can disagree when an asr_pipe steers
            # get_active_asr_backend (Greptile review, #1198).
            missing = asr_model_missing_error(backend_id=bid, require_installed=require_installed)
            if missing is not None:
                raise ASRModelMissingError(missing)
        try:
            backend.ensure_loaded()
            from core.device_caps import detect_host_caps
            from services.engine_evidence import snapshot as execution_snapshot
            from services.engine_routing import routing_fields

            cls = type(backend)
            caps = detect_host_caps()
            routing = routing_fields(getattr(cls, "gpu_compat", ("cpu",)), caps)
            _RUNTIME_EVIDENCE[bid] = execution_snapshot(
                engine_id=bid,
                engine_cls=cls,
                instance=backend,
                routing=routing,
                caps=caps,
            )
            _RUNTIME_INSTANCES[bid] = backend
            return backend
        except ImportError as e:
            # ModuleNotFoundError and its ImportError parent ("cannot import
            # name X" version skew) are the same env-rot class: the backend
            # cannot work in this process, but siblings with independent
            # import chains can. Record it either way so Model Catalogue
            # reports the truth (unavailable + why + how to repair).
            reason = _deep_import_reason(type(backend), e)
            _DEEP_IMPORT_BROKEN[bid] = scrub_text(reason)
            _LAST_ERRORS[bid] = _DEEP_IMPORT_BROKEN[bid]
            if _asr_backend_pinned() or bid in tried:
                # Pinned engine (never silently swapped), or auto-detect has
                # no fresh candidate left (its last resort repeats) —
                # surface the actionable cause instead of looping.
                raise RuntimeError(reason) from e
            tried.add(bid)
            logger.warning(
                "ASR backend %r failed to load with a broken import chain "
                "(%s) — marking it unavailable and falling through to the "
                "next engine (#1185)", bid, e,
            )


# ── Reference-transcript cache (#1032) ──────────────────────────────────────
# `get_active_asr_backend()` returns a FRESH backend instance per call for the
# whisper family, so every `transcribe_reference` used to reload whisper
# weights from scratch — a multi-second (CPU: tens of seconds) hit on EVERY
# /generate whose reference clip has no stored transcript (#308 introduced the
# call; profiles saved without a transcript hit it per request). The reference
# audio is identical across those requests, so cache the *transcript* keyed by
# the file's content hash: no model or VRAM is held, repeated generates with
# the same clip skip ASR entirely. Bounded LRU; failures (None) are never
# cached so a transient ASR problem still retries next request.
_REF_TRANSCRIPT_CACHE_MAX = 64
_ref_transcript_cache: "OrderedDict[str, str]" = OrderedDict()
_ref_transcript_lock = threading.Lock()


def _ref_audio_fingerprint(audio_path: str) -> str | None:
    """sha256 of the clip's bytes, or None when unreadable (→ no caching).

    Content-keyed (not path-keyed) because ad-hoc clone uploads land in a new
    NamedTemporaryFile per request — the path changes, the bytes don't.
    Reference clips are seconds long, so hashing is negligible next to ASR."""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(audio_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _installed_reference_fallbacks(
    selected: list[ASRBackend],
) -> list[ASRBackend]:
    """Return the strongest compatible local fallbacks without changing prefs."""
    fallbacks: list[ASRBackend] = []
    selected_repos = {
        _fw_repo(str(getattr(item, "_model_name", "")))
        for item in selected
        if isinstance(item, FasterWhisperBackend)
    }
    try:
        from api.routers.setup.models import (
            KNOWN_MODELS,
            _model_supported,
            _snapshot_dirs,
            snapshot_is_complete,
        )

        available, _reason = FasterWhisperBackend.is_available()
        if available:
            compatible = sorted(
                (
                    model
                    for model in KNOWN_MODELS
                    if str(model.get("role", "")).lower() == "asr"
                    and not model.get("dictation_id")
                    and (
                        str(model.get("repo_id", "")).startswith("Systran/faster-")
                        or model.get("repo_id")
                        == "deepdml/faster-whisper-large-v3-turbo-ct2"
                    )
                    and _model_supported(model)
                    and model.get("repo_id") not in selected_repos
                ),
                key=lambda model: float(model.get("size_gb") or 0),
                reverse=True,
            )
            for model in compatible:
                snapshots = [
                    path
                    for path in _snapshot_dirs(str(model["repo_id"]))
                    if snapshot_is_complete(model, path)
                ]
                if not snapshots:
                    continue
                # A concrete complete snapshot cannot trigger a Hub download.
                snapshot = max(snapshots, key=lambda path: os.path.getmtime(path))
                backend = FasterWhisperBackend(model_name=snapshot)
                setattr(backend, "_reference_ephemeral", True)
                fallbacks.append(backend)
                break
    except Exception as exc:  # noqa: BLE001 - optional local fallback
        logger.warning("reference ASR cache fallback unavailable (%s)", exc)

    try:
        from services import sherpa_dictation

        selected_sherpa = {
            item.spec.id
            for item in selected
            if isinstance(item, SherpaDictationBackend)
        }
        installed = sorted(
            (
                spec
                for spec in sherpa_dictation.list_specs()
                if spec.id not in selected_sherpa
                and sherpa_dictation.is_installed(spec)
            ),
            key=lambda spec: float(spec.size_gb or 0),
            reverse=True,
        )
        if installed:
            fallbacks.append(get_sherpa_dictation_backend(installed[0].id))
    except Exception as exc:  # noqa: BLE001 - optional local fallback
        logger.warning("reference dictation cache fallback unavailable (%s)", exc)
    return fallbacks


def _transcribe_reference_candidates(
    candidates: list[ASRBackend], audio_path: str,
) -> str:
    for backend in candidates:
        try:
            result = backend.transcribe(audio_path, word_timestamps=False) or {}
            candidate_text = result.get("text") or " ".join(
                (seg.get("text") or "").strip()
                for seg in result.get("segments", [])
            )
            candidate_text = (candidate_text or "").strip()
            if candidate_text:
                return candidate_text
        except Exception as exc:  # noqa: BLE001 - try the next local engine
            logger.warning("transcribe_reference: %s failed (%s)", backend.id, exc)
        finally:
            if getattr(backend, "_reference_ephemeral", False):
                try:
                    backend.unload()
                except Exception:  # noqa: BLE001 - release is best-effort
                    logger.warning("reference ASR fallback unload failed", exc_info=True)
    return ""


def transcribe_reference(audio_path: str) -> str | None:
    """Transcribe a voice-clone reference clip with the active ASR backend.

    Voice cloning without a user-supplied transcript used to fall through to
    ``VoiceStudio.load_asr_model()`` — a transformers ``pipeline()`` load of
    whisper-large-v3-turbo that fails outright on transformers 5.3 (#308),
    even when whisperx / faster-whisper / mlx-whisper are installed and
    working. Route the reference transcript through the registry instead, so
    the model-attached pipeline is only reached when it is genuinely the last
    resort. Returns ``None`` on any failure — callers pass ``ref_text=None``
    through and the model's installed-only fallback still gets its chance.

    Results are cached by audio content (#1032) — see the cache notes above.
    """
    fingerprint = _ref_audio_fingerprint(audio_path)
    if fingerprint is not None:
        with _ref_transcript_lock:
            cached = _ref_transcript_cache.get(fingerprint)
            if cached is not None:
                _ref_transcript_cache.move_to_end(fingerprint)
                return cached
    # Prefer the selected offline ASR engine. When its selected weights are not
    # installed, reuse the selected dictation engine if that model is already
    # local. Short clone references need plain transcription, which dictation
    # engines provide well. Falling straight through to the TTS model's bundled
    # fallback produced incomplete reference conditioning for longer clips and
    # introduced spurious words at the start of short generations. Neither
    # branch may download weights implicitly.
    candidates: list[ASRBackend] = []
    offline_missing = asr_model_missing_error()
    if offline_missing is None:
        try:
            # `load_*`, not `get_*`: a backend whose shallow probe passes but
            # whose deep import chain is broken must fall through cleanly.
            backend = load_active_asr_backend()
            if not isinstance(backend, PyTorchWhisperBackend):
                candidates.append(backend)
        except Exception as e:  # noqa: BLE001 — reference ASR is best-effort
            logger.warning("transcribe_reference: offline ASR unavailable (%s)", e)

    capture_missing = asr_model_missing_error(purpose="dictation")
    if capture_missing is None:
        try:
            capture = get_capture_asr_backend()
            if not isinstance(capture, PyTorchWhisperBackend) and not any(
                type(item) is type(capture) and item.id == capture.id
                for item in candidates
            ):
                candidates.append(capture)
        except Exception as e:  # noqa: BLE001 — reference ASR is best-effort
            logger.warning("transcribe_reference: dictation ASR unavailable (%s)", e)

    text = _transcribe_reference_candidates(candidates, audio_path)
    fallbacks: list[ASRBackend] = []
    if not text:
        fallbacks = _installed_reference_fallbacks(candidates)
        text = _transcribe_reference_candidates(fallbacks, audio_path)

    if not candidates and not fallbacks:
        logger.info(
            "transcribe_reference: no installed ASR model available — skipping "
            "reference auto-transcription (no silent download)."
        )
        return None
    if not text:
        logger.warning(
            "transcribe_reference: installed ASR engines returned no transcript "
            "— deferring to the model's built-in ASR fallback"
        )
        return None
    if text and fingerprint is not None:
        with _ref_transcript_lock:
            _ref_transcript_cache[fingerprint] = text
            _ref_transcript_cache.move_to_end(fingerprint)
            while len(_ref_transcript_cache) > _REF_TRANSCRIPT_CACHE_MAX:
                _ref_transcript_cache.popitem(last=False)
    return text or None


_capture_backend: ASRBackend | None = None
# The sherpa model id the cached capture backend was built for, so a model
# switch in Settings rebuilds the singleton instead of serving the old model.
_capture_backend_key: str | None = None
# Guards the read-modify-write of the two globals above. Both the background
# capture-ASR preload (runs in the GPU-pool thread) and the live-dictation WS
# handlers (run on the event loop) resolve/replace the singleton, so the
# check-then-build must be atomic to avoid two threads each building a model.
_capture_backend_lock = threading.Lock()

# ── Idle release of the warm capture/dictation ASR (#1101 class) ────────────
#
# The TTS model has always been idle-unloaded (model_manager.idle_worker), but
# the capture ASR singleton above was not: once you dictated even once, its
# model stayed resident for the life of the process. Measured on a 16 GB M2:
# the backend sits at ~6.2 GB idle — TTS 3.8 GB plus ~2 GB of warm ASR — while
# an actual generate costs only ~116 MB on top. That baseline, not any spike, is
# what pushes a 16 GB machine into memory pressure until the OS kills the
# backend mid-generate — the death behind #1076/#1092/#1093/#1101. Freeing
# 3.8 GB of TTS while silently holding 2 GB of ASR forever was the asymmetry.
#
# Reclaiming it costs a model re-warm on the next dictation (~1.4 s for
# mlx-whisper turbo) and only after a full idle timeout — the same bargain the
# TTS model already makes.
_capture_last_used: float = 0.0
# Live dictation streams hold the singleton for the WHOLE session while calling
# nothing that would refresh `_capture_last_used`, so a long session could have
# its model unloaded mid-sentence. A lease pins it for exactly that window.
_capture_leases: int = 0


def _touch_capture() -> None:
    """Mark the capture backend as used now (resets its idle clock)."""
    global _capture_last_used
    _capture_last_used = time.monotonic()


@contextlib.contextmanager
def capture_lease():
    """Pin the warm capture backend for the duration of a live session, so the
    idle reaper can never unload the model out from under an open dictation
    stream. Releasing the lease restarts the idle clock."""
    global _capture_leases
    with _capture_backend_lock:
        _capture_leases += 1
    try:
        yield
    finally:
        with _capture_backend_lock:
            _capture_leases = max(0, _capture_leases - 1)
        _touch_capture()


def release_idle_capture_backend(idle_s: float, *, now: float | None = None) -> bool:
    """Unload the warm capture/dictation ASR once it has gone unused for
    ``idle_s`` seconds. Returns True when a model was actually released.

    No-ops while a live session holds a lease, when nothing is loaded, or when
    the model was used recently. Never raises — a failed unload must not take
    the idle worker down with it."""
    global _capture_backend, _capture_backend_key
    now = time.monotonic() if now is None else now
    with _capture_backend_lock:
        if _capture_backend is None or _capture_leases > 0:
            return False
        if now - _capture_last_used < idle_s:
            return False
        backend, _capture_backend, _capture_backend_key = _capture_backend, None, None
    try:
        backend.unload()
    except Exception:  # noqa: BLE001 — a stuck unload must not kill idle_worker
        logger.warning("capture ASR unload failed", exc_info=True)
    logger.info(
        "Idle timeout reached. Unloading capture ASR (%s) to free memory.",
        type(backend).__name__,
    )
    return True


def get_sherpa_dictation_backend(model_id: str) -> "SherpaDictationBackend":
    """Return a shared, warm-cached :class:`SherpaDictationBackend` for
    ``model_id``, building it at most once and reusing the recognizer across
    live-dictation WS sessions.

    Live sessions previously constructed a FRESH backend per WebSocket connect,
    so every session reloaded the ONNX recognizer (1.3–2.5s "loading…") and the
    #888 background preload was a no-op. This reuses the SAME module-level
    ``_capture_backend`` singleton the preload warms (when the ids match), and
    rebuilds on a model switch — identical invalidation to
    :func:`get_capture_asr_backend`. Thread-safe: the recognizer is shared;
    each session creates its own decode stream (see capture_ws)."""
    global _capture_backend, _capture_backend_key
    from services.performance_profiles import requested_tier

    performance_tier = requested_tier("dictation")
    _touch_capture()  # any handout resets the idle clock
    with _capture_backend_lock:
        if (isinstance(_capture_backend, SherpaDictationBackend)
                and _capture_backend_key == model_id
                and _capture_backend.performance_tier == performance_tier):
            return _capture_backend
        backend = SherpaDictationBackend(model_id=model_id)
        _capture_backend = backend
        _capture_backend_key = model_id
        return backend


def sherpa_engine_model_id() -> str:
    """The sherpa model the ``sherpa-onnx-asr`` engine loads when nothing pins
    one explicitly: env var (power-user pin) → the dictation model the user
    picked in Settings / the Engines menu → the catalogue default.

    Unlike :func:`dictation_model_id` this ignores ``dictation.enabled`` — a
    user who turned the hotkey off but chose the Sherpa engine for dub/batch
    transcription still means *this* model — and never returns None: the
    engine needs *some* model to construct. A demoted model (decoded nothing
    on this host) falls through to the default rather than being re-picked.
    """
    from services import sherpa_dictation as _sd
    explicit = os.environ.get("OMNIVOICE_SHERPA_ASR_MODEL")
    if explicit:
        return explicit
    try:
        from core import prefs
        mid = prefs.get("dictation.model_id")
    except Exception:  # noqa: BLE001 — prefs store unavailable → default
        return _sd.DEFAULT_MODEL_ID
    if _sd.is_sherpa_model(mid) and not _sd.is_demoted(mid):
        return _sd.get_spec(mid).id
    return _sd.DEFAULT_MODEL_ID


def dictation_model_id() -> str | None:
    """The selected sherpa dictation model id, or None when dictation is off /
    no sherpa model is chosen. Env var wins (power-user pin), then prefs."""
    explicit = os.environ.get("OMNIVOICE_SHERPA_ASR_MODEL")
    if explicit:
        return explicit
    try:
        from core import prefs
        if not prefs.get("dictation.enabled", True):
            return None
        mid = prefs.get("dictation.model_id")
    except Exception:
        return None
    from services.sherpa_dictation import is_demoted, is_sherpa_model
    if not is_sherpa_model(mid):
        return None
    if is_demoted(mid):
        # This model was observed decoding nothing on this machine (see
        # sherpa_dictation.demote_model). Returning None routes dictation to
        # the capture ASR engine, which works — silently degrading to a slower
        # engine beats confidently selecting one that returns no text at all.
        logger.warning(
            "dictation model %s is demoted (produced no text on this machine) "
            "— using the capture ASR engine instead", mid,
        )
        return None
    return mid


def _parakeet_mlx_installed() -> bool:
    """True only when the parakeet-mlx model weights are ALREADY on disk.

    The capture picker prefers Parakeet TDT v3 on Apple Silicon, but only when
    it costs nothing: like every whisper-family backend, parakeet-mlx
    auto-downloads from HF on first load, and the capture path must never
    trigger a surprise multi-GB download (the asr_model_missing contract).
    Installed state comes from the same HF-cache helpers the model store uses
    (positive results memoized — see :func:`_repo_installed`), so the answer
    matches the Model Catalogue's install badges. Never raises.
    """
    try:
        repo = os.environ.get("ASR_MODEL_PARAKEET_MLX", _PARAKEET_MLX_DEFAULT)
        return _repo_installed(repo)
    except Exception:  # noqa: BLE001 — a broken check must not break the picker
        logger.warning("parakeet-mlx installed-check failed", exc_info=True)
        return False


#: The 25 (European) languages Parakeet TDT 0.6B v3 supports (NVIDIA model
#: card). Everything else — CJK, Arabic, Hindi, … — is whisper-only.
_PARAKEET_MLX_LANGS = frozenset({
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu",
    "it", "lt", "lv", "mt", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv",
    "uk",
})


def _locale_language() -> str | None:
    """Primary language subtag of the process locale (``de_DE.UTF-8`` → ``de``),
    or None when no usable locale is set (C/POSIX, empty — e.g. a launchd GUI
    environment). Same stdlib-only signal endpoint_race's probe-order hint
    uses. Never raises."""
    cands: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(key)
        if v:
            cands.append(v)
    try:
        import locale as _locale
        cands.extend(x for x in _locale.getlocale() if x)
    except Exception:  # noqa: BLE001 — locale probing is best-effort
        pass
    for cand in cands:
        lang = re.split(r"[_\-.@]", cand.strip().lower(), maxsplit=1)[0]
        if lang and lang not in ("c", "posix"):
            return lang
    return None


def _capture_prefers_parakeet() -> bool:
    """Whether the capture picker should auto-prefer parakeet-mlx right now.

    Three gates, cheapest first: the backend is available (Apple Silicon +
    package), the user's language is covered, and the weights are already on
    disk (never a surprise download).

    Language-parity rule (smallest honest rule — there is no explicit
    dictation-language setting, the REST ``language`` field is an unused
    hint): Parakeet TDT v3 knows exactly 25 (European) languages, while the
    mlx-whisper tier it replaces covers ~100 — so auto-prefer Parakeet only
    when the OS locale (the one signal we have) names a covered language.
    No usable locale, or a non-covered one (CJK, Arabic, …) → keep whisper:
    installing a 25-language engine must never silently break dictation that
    worked yesterday. Pinning ``ASR_MODEL_PARAKEET_MLX`` explicitly bypasses
    the language gate (the user chose the engine; trust them).
    """
    ok, _ = ParakeetMLXBackend.is_available()
    if not ok:
        return False
    if not os.environ.get("ASR_MODEL_PARAKEET_MLX") \
            and _locale_language() not in _PARAKEET_MLX_LANGS:
        return False
    return _parakeet_mlx_installed()


def get_capture_asr_backend(*, skip_sherpa: bool = False) -> ASRBackend:
    """Pick the fastest ASR engine for capture / dictation.

    Selection order:

      0. sherpa-onnx dictation — when ``dictation.model_id`` names one of the
         seven sherpa models (live/CPU; the new live-dictation path).
      1. parakeet-mlx          — Apple Silicon, only when the model is ALREADY
                                 installed (never a surprise download) AND the
                                 OS-locale language is one of Parakeet's 25
                                 (European) languages — see
                                 :func:`_capture_prefers_parakeet`; a CJK/etc
                                 locale keeps the multilingual whisper tier
                                 below (language parity). TDT decoding is
                                 dictation-grade fast on the GPU.
      2. mlx-whisper Turbo     — Apple Silicon, ~5× faster than large-v3
      3. mlx-whisper large     — still native Metal, faster than CPU int8
      4. faster-whisper        — cross-platform CTranslate2 fallback
      5. pytorch-whisper       — last resort

    The caller should also pass ``word_timestamps=False`` to the returned
    backend to skip per-word timing and shave another ~30% latency.

    Returns a cached singleton so the model stays warm between calls; the
    singleton is rebuilt if the selected sherpa model changes.

    ``skip_sherpa`` is used only to validate a token-silent Sherpa result with
    the installed capture fallback before persisting model demotion.
    """
    global _capture_backend, _capture_backend_key

    _touch_capture()  # any handout resets the idle clock (#1101 class)
    # Atomic resolve+build so the preload thread and a WS session (which may
    # call get_sherpa_dictation_backend concurrently) can't both build a model.
    with _capture_backend_lock:
        # 0. Honor an explicit sherpa dictation model selection.
        sherpa_id = None if skip_sherpa else dictation_model_id()
        if sherpa_id:
            ok, _ = SherpaDictationBackend.is_available()
            if ok:
                from services.performance_profiles import requested_tier
                performance_tier = requested_tier("dictation")
                if not (isinstance(_capture_backend, SherpaDictationBackend)
                        and _capture_backend_key == sherpa_id
                        and _capture_backend.performance_tier == performance_tier):
                    try:
                        _capture_backend = SherpaDictationBackend(model_id=sherpa_id)
                        _capture_backend_key = sherpa_id
                    except Exception as e:  # noqa: BLE001 — fall through to Whisper
                        logger.warning(
                            "sherpa dictation model %r unavailable (%s) — falling "
                            "back to Whisper capture engine", sherpa_id, e,
                        )
                        _capture_backend = None
                        _capture_backend_key = None
                if _capture_backend is not None:
                    return _capture_backend
            else:
                logger.info(
                    "dictation.model_id=%r selected but sherpa-onnx not installed — "
                    "falling back to Whisper capture engine", sherpa_id,
                )

        # Prefer an already-installed Parakeet TDT v3 on Apple Silicon (when
        # the language gate allows it — see _capture_prefers_parakeet). Gated
        # on the weights being on disk so this NEVER triggers a download —
        # users opt in by installing the model from the engine's Weights list in Model Catalogue. The
        # gate's answer is part of the warm-singleton key so installing
        # parakeet mid-session rebuilds the singleton instead of serving the
        # stale whisper pick until restart (the memo in _repo_installed keeps
        # the repeated check cheap once it turns positive).
        prefer_parakeet = _capture_prefers_parakeet()
        auto_key = f"auto:parakeet={int(prefer_parakeet)}"
        if _capture_backend is not None and _capture_backend_key == auto_key:
            return _capture_backend

        if prefer_parakeet:
            _capture_backend = ParakeetMLXBackend()
            _capture_backend_key = auto_key
            return _capture_backend

        # Prefer MLX Turbo on Apple Silicon
        ok, _ = MLXWhisperBackend.is_available()
        if ok:
            _capture_backend = MLXWhisperBackend(model_name=_MLX_MODEL_TURBO)
            _capture_backend_key = auto_key
            return _capture_backend

        # Fall back to faster-whisper (CPU int8 on non-Apple)
        ok, _ = FasterWhisperBackend.is_available()
        if ok:
            _capture_backend = FasterWhisperBackend()
            _capture_backend_key = auto_key
            return _capture_backend

        # Last resort
        _capture_backend = PyTorchWhisperBackend()
        _capture_backend_key = auto_key
        return _capture_backend


# ── No-ASR-installed preflight (TTS-only installs) ──────────────────────────
#
# Only the TTS model is required (models.yaml): a fresh install legitimately
# has NO ASR model on disk. Every whisper-family backend above happily
# *auto-downloads* its weights from HF on first load (faster_whisper's
# WhisperModel, mlx_whisper, whisperx and the transformers pipeline all
# default to download-on-miss), so an ASR-less install that hit dub / batch /
# dictation either silently pulled a multi-GB model or died with an opaque
# error offline. Consumers call :func:`asr_model_missing_error` BEFORE any
# backend is constructed or loaded and turn the typed payload into an
# actionable 409 / SSE / WS error carrying a one-click download CTA.

#: Machine-readable error id — the frontend keys its download-CTA UI on this.
ASR_MODEL_MISSING = "asr_model_missing"

_PYTORCH_ASR_DEFAULT = "openai/whisper-large-v3-turbo"
_FASTER_WHISPER_DEFAULT = "Systran/faster-whisper-large-v3"


def faster_whisper_model_id() -> str:
    """Resolve the UI-selected CTranslate2 model, with env pins authoritative."""
    from core import prefs

    return str(
        prefs.resolve(
            "asr_model_faster",
            env="ASR_MODEL_FASTER",
            default=_FASTER_WHISPER_DEFAULT,
        )
    )


def select_faster_whisper_model(repo_id: str) -> None:
    """Persist and apply a CTranslate2 model selection for this process."""
    from core import prefs

    if prefs.is_env_shadowed("ASR_MODEL_FASTER"):
        raise ValueError("ASR_MODEL_FASTER is set outside VoiceStudio")
    prefs.set_("asr_model_faster", repo_id)
    # Sidecars inherit the process environment. Updating it here makes the
    # selection effective immediately as well as after the next app launch.
    os.environ["ASR_MODEL_FASTER"] = repo_id
    instance = _ISOLATED_INSTANCES.pop("faster-whisper-isolated", None)
    if instance is not None:
        instance.shutdown()

# faster-whisper / WhisperX short model aliases → the HF repo they download.
# Covers our own defaults plus the documented size aliases; an unrecognized
# alias returns None and the preflight stays out of the way (never blocks).
_FW_ALIAS_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}


def _fw_repo(name: str) -> str | None:
    """HF repo for a faster-whisper/WhisperX model name (alias or repo id)."""
    name = (name or "").strip()
    return name if "/" in name else _FW_ALIAS_REPOS.get(name.lower())


def _offline_asr_repo(backend_id: str | None = None) -> str | None:
    """The HF repo the active *offline* (dub/batch) ASR backend would download
    on first load, or None when the selection can't be preflighted (FunASR /
    NeMo / Moonshine / OpenAI-compat are explicit opt-ins — stay out of the
    way there). ``backend_id`` pins the check to a specific backend — the
    fallback loop in :func:`load_active_asr_backend` passes the candidate it
    is actually about to load, which can differ from ``active_backend_id()``
    when a preloaded ``asr_pipe`` steers selection (Greptile review, #1198)."""
    bid = backend_id or active_backend_id()
    if bid == "whisperx":
        return _fw_repo(os.environ.get("ASR_MODEL_WHISPERX", "large-v3"))
    if bid == "faster-whisper":
        return _fw_repo(faster_whisper_model_id())
    if bid == "faster-whisper-isolated":
        # Mirror the sidecar's own resolution (_asr_sidecar/main.py):
        # ASR_MODEL_FW is a sidecar-only override, otherwise the shared
        # ASR_MODEL_FASTER selection applies — so the preflight can never
        # download a different repo than the sidecar will load.
        return _fw_repo(
            os.environ.get("ASR_MODEL_FW")
            or faster_whisper_model_id()
            or _FASTER_WHISPER_DEFAULT
        )
    if bid == "mlx-whisper":
        return os.environ.get("ASR_MODEL", _MLX_MODEL_DEFAULT)
    if bid == "parakeet-mlx":
        return os.environ.get("ASR_MODEL_PARAKEET_MLX", _PARAKEET_MLX_DEFAULT)
    if bid == "sherpa-onnx-asr":
        # The offline sherpa backend loads the configured dictation model
        # (same resolution as SherpaDictationBackend.__init__ with no args).
        # Unknown/none → fail open.
        try:
            from services import sherpa_dictation as _sd
            spec = _sd.get_spec(sherpa_engine_model_id())
            return spec.repo_id if spec is not None else None
        except Exception:  # noqa: BLE001 — preflight must stay best-effort
            return None
    if bid == "pytorch-whisper":
        return os.environ.get("OMNIVOICE_PYTORCH_ASR_MODEL", _PYTORCH_ASR_DEFAULT)
    return None


def _capture_whisper_repo() -> str | None:
    """The HF repo :func:`get_capture_asr_backend`'s non-sherpa fallback chain
    would download — same order, but WITHOUT constructing a backend. ``None``
    means the selection can't be preflighted (the caller fails open)."""
    # Mirrors the picker's parakeet-mlx step exactly (availability + installed
    # weights + the language gate): because that step is gated on the weights
    # being installed, when it wins the preflight is trivially satisfied
    # (installed state is what the gate checked).
    if _capture_prefers_parakeet():
        return os.environ.get("ASR_MODEL_PARAKEET_MLX", _PARAKEET_MLX_DEFAULT)
    ok, _ = MLXWhisperBackend.is_available()
    if ok:
        return _MLX_MODEL_TURBO
    ok, _ = FasterWhisperBackend.is_available()
    if ok:
        # An unrecognized-but-valid alias (a name faster_whisper itself can
        # resolve but our alias table doesn't know) yields None here — FAIL
        # OPEN rather than coerce to the default repo and demand a download
        # of a model the user never picked.
        return _fw_repo(faster_whisper_model_id())
    return os.environ.get("OMNIVOICE_PYTORCH_ASR_MODEL", _PYTORCH_ASR_DEFAULT)


def _recommended_asr_model(
    purpose: str, missing_repo: str | None, *, prefer_sherpa: bool = True,
    excluded_sherpa_model_id: str | None = None,
) -> dict | None:
    """The catalog entry to offer in the download CTA.

    Offline: the missing repo itself when it's in the catalog (guarantees
    download → retry succeeds), else the first curated + host-supported
    non-sherpa ASR pick. Dictation: the curated sherpa dictation entry (the
    payload's ``dictation_id`` lets the client also set ``dictation.model_id``
    so a retry picks it up); when sherpa-onnx isn't importable the Whisper
    fallback repo is recommended instead.
    """
    from api.routers.setup.models import KNOWN_MODELS, _model_curated, _model_supported

    def _shape(m: dict) -> dict:
        rec = {"repo_id": m["repo_id"], "label": m["label"], "size_gb": m["size_gb"]}
        if m.get("dictation_id"):
            rec["dictation_id"] = m["dictation_id"]
        return rec

    by_id = {m["repo_id"]: m for m in KNOWN_MODELS}
    exact = by_id.get(missing_repo) if missing_repo else None

    def _eligible(m: dict, *, sherpa: bool) -> bool:
        if (m.get("engine") == "sherpa-onnx") != sherpa:
            return False
        if sherpa and m.get("dictation_id") == excluded_sherpa_model_id:
            return False
        return _model_supported(m)

    if purpose != "dictation":
        if exact is not None and _model_supported(exact):
            return _shape(exact)
        prefer_sherpa = False

    if purpose == "dictation" and prefer_sherpa:
        ok, _ = SherpaDictationBackend.is_available()
        if ok:
            if exact is not None and _eligible(exact, sherpa=True):
                return _shape(exact)
            for m in KNOWN_MODELS:
                if (m.get("role") == "ASR" and _eligible(m, sherpa=True)
                        and _model_curated(m)):
                    return _shape(m)

    # No usable Sherpa recommendation remains (runtime unavailable, explicit
    # fallback probe, or the sole curated entry is the demoted model). Offer
    # the exact capture fallback so download → retry cannot loop.
    if exact is not None and _eligible(exact, sherpa=False):
        return _shape(exact)
    for m in KNOWN_MODELS:
        if m.get("role") != "ASR":
            continue
        if _eligible(m, sherpa=False) and _model_curated(m):
            return _shape(m)
    return None


#: Repos confirmed installed this session (positive-only memo). Installs only
#: ADD models, so no invalidation is needed — and dictation utterances /
#: generates stop paying a full ``scan_cache_dir`` walk on every call once a
#: repo has been confirmed once. (A user deleting a model mid-session degrades
#: to the pre-preflight behaviour for that repo: fail open, auto-download on
#: next use.) Test fixtures that stub ``is_cached`` clear this between tests.
_INSTALLED_REPO_MEMO: set[str] = set()


def _repo_installed(repo: str, *, refresh: bool = False) -> bool:
    """``is_cached`` + ``cache_is_complete`` with a positive-only session memo.

    Installed state comes from the same HF-cache helpers the model store uses,
    so the answer matches the Model Catalogue → Models install badges."""
    if not refresh and repo in _INSTALLED_REPO_MEMO:
        return True
    from api.routers.setup.models import cache_is_complete, get_model_catalog, is_cached
    meta = get_model_catalog().get(repo) or {"repo_id": repo}
    if is_cached(repo) and cache_is_complete(meta):
        _INSTALLED_REPO_MEMO.add(repo)
        return True
    return False


def asr_model_missing_error(*, purpose: str = "transcribe",
                            sherpa_model_id: str | None = None,
                            backend_id: str | None = None,
                            skip_sherpa: bool = False,
                            require_installed: bool = False) -> dict | None:
    """None when the active ASR selection can transcribe without downloading
    anything; otherwise the typed ``{"error": "asr_model_missing", ...}``
    payload for a 409 / SSE / WS error with a download CTA.

    ``purpose="dictation"`` mirrors the capture selection order (sherpa pref →
    parakeet-mlx → MLX turbo → faster-whisper → pytorch); anything else uses
    the offline dub/batch selection (:func:`active_backend_id`).
    ``sherpa_model_id`` lets the live-dictation WS pass its per-session
    ``?model=`` override. Installed state comes from the same HF-cache helpers
    the model store uses (see :func:`_repo_installed`), so the answer matches
    the Model Catalogue's install badges.
    ``skip_sherpa`` probes only the non-Sherpa capture fallback; silent-model
    recovery uses it before deciding whether persistent demotion is warranted.
    ``require_installed`` makes unknown/custom selections fail closed for that
    recovery path so it can never turn the normal fail-open policy into an
    implicit model download.

    FAIL-OPEN rule: a repo the model catalog doesn't know (a custom
    ``ASR_MODEL_*`` pin, pytorch-whisper's default repo, an unrecognized
    alias) returns None — the download CTA can only install catalog entries,
    so a payload here would trap the user in an un-installable CTA loop; the
    previous auto-download behaviour is the honest fallback. Never raises —
    a broken preflight must degrade to the old behaviour, not block ASR.
    """
    try:
        prefer_sherpa_recommendation = not skip_sherpa
        excluded_sherpa_model_id = None
        if purpose == "dictation":
            sid = None if skip_sherpa else (sherpa_model_id or dictation_model_id())
            if sid:
                ok, _ = SherpaDictationBackend.is_available()
                if ok:
                    from services import sherpa_dictation as _sd
                    spec = _sd.get_spec(sid)
                    # A recognizer observed returning silence must follow the
                    # same capture fallback as execution, even when the
                    # frontend keeps sending its persisted `?model=` value.
                    if spec is not None:
                        if _sd.is_demoted(spec.id):
                            excluded_sherpa_model_id = spec.id
                        else:
                            if _sd.is_installed(spec):
                                return None
                            return {
                                "error": ASR_MODEL_MISSING,
                                "missing_repo_id": spec.repo_id,
                                "recommended": _recommended_asr_model(
                                    purpose, spec.repo_id,
                                ),
                            }
            repo = _capture_whisper_repo()
        else:
            repo = _offline_asr_repo(backend_id)
        if repo is None:
            if require_installed:
                return {
                    "error": ASR_MODEL_MISSING,
                    "missing_repo_id": "unresolved-capture-fallback",
                    "recommended": None,
                }
            return None  # explicit opt-in engine — can't (and shouldn't) preflight
        from api.routers.setup.models import get_model_catalog
        if require_installed:
            if _repo_installed(repo, refresh=True):
                return None
            return {
                "error": ASR_MODEL_MISSING,
                "missing_repo_id": repo,
                "recommended": _recommended_asr_model(
                    purpose, repo,
                    prefer_sherpa=prefer_sherpa_recommendation,
                    excluded_sherpa_model_id=excluded_sherpa_model_id,
                ),
            }
        if get_model_catalog().get(repo) is None:
            return None  # not installable from the CTA — fail open (see docstring)
        if _repo_installed(repo):
            return None
        return {
            "error": ASR_MODEL_MISSING,
            "missing_repo_id": repo,
            "recommended": _recommended_asr_model(
                purpose, repo,
                prefer_sherpa=prefer_sherpa_recommendation,
                excluded_sherpa_model_id=excluded_sherpa_model_id,
            ),
        }
    except Exception:  # noqa: BLE001 — preflight is best-effort, never a blocker
        if require_installed:
            logger.warning("ASR install preflight failed; refusing implicit download", exc_info=True)
            return {
                "error": ASR_MODEL_MISSING,
                "missing_repo_id": "unverified-local-model",
                "reason": "verification_failed",
                "recommended": None,
            }
        logger.warning("ASR install preflight failed — proceeding without it",
                       exc_info=True)
        return None


def asr_model_missing_detail(payload: dict) -> str:
    """Human-readable (English) fallback message for the typed payload —
    what legacy clients / logs see; the frontend renders its own i18n copy."""
    if payload.get("reason") == "verification_failed":
        return ("Could not verify the local speech-to-text model. "
                "Check Settings > Logs > Backend, then retry. No model was downloaded.")
    rec = payload.get("recommended") or {}
    if rec.get("label"):
        return (
            "No speech-to-text model is installed. Download "
            f"{rec['label']} ({rec['size_gb']} GB) from the engine's Weights list in Model Catalogue, "
            "then retry."
        )
    return ("No speech-to-text model is installed. Download one from "
            "the engine's Weights list in Model Catalogue, then retry.")
