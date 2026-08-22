import os
import io
import re
import uuid
import time
import random
import asyncio
import tempfile
import contextlib
import logging
import traceback
from typing import Optional
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import sqlite3
from core.db import db_conn, ensure_schema
from core.config import OUTPUTS_DIR, VOICES_DIR
import functools
from services.model_manager import (
    get_model, _gpu_pool, run_on_gpu_pool_guarded, GpuJobTimeoutError,
    GpuPoolBusyError,
)
from services.audio_io import _safe_torchaudio_save
from services.binary_preflight import InvalidBinaryError
from core import event_bus
from core.logging_utils import log_safe
from omnivoice.utils.voice_design import heal_design_instruct

router = APIRouter()
logger = logging.getLogger("omnivoice.generate")


def _profile_instruct(row):
    """Validator-safe instruct for a stored profile row.

    Sanitizes the persisted instruct (dropping the ``"[object Object]"``
    sentinel / freeform prose that older builds saved) and, for a design row,
    rebuilds the tags from ``vd_states`` when the stored value is unusable — so
    a poisoned/legacy profile never 400-s generation (#550 #571 #594 #596).
    """
    try:
        vd = row["vd_states"]
    except (KeyError, IndexError):
        vd = None
    return heal_design_instruct(row["instruct"], vd)


def _note_generate_progress() -> None:
    """Tell the pool guard this render just finished a unit of work (#1391).

    Every multi-part render calls this after each part. A job that keeps
    completing chunks is working, however slowly, and must not be abandoned as
    "too heavy for the available compute" the way #1338/#1348/#1391 were —
    while a job that produces nothing for a whole base budget still dies on
    time. Never raises: a liveness signal that can break a render is worse
    than no signal.
    """
    try:
        from services.model_manager import report_generate_progress

        report_generate_progress()
    except Exception:  # noqa: BLE001 — diagnostics must not break synthesis
        pass


def _render_with_pauses(gen_span, segments, sample_rate):
    """Synthesize ``[(text, pause_ms), ...]`` spans and stitch silence between
    them (issue #276).

    ``gen_span(text) -> torch.Tensor`` synthesizes one text span (raw model
    output). A silence buffer of ``pause_ms`` is inserted after a span when
    requested, matching the audio tensor's channel dims / dtype / device.
    Returns the concatenated waveform. Kept model-free (``gen_span`` is injected)
    so the stitching is unit-testable without loading the TTS model.
    """
    import torch

    items = []  # ('a', tensor) for audio, ('s', n_samples) for silence
    for span_text, pause_ms in segments:
        if span_text and span_text.strip():
            items.append(("a", gen_span(span_text)))
            _note_generate_progress()
        if pause_ms > 0:
            n = int(round(sample_rate * pause_ms / 1000.0))
            if n > 0:
                items.append(("s", n))

    ref = next((t for kind, t in items if kind == "a"), None)
    if ref is None:
        # No speakable text (e.g. the input was only pause markers) — emit the
        # requested silence so the caller still gets a valid clip.
        total = sum(n for kind, n in items if kind == "s") or 1
        return torch.zeros(total, dtype=torch.float32)

    parts = []
    for kind, val in items:
        if kind == "a":
            parts.append(val)
        else:
            shape = list(ref.shape)
            shape[-1] = val
            parts.append(torch.zeros(*shape, dtype=ref.dtype, device=ref.device))
    return torch.cat(parts, dim=-1)


def _sanitize_audio(audio_out):
    """Replace non-finite samples (NaN / ±inf) with silence so a model glitch
    can't produce an unreadable WAV (#629). Returns the input unchanged when it's
    already finite or isn't a tensor. Never raises."""
    try:
        import torch
        if torch.is_tensor(audio_out) and not bool(torch.isfinite(audio_out).all()):
            logger.warning(
                "Generated audio contained non-finite samples (NaN/inf) — "
                "sanitizing to silence to keep the WAV decodable (#629)."
            )
            return torch.nan_to_num(audio_out, nan=0.0, posinf=0.0, neginf=0.0)
    except Exception as exc:
        logger.warning("Generated audio validation failed")
        raise RuntimeError(
            "Generated audio could not be validated. Retry the generation."
        ) from exc
    return audio_out


def _apply_effect_chain(audio_out, sample_rate, effect_preset, *, skip_mastering=False):
    """Shared post-DSP for /generate: preset validation → mastering →
    effect chain → loudness normalization.

    ``skip_mastering`` honors a backend's ``applies_own_mastering`` flag
    (issue #312): studio engines (e.g. VoxCPM2's native 48 kHz output)
    opt out of the broadcast highpass + Compressor pre-stage that's tuned
    for VoiceStudio's 24 kHz clone output. Loudness normalization still runs —
    it's a benign peak scale. Mirrors ``_run_tts`` in openai_compat.py.
    """
    from services.audio_dsp import (
        EFFECT_PRESETS, apply_mastering, normalize_audio,
        apply_effects_chain, get_effect_chain,
    )

    # #629: a numerical glitch in the model (observed on MPS) can leave NaN/±inf
    # samples, which write an unreadable WAV that then fails decoding with an
    # opaque "ffmpeg returned error code: 183 / Invalid data" — surfaced to the
    # user as a misleading "ran out of memory". Replace non-finite samples with
    # silence here, before any DSP/encode touches the audio, so the output is
    # always a valid WAV. Covers the raw path too (it returns just below).
    audio_out = _sanitize_audio(audio_out)

    preset = effect_preset or "broadcast"
    if preset not in EFFECT_PRESETS:
        raise ValueError(
            f"Unknown effect preset: {preset!r}. "
            f"Valid: {list(EFFECT_PRESETS.keys())}"
        )

    if preset == "raw":
        # Raw: skip all DSP — return raw model output
        return audio_out

    if not skip_mastering:
        audio_out = apply_mastering(audio_out, sample_rate=sample_rate)
    chain = get_effect_chain(preset)
    if chain:
        audio_out = apply_effects_chain(
            audio_out, sample_rate=sample_rate, chain=chain,
        )
    return normalize_audio(audio_out, target_dBFS=-2.0)


def _safe_exc_text(e: BaseException) -> str:
    """``f"{type(e).__name__}: {e}"`` — the house style used for
    unrecognized-error formatting throughout the backend (grep
    ``type(e).__name__`` in settings.py / asr_backend.py / model_manager.py
    / engines.py) — with a guard against leaking a raw container repr.

    #977: an AssertionError raised deep inside a vendored dependency
    (mlx-audio's Kokoro pipeline) had ``.args`` shaped like
    ``('du', {'a': 'American English', ...})`` — a tuple containing a dict.
    ``str(e)`` on that renders the WHOLE table straight into the user-facing
    message. Any engine's ``generate()`` can raise something shaped like
    this (not just Kokoro), so guard generically: if any element of
    ``e.args`` is a container rather than a plain string, don't interpolate
    ``str(e)`` at all — name the exception type and point at the log
    instead.
    """
    args = getattr(e, "args", ())
    if any(isinstance(a, (dict, list, tuple, set, frozenset)) for a in args):
        return f"{type(e).__name__} — see Settings → Logs → Backend for details"
    return f"{type(e).__name__}: {e}"


def _exception_chain(e):
    """Yield ``e`` plus every ``__cause__``/``__context__`` beneath it
    (cycle-safe). Engines and hub libraries routinely wrap the original
    transport/allocator error, so classification must look at the whole
    chain, not just the outermost message."""
    seen = set()
    stack = [e]
    while stack:
        exc = stack.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        yield exc
        stack.append(exc.__cause__)
        stack.append(exc.__context__)


# #880: transport-level exception type names from httpx (huggingface_hub ≥1.x
# downloads over it) and requests/urllib3 (older engine deps). Any of these
# anywhere in the exception chain means the network — not memory — killed the
# generation.
_NETWORK_EXC_NAMES = frozenset({
    # httpx
    "ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError",
    "WriteError", "WriteTimeout", "PoolTimeout", "NetworkError",
    "TransportError", "RemoteProtocolError", "ProxyError", "CloseError",
    # requests / urllib3
    "ConnectionError", "ChunkedEncodingError", "MaxRetryError",
    "NewConnectionError", "ProtocolError",
    # stdlib socket-level drops mid-download
    "ConnectionResetError", "ConnectionAbortedError", "ConnectionRefusedError",
    # huggingface_hub: failed first-use download with nothing in the disk cache
    "LocalEntryNotFoundError",
})

# Same class, but the transport error was stringified into a wrapper message
# (so the type name is gone). All lowercase; matched against .lower().
_NETWORK_MSG_SIGNATURES = (
    "client has been closed",    # httpx closed-client lifecycle error (#880)
    "cannot send a request",     # httpx: same error, message head
    "connection error",          # requests / huggingface_hub wording
    "connection reset",          # ECONNRESET mid-download
    "read timed out",            # requests/urllib3 timeout wording
    "max retries exceeded",      # urllib3 retry exhaustion
    "temporary failure in name resolution",  # DNS down (glibc)
    "name or service not known",             # DNS down (glibc)
    "getaddrinfo failed",                    # DNS down (Windows)
)

# #1335: a TLS connection cut mid-download. core/failure.py already classifies
# this for the dub/transcribe surfaces (TLS_CONNECTION_DROPPED, #1301), but
# /generate has its own taxonomy and never learned it — so the reporter got a
# bare 500 carrying `_ssl.c:1016`, which means nothing to anyone. It is a
# dropped download, so the network branch is the right owner: retry, not Flush.
#
# Gated on an "ssl" marker rather than matched bare (CodeRabbit): "eof occurred
# in violation of protocol" is OpenSSL's wording, but nothing stops an
# unrelated component from saying something similar, and mislabelling a local
# fault as a network problem sends the user to check their connection for a
# failure that has nothing to do with it. The real message always carries the
# marker: "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of
# protocol (_ssl.c:1016)".
_TLS_DROP_SIGNATURES = (
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
)


def _is_network_failure(e) -> bool:
    """True iff the failure (anywhere in its chain) is an HTTP-client
    lifecycle / network-transport error — e.g. a first-use model download
    from the HF Hub dying mid-generation (#880)."""
    for exc in _exception_chain(e):
        if type(exc).__name__ in _NETWORK_EXC_NAMES:
            return True
        low = str(exc).lower()
        if any(sig in low for sig in _NETWORK_MSG_SIGNATURES):
            return True
        if "ssl" in low and any(sig in low for sig in _TLS_DROP_SIGNATURES):
            return True
    return False


# Signatures of an *actual* out-of-memory condition. All lowercase.
_OOM_MSG_SIGNATURES = (
    "out of memory",               # CUDA / MPS / generic torch wording
    "not enough memory",           # torch CPU DefaultCPUAllocator
    "cannot allocate memory",      # OS-level ENOMEM
    "std::bad_alloc",              # C++ allocator failure
    "cublas_status_alloc_failed",  # cuBLAS workspace allocation
    "cuda_error_out_of_memory",    # raw CUDA driver error name
    "paging file is too small",    # Windows [WinError 1455] mapping DLLs
)


def _is_oom_failure(e) -> bool:
    """True iff the failure (anywhere in its chain) actually looks like an
    out-of-memory condition — the only case where the Flush hint is honest."""
    for exc in _exception_chain(e):
        if isinstance(exc, MemoryError):
            return True
        # torch.cuda.OutOfMemoryError subclasses RuntimeError; match by name
        # so this needs no torch import (and covers other frameworks' twins).
        if type(exc).__name__ == "OutOfMemoryError":
            return True
        low = str(exc).lower()
        if any(sig in low for sig in _OOM_MSG_SIGNATURES):
            return True
    return False


# #919: an engine that requires a model path / env var which isn't set (or is
# set to a directory missing its model files) fails with a *configuration*
# error, not a runtime one. The reporting user selected sherpa-onnx and hit
# "OMNIVOICE_SHERPA_MODEL not set. Point it to a sherpa-onnx TTS model
# directory …" — a pure setup problem — yet the OOM catch-all told them (on a
# 63 GB-RAM box) to press Flush for memory they never ran out of. Classify the
# whole CLASS of "engine not configured / required env var not set" errors so
# any current or future opt-in engine (sherpa/Confucius4/dots/MOSS …) surfaces
# actionable setup guidance instead of the memory hint. All lowercase; matched
# over the whole exception chain (engines wrap the original error).
_CONFIG_MSG_SIGNATURES = (
    "not set. point it to",      # sherpa: OMNIVOICE_SHERPA_MODEL not set
    "no model.onnx found in",    # sherpa: dir set but the model file is missing
    "not configured",            # generic "engine not configured" wording
    "venv not found. set",       # confucius4/dots/MOSS dedicated-venv opt-ins
    "unavailable: omnivoice_",   # is_available() reason wrapped by _ensure_loaded
)

# An OMNIVOICE_* engine env var named alongside "not set" / "point it to" /
# "set omnivoice_…" is the strongest config-missing signal and generalizes to
# any engine gated on such a var (issue #919 class).
_CONFIG_ENV_RE = re.compile(r"omnivoice_[a-z0-9_]+")


def _is_config_failure(e) -> bool:
    """True iff the failure is a *configuration* problem — a required engine
    model path / env var that isn't set (or points nowhere) — rather than a
    runtime fault. The remedy is to set the value, never to Flush VRAM."""
    for exc in _exception_chain(e):
        low = str(exc).lower()
        if any(sig in low for sig in _CONFIG_MSG_SIGNATURES):
            return True
        if _CONFIG_ENV_RE.search(low) and (
            "not set" in low or "point it to" in low or "set omnivoice_" in low
        ):
            return True
    return False


# Exception types that mean "the budget ran out", not "something broke".
# asyncio.TimeoutError is an alias of the builtin on 3.11+, but the engines'
# own wrappers are separate classes, so match by name across the chain.
_TIMEOUT_EXC_NAMES = frozenset({
    "TimeoutError", "GpuJobTimeoutError", "FuturesTimeoutError",
})

# Same class, stringified into a wrapper (all lowercase).
_TIMEOUT_MSG_SIGNATURES = (
    "timed out",
    "timeout expired",
    "exceeded its time budget",
)


def _is_timeout_failure(e) -> bool:
    """True iff the generation ran out of *time* rather than failing (#1368).

    A bare ``TimeoutError`` used to fall through to the unrecognized-error
    catch-all, so the user was told to "retry once and report it with the full
    trace" for the one failure mode whose cause is fully known and whose
    message is usually EMPTY — ``TimeoutError:`` with nothing after the colon
    tells them nothing at all.

    Deliberately checked before the OOM branch: a job killed at its deadline is
    not an allocation failure, and Flush is the wrong remedy for it.
    """
    for exc in _exception_chain(e):
        if isinstance(exc, TimeoutError) or type(exc).__name__ in _TIMEOUT_EXC_NAMES:
            return True
        low = str(exc).lower()
        if any(sig in low for sig in _TIMEOUT_MSG_SIGNATURES):
            # "read timed out" is a download dying, which _is_network_failure
            # owns and explains better; don't steal it.
            if "read timed out" in low:
                continue
            return True
    return False


def _oom_friendly_reraise(e):
    """Best-effort cache flush + the user-facing OOM hint shared by both
    inference paths."""
    import gc
    import torch
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()
    # #278: don't mislabel a torch.compile/Triton/Inductor crash as an
    # out-of-memory condition. (model_manager's generate wrapper already
    # retries these eagerly; this only triggers if that retry also died.)
    from services.model_manager import _is_compile_runtime_failure
    if _is_compile_runtime_failure(e):
        raise RuntimeError(
            f"TTS engine hit a torch.compile/Triton error (not out of memory). "
            f"Disable torch.compile in Settings → Performance, use the Flush "
            f"button to reload the model, then regenerate. Underlying error: {e}"
        ) from e
    # #437: a Permission-denied / exec failure (e.g. a bundled engine binary
    # that lost its +x bit) is NOT an OOM — don't send the user to the Flush
    # button; tell them what's actually wrong.
    es = str(e)
    if isinstance(e, PermissionError) or "Permission denied" in es or "Errno 13" in es:
        raise RuntimeError(
            f"A required engine binary couldn't be executed (permission denied). "
            f"This usually means a bundled binary lost its execute bit — reinstall, "
            f"or run `chmod +x` on the engine binary named in the error. "
            f"Underlying error: {e}"
        ) from e
    # #629: a decode/ffmpeg failure on the rendered audio is NOT out of memory —
    # it's unreadable audio (usually a transient numerical glitch). Say so rather
    # than sending the user down the OOM path.
    if "ffmpeg returned error" in es or "Decoding failed" in es or "Invalid data found" in es:
        raise RuntimeError(
            f"The engine produced unreadable audio (a decode step failed) — this is "
            f"usually a transient glitch. Use the Flush button to reload the model, "
            f"then regenerate. Underlying error: {e}"
        ) from e
    # #664: a bad voice-design instruct (free-form prose, mixed EN/ZH, or
    # conflicting tags) raises "Unsupported instruct items …" / "Cannot mix …
    # in a single instruct" / "Conflicting instruct items …" from omnivoice's
    # _resolve_instruct. That's a USER-INPUT validation error, not an OOM. Match
    # on the message signature (NOT the type — a lower layer can wrap the original
    # ValueError, which is why the route's `except ValueError` guard misses it)
    # and re-raise as a clean ValueError so the route returns a 400 with the
    # instruct guidance, instead of a 500 telling the user to Flush for memory
    # they never ran out of. (Complements the client-side guard in #658/#612.)
    _low = es.lower()
    if ("unsupported instruct items" in _low
            or "conflicting instruct items" in _low
            or "in a single instruct" in _low):
        raise ValueError(es) from e
    # #705: a corrupt or wrong-architecture native component (a .dll / .pyd / .exe
    # — torch, ffmpeg, or a bundled engine binary) fails to load/spawn on Windows
    # with "[WinError 193] %1 is not a valid Win32 application". That is NOT OOM,
    # and Flush won't help — reinstalling/repairing the component is the real fix.
    if "[winerror 193]" in _low or "is not a valid win32 application" in _low:
        raise RuntimeError(
            f"A native component (a DLL / .pyd / .exe — e.g. torch, ffmpeg, or an "
            f"engine binary) is corrupt or built for the wrong architecture "
            f"([WinError 193]). Reinstall or repair that component — the Flush "
            f"button won't help here. Underlying error: {e}"
        ) from e
    # #1227: Windows Smart App Control / an App Control (WDAC) policy blocked
    # a file the engine needs — "[WinError 4551] An Application Control policy
    # has blocked this file". WinError 1260 is the same class from the older
    # Software Restriction / AppLocker policies. Not OOM, and Flush can't help:
    # the OS is refusing to load the binary at all.
    if ("[winerror 4551]" in _low or "[winerror 1260]" in _low
            or "application control policy" in _low):
        raise RuntimeError(
            f"Windows blocked a file VoiceStudio needs from running — an "
            f"Application Control policy (Smart App Control, WDAC, or "
            f"AppLocker) refused to load it. On a personal PC: Windows "
            f"Security → App & browser control → Smart App Control → Off "
            f"(note Windows only lets you turn it off once — re-enabling "
            f"needs a Windows reset), then restart VoiceStudio. On a managed/"
            f"work PC ask IT to allow the VoiceStudio install folder. The Flush "
            f"button won't help. Underlying error: {e}"
        ) from e
    # #1221: libsndfile/soundfile could not read or write an audio file. Its
    # errors are bare ("LibsndfileError: System error.") so they used to fall
    # through to the unrecognized catch-all. audio_io._describe_write_failure
    # already names the target for the WRITE path; this covers every other
    # libsndfile surface (reading a reference clip, a decode) with the causes
    # that actually produce an OS-level audio I/O failure.
    if "libsndfile" in _low or "writing the audio file failed" in _low:
        raise RuntimeError(
            f"An audio file couldn't be read or written (libsndfile failed at "
            f"the OS level). This is a file/disk problem, not a memory one: "
            f"check the drive isn't full, the output and temp folders exist "
            f"and are writable, and that antivirus or OneDrive isn't locking "
            f"them (add a VoiceStudio exclusion if you use one). If it happens "
            f"only with one reference clip, re-import that clip. Underlying "
            f"error: {e}"
        ) from e
    # #715: a "[Errno 32] Broken pipe" (BrokenPipeError) surfacing from
    # generation is NOT out of memory — it means the backend's stdout/stderr
    # pipe to the desktop shell that launched it closed mid-render (an orphaned
    # backend whose parent shell exited or relaunched). main.py wraps
    # sys.stdout/stderr to swallow EPIPE, but a C-level write inside the native
    # engine/torch can still raise one past that guard. Flush won't help —
    # relaunching the app re-parents the backend to a live shell.
    # #756: the GPU's compute capability isn't in this PyTorch build's arch list,
    # so CUDA can't launch kernels ("no kernel image is available for execution").
    # NOT OOM. get_best_device() now falls back to CPU up front, but classify the
    # raw error too in case CUDA was forced (OMNIVOICE_FORCE_CUDA) or a sub-path
    # still ran on the GPU — point at the real fix, not the Flush button.
    if "no kernel image is available" in _low:
        raise RuntimeError(
            f"Your GPU isn't supported by the installed PyTorch build (CUDA can't "
            f"launch kernels for its compute capability). Switch the compute device "
            f"to CPU in Settings, or install a matching PyTorch (e.g. a cu128 build "
            f"for newer GPUs). The Flush button won't help. Underlying error: {e}"
        ) from e
    if isinstance(e, BrokenPipeError) or "broken pipe" in _low or "errno 32" in _low:
        raise RuntimeError(
            f"The backend lost its output pipe mid-generation — the desktop app "
            f"that launched it closed or relaunched ([Errno 32] Broken pipe). "
            f"Restart the app and try again; the Flush button won't help here. "
            f"Underlying error: {e}"
        ) from e
    # #880: an httpx/requests transport failure surfacing from generation —
    # most commonly a first-use model download from the HF Hub dying with
    # httpx's "Cannot send a request, as the client has been closed" (the
    # shared client got closed mid-lifecycle), a connect/read timeout, or a
    # dropped connection — is NOT out of memory. The model never finished
    # loading, so Flush is the wrong remedy; retrying is. Matched over the
    # whole exception chain (type names + stringified signatures) because
    # engines wrap the original transport error.
    if _is_network_failure(e):
        raise RuntimeError(
            f"A model download or network call failed mid-generation (usually "
            f"the engine fetching its model files on first use). This is a "
            f"network problem, not a memory problem — flushing VRAM won't "
            f"help. Retry the generation; if it keeps failing, check your "
            f"internet connection and any HF_ENDPOINT/mirror setting. "
            f"Underlying error: {e}"
        ) from e
    # #919: a required engine model path / env var that isn't set is a pure
    # CONFIGURATION problem, not a runtime one. sherpa-onnx's
    # "OMNIVOICE_SHERPA_MODEL not set. Point it to …" used to fall through to
    # the OOM catch-all, telling a user with 63 GB of RAM to press Flush. Point
    # at the real fix — set the variable — and never mention memory or Flush.
    # The underlying error already names the exact variable + what to point it
    # at (and Model Catalogue → Engines shows a copy-paste setup line), so keep it
    # front-and-center. Checked before the OOM branch so a config error can
    # never be mislabeled as memory.
    if _is_config_failure(e):
        raise RuntimeError(
            f"This TTS engine isn't set up yet — it needs a model path or "
            f"environment variable that isn't configured, so nothing was "
            f"generated. Set it as the underlying error describes (it names the "
            f"exact variable and what to point it at), then restart VoiceStudio — "
            f"or pick a ready engine in Model Catalogue → Engines. This is a setup "
            f"problem, not a memory one. Underlying error: {e}"
        ) from e
    # #880 (the class bug): the OOM hint used to be the catch-all fallback,
    # so ANY unrecognized error told the user to press Flush for memory they
    # never ran out of. Only claim OOM when something in the chain actually
    # looks like one; everything else surfaces as what it is — unrecognized —
    # with the real error front and center.
    # #1368: a generate killed at its deadline is not an unrecognized fault.
    # It arrived as a bare `TimeoutError:` with an EMPTY message, so the
    # catch-all below asked the user to report a trace that says nothing.
    # Checked before the OOM branch — a job that ran out of time did not run
    # out of memory, and Flush is the wrong remedy.
    if _is_timeout_failure(e):
        # `TimeoutError` is routinely raised with no message, so the usual
        # "Underlying error: …" tail rendered as a bare `TimeoutError:` —
        # a sentence stopping mid-thought. Append it only when it says
        # something (#1368).
        # Test the MESSAGE, not _safe_exc_text() — that always prefixes the
        # type name, so it is never empty and the check would never fire.
        _tail = f" Underlying error: {_safe_exc_text(e)}" if str(e).strip() else ""
        raise RuntimeError(
            "The engine hit its time limit before finishing, so generation was "
            "stopped. Nothing is broken and flushing memory won't help. The "
            "usual causes are a first-use model download still in progress "
            "(retry once it finishes — it resumes), a very long input, or an "
            "engine running on CPU. Shorter text, or raising "
            "OMNIVOICE_GENERATE_TIMEOUT_S, will get it through."
            + _tail
        ) from e
    # #1334: Windows refusing to back a large model mapping (WinError 1455) is
    # a paging-file limit, not a working-set shortage. It was matching the OOM
    # branch below and telling the user to press Flush — advice that cannot
    # work, as the shared hint for this class says outright ("closing other
    # apps usually won't fix it"). Checked first so the specific case wins.
    _low_1455 = str(e).lower()
    if "paging file is too small" in _low_1455 or (
        "1455" in _low_1455 and ("winerror" in _low_1455 or "os error" in _low_1455)
    ):
        from core.failure import _HINTS
        raise RuntimeError(
            "Windows ran out of virtual memory while mapping the model — its "
            "paging file is smaller than the model needs. This is not your RAM "
            "being full, it is not a network problem, and Flush cannot help. "
            + _HINTS["WINDOWS_PAGING_FILE_TOO_SMALL"]
            + f" Underlying error: {e}"
        ) from e
    if _is_oom_failure(e):
        raise RuntimeError(
            f"TTS engine stopped mid-generation. This usually means it ran out of memory. "
            f"Try the Flush button to reload the model, then regenerate. Underlying error: {e}"
        ) from e
    raise RuntimeError(
        f"TTS engine stopped mid-generation with an error VoiceStudio doesn't "
        f"recognize. Retry once; if it keeps failing, please report it with "
        f"the full trace. Underlying error: {_safe_exc_text(e)}"
    ) from e


def _generate_timeout_s(text: str, *, execution_device=None) -> float:
    """Wall-clock budget for one generate, scaled to the request.

    Thin alias for the canonical helper, which moved to
    ``services.model_manager.generate_timeout_s`` (#1190) so /v1/audio/speech,
    batch, dub and archetype previews share it instead of each re-deriving (or,
    as they did, silently keeping the flat 300s).
    """
    from services.model_manager import generate_timeout_s
    return generate_timeout_s(text, execution_device=execution_device)


def _run_inference(
    model, text, language, ref_audio_path, ref_text, instruct, duration,
    num_step, guidance_scale, speed, t_shift, denoise,
    postprocess_output, layer_penalty_factor, position_temperature,
    class_temperature, used_seed, effect_preset="broadcast",
    max_chunk_chars=None, crossfade_ms=None, *, dropped_sink=None,
):
    import torch
    try:
        if used_seed is not None:
            torch.manual_seed(used_seed)

        kwargs = {}
        if t_shift is not None: kwargs["t_shift"] = t_shift
        if layer_penalty_factor is not None: kwargs["layer_penalty_factor"] = layer_penalty_factor
        if position_temperature is not None: kwargs["position_temperature"] = position_temperature
        if class_temperature is not None: kwargs["class_temperature"] = class_temperature

        sr = model.sampling_rate if hasattr(model, 'sampling_rate') else 24000

        from services.tts_backend import generate_with_cached_ref

        def _gen(gen_text, gen_duration):
            """One generate call for this request's voice, reference encoded once."""
            return generate_with_cached_ref(
                model, ref_audio=ref_audio_path, ref_text=ref_text,
                text=gen_text, language=language, instruct=instruct,
                duration=gen_duration, num_step=num_step,
                guidance_scale=guidance_scale, speed=speed, denoise=denoise,
                postprocess_output=postprocess_output, **kwargs
            )

        # Inline [pause Nms] markers (issue #276): split the text and stitch
        # silence between independently-synthesized spans. Fully opt-in — text
        # without a marker takes the unchanged single-shot path below.
        from omnivoice.utils.text import parse_pause_markers
        segments = parse_pause_markers(text)
        has_pause = len(segments) > 1 or (segments and segments[0][1] > 0)

        if has_pause:
            def _gen_span(span_text):
                # Per-span duration is left to the model; an explicit overall
                # `duration` can't be meaningfully split across spans.
                return _gen(span_text, None)[0]
            audio_out = _render_with_pauses(_gen_span, segments, sr)
        else:
            # Wave 1.2: long text is split at sentence boundaries and the
            # per-chunk audio crossfaded — removes the length ceiling. Short
            # text takes the single-shot path below unchanged. [pause] inputs
            # keep the dedicated stitcher above (spans are already short).
            from services.chunked_tts import (
                DEFAULT_CROSSFADE_MS, DEFAULT_MAX_CHUNK_CHARS,
                concatenate_audio_chunks, split_text_into_chunks,
            )
            _max_chars = DEFAULT_MAX_CHUNK_CHARS if max_chunk_chars is None else max_chunk_chars
            _xfade_ms = DEFAULT_CROSSFADE_MS if crossfade_ms is None else crossfade_ms
            text_chunks = split_text_into_chunks(text, _max_chars)
            if len(text_chunks) > 1:
                parts = []
                for i, chunk_text in enumerate(text_chunks):
                    # Vary the seed per chunk (deterministically) to avoid
                    # correlated RNG artifacts across chunk boundaries.
                    if used_seed is not None:
                        torch.manual_seed(used_seed + i)
                    parts.append(_gen(chunk_text, None)[0])
                    _note_generate_progress()
                audio_out = concatenate_audio_chunks(parts, sr, _xfade_ms,
                                                     texts=text_chunks,
                                                     sink=dropped_sink)
            else:
                audio_out = _gen(text, duration)[0]

        # Apply DSP effect preset. The VoiceStudio model never masters its own
        # output, so mastering always runs here (unchanged behavior).
        return _apply_effect_chain(audio_out, sr, effect_preset)

    except ValueError as e:
        # Don't wrap validation errors in OOM message
        raise e
    except Exception as e:
        _oom_friendly_reraise(e)


def _run_backend_inference(
    backend, text, language, ref_audio_path, ref_text, instruct, duration,
    num_step, guidance_scale, speed, denoise, postprocess_output,
    used_seed, effect_preset="broadcast",
    max_chunk_chars=None, crossfade_ms=None, *, dropped_sink=None,
):
    """Engine-aware twin of :func:`_run_inference` (issue #312).

    Runs the request through a pluggable ``TTSBackend`` adapter instead of the
    VoiceStudio model directly. The adapter protocol is narrower than the
    VoiceStudio-native surface — engine-specific extras (``t_shift``,
    ``layer_penalty_factor``, …) only exist on the native path, which is why
    VoiceStudio itself still goes through ``_run_inference``.
    """
    import torch
    try:
        if used_seed is not None:
            torch.manual_seed(used_seed)

        if language and language.lower() == "auto":
            language = None

        gen_kwargs = dict(
            language=language, ref_audio=ref_audio_path, ref_text=ref_text,
            instruct=instruct, num_step=num_step, guidance_scale=guidance_scale,
            speed=speed, denoise=denoise, postprocess_output=postprocess_output,
        )
        sr = backend.sample_rate

        # Inline [pause Nms] markers (issue #276) work for every engine — the
        # silence stitching is model-free.
        from omnivoice.utils.text import parse_pause_markers
        segments = parse_pause_markers(text)
        has_pause = len(segments) > 1 or (segments and segments[0][1] > 0)

        if has_pause:
            def _gen_span(span_text):
                # Per-span duration is left to the engine; an explicit overall
                # `duration` can't be meaningfully split across spans.
                return backend.generate(span_text, duration=None, **gen_kwargs)
            audio_out = _render_with_pauses(_gen_span, segments, sr)
        else:
            # Wave 1.2: sentence-boundary chunking for long text (see
            # _run_inference for the rationale; behavior is identical here).
            from services.chunked_tts import (
                DEFAULT_CROSSFADE_MS, DEFAULT_MAX_CHUNK_CHARS,
                concatenate_audio_chunks, split_text_into_chunks,
            )
            _max_chars = DEFAULT_MAX_CHUNK_CHARS if max_chunk_chars is None else max_chunk_chars
            _xfade_ms = DEFAULT_CROSSFADE_MS if crossfade_ms is None else crossfade_ms
            text_chunks = split_text_into_chunks(text, _max_chars)
            if len(text_chunks) > 1:
                parts = []
                for i, chunk_text in enumerate(text_chunks):
                    if used_seed is not None:
                        torch.manual_seed(used_seed + i)
                    parts.append(backend.generate(chunk_text, duration=None, **gen_kwargs))
                    _note_generate_progress()
                audio_out = concatenate_audio_chunks(parts, sr, _xfade_ms,
                                                     texts=text_chunks,
                                                     sink=dropped_sink)
            else:
                audio_out = backend.generate(text, duration=duration, **gen_kwargs)

        return _apply_effect_chain(
            audio_out, sr, effect_preset,
            skip_mastering=getattr(backend, "applies_own_mastering", False),
        )

    except ValueError as e:
        # Don't wrap validation errors in OOM message
        raise _language_rejection_or(e, backend, language)
    except Exception as e:
        rewritten = _language_rejection_or(e, backend, language)
        if rewritten is not e:
            raise rewritten from e
        _oom_friendly_reraise(e)


# #1257: the language picker offers all 646 languages regardless of engine,
# because MLXAudioBackend.supported_languages() returns ["multi"] on the stated
# assumption that "each engine silently ignores languages it doesn't know".
# That assumption is false — the underlying library raises, and the reporter got
# a bare 400 that recited 23 language codes without saying which engine was
# refusing, or that switching engines was the fix.
# Each signature must be about the LANGUAGE itself. "Unsupported language" as a
# bare prefix also matches "Unsupported language model configuration" — a model
# problem handed engine-switch advice it has no use for (#1257 review) — so the
# looser wordings require the rejected thing to end there or be a code/name.
_LANGUAGE_REJECTION_SIGNATURES = (
    "invalid language code",
    "language not supported",
    "language is not supported",
    "unsupported language code",
)

#: `unsupported language: xx` / `unsupported language 'xx'` — but not
#: `unsupported language model ...`.
_LANGUAGE_REJECTION_RE = re.compile(
    r"unsupported language\s*[:=]|unsupported language\s*['\"]|"
    r"unsupported language\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _language_rejection_or(e: BaseException, backend, language):
    """``e`` rewritten with engine context when it's a language rejection.

    Returns ``e`` unchanged otherwise, so this is safe to wrap any failure in.
    Matched on the message, not the type: the engines multiplex third-party
    libraries that each raise their own class.
    """
    text = str(e)
    low = text.lower()
    if not any(sig in low for sig in _LANGUAGE_REJECTION_SIGNATURES) and not (
        _LANGUAGE_REJECTION_RE.search(text)
    ):
        return e
    engine = getattr(backend, "display_name", None) or getattr(
        type(backend), "id", type(backend).__name__
    )
    requested = f" '{language}'" if language else ""
    return ValueError(
        f"The {engine} engine can't speak{requested}. VoiceStudio offers every "
        f"language its default engine supports, but each engine covers a "
        f"different set — pick one this engine supports, or switch engine in "
        f"Model Catalogue → Engines (the VoiceStudio engine has the widest coverage) "
        f"and generate again. Engine's own message: {e}"
    )


def _persist_profile_ref_text(profile_id: str, ref_text: str) -> None:
    """Cache an auto-transcribed reference transcript onto its profile row.

    #1032 perf regression: profiles saved without a transcript re-ran a FULL
    ASR model load + transcribe on every /generate (the #308 auto-transcribe
    path). Persisting the first transcript makes subsequent generates read it
    from the row like a user-entered one. The guarded UPDATE only ever fills
    an empty column — it can never overwrite a transcript the user typed or a
    lock wrote — and a failure is logged, never raised (best-effort, same
    contract as the transcribe itself)."""
    try:
        with db_conn() as conn:
            updated = conn.execute(
                "UPDATE voice_profiles SET ref_text=? "
                "WHERE id=? AND (ref_text IS NULL OR ref_text='')",
                (ref_text, profile_id),
            ).rowcount
        if updated:
            event_bus.emit("profiles", {"action": "updated", "id": profile_id})
    except Exception as e:  # noqa: BLE001 — cache write must not break generate
        logger.warning(
            "could not persist auto-transcribed ref_text onto profile %s: %s",
            log_safe(profile_id), log_safe(e),
        )


async def _finalize_generation(
    audio_tensor, sample_rate, *, text, history_mode, ref_audio_path,
    language, instruct, resolved_profile_id, used_seed, start_time,
    already_marked=False,
):
    """Shared tail of a successful generation: watermark → save WAV →
    history row (self-healing) → retention prune → event emit.

    Used verbatim by both the classic whole-file response path and the
    streaming-preview path (``stream=true``), so the on-disk artifact —
    watermark, filename, history row, retention behavior — is identical
    regardless of how the audio was delivered to the client.

    ``already_marked`` is for audio that arrives provenance-marked: a remote
    worker marks at the tensor stage before it encodes (with ``force=True``,
    so the *requesting* user's preference governs, not the GPU owner's), and
    embedding a second AudioSeal payload over the first degrades detection of
    both. The take users keep carries exactly one whole-take mark either way.

    Returns ``(watermarked_tensor, meta)`` where ``meta`` carries
    ``id`` / ``filename`` / ``duration`` / ``gen_time``.
    """
    # Invisible AudioSeal provenance watermark on the final audio. Embedding
    # was previously only wired into the dub pipeline (dub_generate.py), so
    # plain TTS came out unmarked despite the setting being on — and the same
    # class of gap later bit /v1/audio/speech (#1169), which is why ALL
    # producers now share the mark_synthetic chokepoint. It self-gates on the
    # user's watermark setting + AudioSeal availability and passes the audio
    # through unchanged on any failure, so it never breaks generation.
    # Dispatched to the dedicated watermark pool, not the GPU pool (#1190):
    # AudioSeal embedding is CPU work that holds no VRAM, so occupying a GPU
    # worker with it only delays the next generate on 1-worker hosts.
    if not already_marked:
        from services.watermark import mark_synthetic_async
        audio_tensor = await mark_synthetic_async(
            audio_tensor, sample_rate, context="generate.finalize",
        )
    gen_time = round(time.time() - start_time, 2)

    audio_id = str(uuid.uuid4())[:8]
    audio_filename = f"{audio_id}.wav"
    audio_path = os.path.join(OUTPUTS_DIR, audio_filename)
    _safe_torchaudio_save(audio_path, audio_tensor, sample_rate)

    audio_dur = round(audio_tensor.shape[-1] / sample_rate, 2)

    # #710: the clip is already generated and saved above. A history-write
    # failure — e.g. "no such table: generation_history" on a DB that missed
    # schema init — must NOT 500 the user's generation. Self-heal the schema
    # once and retry; if it still fails, log and return the audio anyway.
    def _write_history():
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO generation_history (id, text, mode, language, instruct, profile_id, audio_path, duration_seconds, generation_time, seed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (audio_id, text[:200], history_mode or ("clone" if ref_audio_path else "design"),
                 language or "Auto", instruct or "", resolved_profile_id,
                 audio_filename, audio_dur, gen_time, used_seed, time.time())
            )
    try:
        _write_history()
    except sqlite3.OperationalError as e:
        logger.warning("generation history write failed (%s); healing schema + retrying", e)
        try:
            ensure_schema()
            _write_history()
        except Exception as e2:
            logger.warning("history write still failed after schema heal; returning audio anyway: %s", e2)
    except Exception as e:
        logger.warning("generation history write failed; returning audio anyway: %s", e)
    # Retention cap: without it, takes (rows + WAVs in OUTPUTS_DIR) grow
    # unbounded forever. Best-effort — a prune failure must never affect
    # the generation that just succeeded.
    try:
        _prune_history_over_cap()
    except Exception as e:  # noqa: BLE001
        logger.warning("history retention prune failed (non-fatal): %s", e)
    event_bus.emit("generation_history", {"action": "created", "id": audio_id})

    # Opt-in analytics (core/analytics.py): no-op unless the user turned it on.
    # Metadata only — text_length is the LENGTH of the text, never the text; the
    # allowlist in analytics.sanitize_properties() enforces that regardless.
    try:
        from core.analytics import capture as _ph

        _ph("speech_generated", {
            "mode": history_mode,
            "language": language or "auto",
            "duration_seconds": audio_dur,
            "gen_time_seconds": gen_time,
            "text_length": len(text or ""),          # the LENGTH. never the text.
            "has_profile": bool(resolved_profile_id),
        })
    except Exception:  # noqa: BLE001 — analytics may never break a generation…
        # …but it must not fail SILENTLY either: a typo'd variable here would
        # otherwise mean the event simply never fires and nobody ever knows.
        logger.warning("analytics: speech_generated capture failed", exc_info=True)

    return audio_tensor, {
        "id": audio_id,
        "filename": audio_filename,
        "duration": audio_dur,
        "gen_time": gen_time,
    }


def _pcm16_b64(wav_tensor) -> str:
    """Mono 16-bit little-endian PCM, base64-encoded — the streaming-preview
    wire format (same conversion as /ws/tts). N-D tensors take channel 0."""
    import base64

    import torch

    pcm = (wav_tensor * 32767).clamp(-32768, 32767).to(torch.int16)
    while pcm.ndim > 1:
        pcm = pcm[0]
    return base64.b64encode(pcm.cpu().numpy().tobytes()).decode("ascii")


# ── Remote GPU: this route is the producer the scheduler never had ─────────
#
# Picking a remote worker used to change a badge and nothing else — every
# render still ran on this machine, which is the whole reported bug. The
# decision is taken ONCE per request, through `services/gpu_gateway.py`, and
# BEFORE anything local is loaded — for two reasons that are not
# interchangeable: a render bound for the user's 4090 must not first pull a
# multi-GB model into this machine's RAM, and it must not be refused by a gate
# that asked whether THIS host has the accelerator the engine needs (a
# CUDA-only engine on a Mac control plane is exactly the case remote workers
# exist for).

_REMOTE_OP = "tts"

# The gateway's coarse phase → the sentence a user reads while someone else's
# GPU works. A five-minute remote render otherwise shows the same bare spinner
# as a local one, with no way to tell "queued behind another task" from
# "downloading 5 GB of weights" from "actually generating".
_REMOTE_PHASE_LABELS = {
    "queued": "queued on {target}",
    "loading": "loading model on {target}",
    "running": "generating on {target}",
    "uploading": "receiving audio from {target}",
}


class _LocalDecision:
    """Stand-in for ``worker.routing.Decision`` meaning "run here".

    Used only when the gateway cannot be imported at all, so a build without
    it still renders instead of 500-ing.
    """

    remote = False
    worker_id = None
    label = "Local"
    reason = ""


_LOCAL_DECISION = _LocalDecision()


def _routing_decision():
    """Local or remote for this request — resolved once, never re-asked.

    Asked once because the target is user-settable at any moment: a decision
    that flipped between prewarm and dispatch would either warm an engine
    nothing will use or dispatch remotely after paying a local cold load.
    """
    try:
        from services import gpu_gateway

        return gpu_gateway.decide(_REMOTE_OP)
    except Exception:  # noqa: BLE001 — routing is advisory; local always works
        logger.debug("remote routing unavailable; running locally", exc_info=True)
        return _LOCAL_DECISION


def _remote_only_local_call(target_label, reason=""):
    """The local branch of a render whose local half was deliberately skipped.

    ``gpu_gateway.run`` always takes a local callable — it is where rule 1
    (pre-dispatch unavailability) lands. But this route skips every local
    preparation step once the decision is remote, precisely so a job bound for
    the 4090 does not first load gigabytes here, so there is no local render
    left to fall back to.

    The causes rule 1 actually covers — worker offline, disabled, not
    approved, breaker open, remote workers switched off — are already answered
    by ``decide()`` BEFORE that skip, and come back as a local decision with a
    named reason. What is left is the narrow window where dispatch itself is
    refused (a full queue, a task dropped between submit and wait). Saying so
    and offering the local re-run is honest; silently returning nothing is not.
    """
    from services.gpu_gateway import RemoteJobFailed

    def _refuse():
        raise RemoteJobFailed(
            reason or f"{target_label} could not take this render",
            worker_label=target_label,
            code="REMOTE_NOT_DISPATCHED",
            hint="Run it on this machine instead, or pick another GPU.",
        )

    return _refuse


def _remote_progress_frame(state, target):
    """One gateway ``on_state`` payload → the NDJSON event the UI renders."""
    phase = str((state or {}).get("phase") or "running")
    try:
        pct = max(0, min(100, round(float((state or {}).get("progress") or 0.0) * 100)))
    except (TypeError, ValueError):
        pct = 0
    detail = _REMOTE_PHASE_LABELS.get(phase, _REMOTE_PHASE_LABELS["running"])
    detail = detail.format(target=target)
    if phase == "running" and pct:
        detail = f"{detail} ({pct}%)"
    return {
        "type": "progress", "stage": phase, "percent": pct,
        "target": target, "detail": detail,
    }


def _apply_routing_headers(headers, engine_notice, decision):
    """Say where this render ran, on the notice channel that already exists.

    ``X-OmniVoice-Routing`` / ``-Routing-Reason`` are already set for the #21
    engine routing gate and already consumed as a de-duped one-time toast, so
    "this ran on gpu2" and "your 4090 was asleep, this ran here" travel the
    same wire rather than inventing a second one.

    The engine notice wins on a local render: "the engine fell back to CPU"
    explains the slowness the user is looking at, while the worker notice for
    a local render is the quieter of the two. A remote render has no engine
    notice at all — that gate answers for THIS host, and this host did nothing.
    """
    from services.engine_routing import header_safe_reason

    notice = engine_notice
    if decision is not None:
        try:
            from services.gpu_gateway import notice_for

            worker_notice = notice_for(decision)
        except Exception:  # noqa: BLE001 — a notice must never fail a render
            worker_notice = None
        if worker_notice and (getattr(decision, "remote", False) or not notice):
            notice = worker_notice
    if not notice:
        return headers
    headers["X-OmniVoice-Routing"] = notice[0]
    safe = header_safe_reason(notice[1]) if notice[1] else ""
    if safe:
        headers["X-OmniVoice-Routing-Reason"] = safe
    return headers


@router.post("/generate")
async def generate_speech(
    text: str = Form(...),
    language: Optional[str] = Form(None),
    ref_audio: Optional[UploadFile] = File(None),
    ref_text: Optional[str] = Form(None),
    instruct: Optional[str] = Form(None),
    duration: Optional[float] = Form(None),
    num_step: int = Form(16),
    guidance_scale: float = Form(2.0),
    speed: float = Form(1.0),
    t_shift: Optional[float] = Form(None),
    denoise: bool = Form(True),
    postprocess_output: bool = Form(True),
    layer_penalty_factor: Optional[float] = Form(None),
    position_temperature: Optional[float] = Form(None),
    class_temperature: Optional[float] = Form(None),
    profile_id: Optional[str] = Form(None),
    seed: Optional[int] = Form(None),
    effect_preset: str = Form("broadcast"),
    engine: Optional[str] = Form(None),
    # Wave 1.2 — unlimited-length generation: long text is split at sentence
    # boundaries and crossfaded. 0 disables chunking (whole text to engine).
    max_chunk_chars: int = Form(800, ge=0),
    crossfade_ms: int = Form(50, ge=0, le=1000),
    # Expressive-TTS Spec 01: apply the user pronunciation dictionary + inline
    # [[…]] overrides to the text before synthesis. Default ON; the global
    # OMNIVOICE_PRONUNCIATION pref can disable it for power users. Omitting it
    # with an empty dictionary is byte-identical to legacy behavior.
    pronounce: bool = Form(True),
    # Streaming preview: when true, the response is application/x-ndjson —
    # one JSON event per line ("start" → N × "chunk" (base64 PCM16 preview of
    # each text chunk, playable the moment it arrives) → "done" with the saved
    # take's metadata, or "error"). The final WAV on disk (watermark, history
    # row, retention) is produced by the exact same finalize path as the
    # classic flow, so streaming is purely a delivery channel — engine-agnostic
    # (text-level chunking, no per-engine token streaming).
    stream: bool = Form(False),
):
    # #502: NFC-normalize the input text so decomposed (NFD) diacritics — common
    # in pasted Vietnamese and other Latin-with-marks text — are composed to the
    # single codepoints the tokenizer/model expect, instead of base-letter +
    # combining-mark sequences that render as distorted/garbled speech. NFC is a
    # no-op for already-composed text; mirrors the duration estimator
    # (utils/duration.py) so the estimate and the synthesis see the same text.
    import unicodedata
    text = unicodedata.normalize("NFC", text)

    # ── Engine resolution (issue #312) ──────────────────────────────────────
    # The request runs on the engine selected in Settings (POST /engines/select,
    # env var OMNIVOICE_TTS_BACKEND wins), or an explicit per-request `engine`
    # override — same pattern as /ws/tts's `engine` field and /v1/audio/speech's
    # `model`. Omitting both keeps the historical default (VoiceStudio), so
    # existing API consumers see no change.
    from services.tts_backend import (
        OmniVoiceBackend, _mask_hf_tokens, active_backend_id, get_backend_class,
    )

    engine_id = engine or active_backend_id()
    try:
        backend_cls = get_backend_class(engine_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown TTS engine: {engine_id!r}. "
                "See GET /engines/tts for the list of valid engine ids."
            ),
        )

    # Crash forensics (#1164): a generate is exactly the kind of work an OOM
    # kill lands on — record it (engine id only, never the text) so an
    # unclean death is attributable by the next run. Throttled + never raises.
    from core.run_sentinel import touch_activity
    touch_activity("generate", engine_id)

    # ── Where does this render run? Asked once, here, because every line
    # between this point and the dispatch below is preparation of THIS
    # machine's GPU — model eviction, a multi-GB load, a host-capability gate.
    # None of it applies to a render that belongs on the user's other box, and
    # running it anyway is how "I selected gpu2" ended up meaning "the Mac did
    # the work after loading the model twice".
    _decision = _routing_decision()
    _remote = bool(getattr(_decision, "remote", False))
    _target_label = getattr(_decision, "label", "") or "the chosen worker"

    _model = None
    _backend = None
    _engine_min_vram_gb = getattr(backend_cls, "min_vram_gb", 0.0)
    _routing_notice = None
    # Remote renders deliberately skip this host's capability gate. Keep the
    # local fallback call's timeout device-neutral so the closure is valid
    # without pretending the control plane describes the remote worker.
    _routing = {"effective_device": None}

    if not _remote:
        # Single-active-engine memory discipline: hand back any OTHER resident
        # TTS engine's model before loading this one, so switching engines (or
        # a per-request engine= override, which bypasses /engines/select
        # entirely) doesn't stack two multi-GB models in memory — the
        # accumulation behind the 16 GB-Mac OOM deaths. No-op when nothing else
        # is resident, so steady-state single-engine use pays nothing. Opt out:
        # OMNIVOICE_SINGLE_ENGINE_RESIDENT=0.
        from services.engine_memory import evict_other_tts_engines
        await evict_other_tts_engines(engine_id)

        # Non-blocking breadcrumb: if free memory is already low before this
        # load, log it. A later OOM kill (the 16 GB-Mac class) then has a trail
        # pointing at the load that tipped it, instead of a silent process
        # death. Never blocks — the OS can reclaim cache, and a hard refuse
        # would brick legitimate loads.
        try:
            from services.memory_budget import log_if_low

            log_if_low(f"TTS load ({engine_id})")
        except Exception:
            pass

        # VRAM eviction runs in get_model()'s warm-return path now, so every
        # native TTS generate (this route, WS TTS, dub, batch, audiobook) is
        # covered.
        if backend_cls is OmniVoiceBackend:
            # VoiceStudio keeps its native path: it carries the full advanced
            # parameter surface (t_shift, layer/position/class controls) that
            # the generic adapter protocol doesn't. Byte-identical behavior.
            _model = await get_model()
        else:
            try:
                ok, msg = backend_cls.is_available()
            except Exception as exc:
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            if not ok:
                raise HTTPException(
                    status_code=400,
                    detail=f"TTS engine '{engine_id}' is not available: {_mask_hf_tokens(msg)}",
                )
            # Reuse the per-process instance cache shared with the engine
            # health-check route so weights load once, not per request.
            from api.routers.engines import _get_engine_instance
            _backend = _get_engine_instance(backend_cls)

        # ── Routing gate (#21 — no silent CPU fallback). Computed ONCE per
        # request (host caps are constant; the per-request engine= override
        # bypasses the /engines/select gate, so this is the only place it's
        # enforced for synth). Local only, and deliberately: it asks what THIS
        # host can accelerate, and a remote render is precisely the case where
        # that answer is none of the question — a CUDA-only engine sent to a
        # 4090 from a Mac control plane would be refused by a gate describing
        # a machine that is about to do nothing.
        from core.device_caps import detect_host_caps
        from services.engine_routing import resolve_routing, routing_notice
        _routing = resolve_routing(
            getattr(backend_cls, "gpu_compat", ("cpu",)), detect_host_caps(),
            _engine_min_vram_gb,
        )
        if _routing["routing_status"] == "unavailable":
            # The engine needs an accelerator this host lacks and has no CPU path.
            raise HTTPException(status_code=400, detail=_routing["routing_reason"])
        _routing_notice = routing_notice(_routing)  # (status, reason) or None

    # ── #1033/#1037: warm the engine under the LOAD budget, not the generate
    # budget. A cold adapter lazily loads (and possibly downloads multi-GB
    # weights) inside generate(), so a fresh install's first request burned
    # its whole OMNIVOICE_GENERATE_TIMEOUT_S window on the download and died
    # with a misleading "too heavy for the available compute" 503 (#1014
    # measured it: 0% GPU util for the full 300s). Model loading gets its own,
    # larger budget (OMNIVOICE_MODEL_LOAD_TIMEOUT, default 1200s) — the same
    # split get_model() already has for the native engine. Once warm, this is
    # a no-op per request. A remote render gets the same two-phase split from
    # the worker, under the assignment's own model-load deadline.
    if _backend is not None:
        from services import gpu_gateway
        try:
            await gpu_gateway.prewarm(
                _REMOTE_OP, backend=_backend, engine=engine_id, decision=_decision,
            )
        # Builtin TimeoutError base, not GpuJobTimeoutError — reload-proof
        # class identity (see the twin catch in openai_compat.py).
        except (TimeoutError, gpu_gateway.ModelLoadTimeout) as exc:
            logger.warning("engine load exceeded the model-load budget: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    f"TTS engine '{engine_id}' did not finish loading within its "
                    f"model-load budget — on a first run this usually means the "
                    f"weight download is slow or stalled (check Model Catalogue → Models "
                    f"for progress), not that generation failed. Retry once the "
                    f"model shows as installed."
                ),
            ) from exc

    ref_audio_path = None
    cleanup_ref = False
    used_seed = seed
    resolved_profile_id = None
    history_mode = None  # profile.kind when a profile drives; else inferred at insert
    # #1032: profile id to persist an auto-transcribed reference transcript to.
    # Set only for a plain (unlocked) clone profile whose stored ref_text is
    # empty — the case where every /generate re-ran a full ASR model load +
    # transcribe of the same clip. Locked profiles are excluded (their ref
    # audio is the locked take, and unlocking would leave a mismatched
    # transcript paired with the original reference); design profiles are
    # excluded (a re-render replaces the sample, stranding a stale transcript).
    persist_ref_text_profile_id = None

    if profile_id:
        with db_conn() as conn:
            row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if row:
            resolved_profile_id = profile_id
            # `kind` is authoritative (0005): 'design' profiles condition on
            # their deterministic rendered sample + instruct; 'clone' on the
            # user's reference. Lock always wins (it pins a specific take).
            # Rows from pre-0004 DBs mid-upgrade may lack the column → fall
            # back to the legacy is_locked/instruct inference.
            try:
                profile_kind = row["kind"] or "clone"
            except (KeyError, IndexError):
                profile_kind = "design" if (row["instruct"] and not row["is_locked"] and not row["ref_audio_path"]) else "clone"
            history_mode = profile_kind
            if row["is_locked"] and row["locked_audio_path"]:
                ref_audio_path = os.path.join(VOICES_DIR, row["locked_audio_path"])
                if not ref_text:
                    ref_text = row["ref_text"]
                if not instruct:
                    instruct = _profile_instruct(row)
                if used_seed is None and row["seed"] is not None:
                    used_seed = row["seed"]
            elif profile_kind == "design":
                # Rendered sample (if present) carries the voice identity;
                # instruct alone is the fallback for legacy archetype rows.
                ref_audio_path = os.path.join(VOICES_DIR, row["ref_audio_path"]) if row["ref_audio_path"] else None
                if ref_audio_path and not ref_text and row["ref_text"]:
                    ref_text = row["ref_text"]
                if not instruct:
                    instruct = _profile_instruct(row)
                if used_seed is None and row["seed"] is not None:
                    used_seed = row["seed"]
            elif row["instruct"] and not row["is_locked"] and not row["ref_audio_path"]:
                # Legacy design-shaped row (pre-0004 archetype materialization
                # failure path): instruct-only conditioning.
                if not instruct:
                    instruct = _profile_instruct(row)
                if used_seed is None and row["seed"] is not None:
                    used_seed = row["seed"]
            else:
                ref_audio_path = os.path.join(VOICES_DIR, row["ref_audio_path"]) if row["ref_audio_path"] else None
                if not ref_text and row["ref_text"]:
                    ref_text = row["ref_text"]
                elif ref_audio_path and not ref_text:
                    # Empty stored transcript → the auto-transcribe below will
                    # run; cache its result onto the profile so it runs ONCE,
                    # not on every generate (#1032 perf regression).
                    persist_ref_text_profile_id = profile_id
                if not instruct and row["instruct"]:
                    instruct = row["instruct"]
                if used_seed is None and row["seed"] is not None:
                    used_seed = row["seed"]
            if language == "Auto":
                language = None
            # #533: a profile's stored language must drive generation when the
            # request didn't pin one. Without this the German (etc.) archetype
            # generates with language=None and the model drifts to English —
            # even though the archetype PREVIEW renders correctly (archetypes.py
            # passes the language). An EXPLICIT non-Auto request language still
            # wins; we only fill the gap. `row` is a sqlite3.Row, so guard the
            # column lookup for pre-language DBs mid-upgrade.
            if language is None:
                try:
                    prof_lang = row["language"]
                except (KeyError, IndexError):
                    prof_lang = None
                if prof_lang and prof_lang != "Auto":
                    language = prof_lang
    elif ref_audio is not None:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
                f.write(await ref_audio.read())
                ref_audio_path = f.name
                cleanup_ref = True
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # #308: a transcript-less reference is transcribed with the active ASR
    # backend (whisperx / faster-whisper / mlx-whisper) instead of the model's
    # built-in transformers pipeline, which cannot load whisper-large-v3-turbo
    # on transformers 5.3. On failure ref_text stays None and the model's
    # fallback behaves exactly as before.
    if ref_audio_path and not ref_text:
        from services.asr_backend import transcribe_reference
        # Same #730 hang risk as any whisperx transcribe — bound + reset the pool
        # so a wedged reference transcribe can't brick the backend. This path is
        # best-effort (transcribe_reference returns None on failure → the model's
        # built-in ASR fallback), so a timeout degrades to None rather than
        # failing the whole generate.
        try:
            ref_text = await run_on_gpu_pool_guarded(
                functools.partial(transcribe_reference, ref_audio_path),
                what="Reference transcribe",
                # Floor budget (#1190): a reference clip is seconds of audio,
                # so the length-scaled bonus never applies — but the timeout is
                # explicit here too, so no dispatch relies on a hidden default.
                timeout=_generate_timeout_s("", execution_device=_routing["effective_device"]),
            )
        # TimeoutError covers both the execution bound and pool saturation:
        # this path is best-effort either way.
        except TimeoutError as e:
            logger.warning("reference transcribe hung (%s); using model ASR fallback", e)
            ref_text = None
        # #1032: cache the transcript onto its clone profile so the ASR model
        # load + transcribe above happens once per profile, not per generate.
        # Only fills an empty column — a user-entered transcript always wins.
        if ref_text and persist_ref_text_profile_id:
            _persist_profile_ref_text(persist_ref_text_profile_id, ref_text)

    # #526: materialize a concrete seed when none was supplied (and no profile
    # pinned one) so the take is reproducible and we can hand it back via the
    # X-Seed header for the "keep this seed" control. An explicit request seed
    # or a profile's stored seed still wins — used_seed is only filled when it
    # is still None here, never overwritten.
    if used_seed is None:
        used_seed = random.randint(0, 2**31 - 1)

    # Engine-agnostic text normalization (junk strip, numbers→words,
    # abbreviations) — AFTER `language` is fully resolved, and BEFORE the
    # pronunciation dictionary so user dictionary entries operate on
    # normalized text and respellings are never re-mangled (ordering rationale
    # in services/text_normalization.py). Pref-gated (default ON), idempotent,
    # never raises; applied exactly once per request, at this choke point.
    from services.text_normalization import normalize_for_tts
    text = normalize_for_tts(text, language)

    # Expressive-TTS Spec 01: apply the user pronunciation dictionary + inline
    # [[…]] one-off overrides to the text, here — AFTER `language` is fully
    # resolved (a profile may fill it above) so per-language entries match the
    # real render language, and BEFORE the text reaches either inference path
    # (native VoiceStudio or a pluggable backend) and the chunk splitter. This is
    # the single point user text → normalized text → model, so the transform
    # covers generate for every engine. Pure text substitution → identical on
    # mac/Win/Linux. A disabled pref or empty dictionary is a pass-through, so
    # plain text stays byte-identical (#G5 backward-compat).
    from core import prefs as _prefs
    _pron_env = os.environ.get("OMNIVOICE_PRONUNCIATION")
    if _pron_env is not None:
        # Env wins (power-user override); "0"/"false"/"no"/"off" disable it.
        _pron_enabled = _pron_env.strip().lower() not in ("0", "false", "no", "off", "")
    else:
        _pron_enabled = bool(_prefs.get("pronunciation_enabled", True))
    if pronounce and _pron_enabled:
        from services.pronunciation import apply_pronunciation, load_entries_from_db
        try:
            _pron_rows = load_entries_from_db()
        except Exception:  # noqa: BLE001 — table missing / DB locked → no-op
            _pron_rows = []
        text = apply_pronunciation(text, _pron_rows, language)
    else:
        # Even with the dictionary off, inline [[…]] overrides are an explicit,
        # in-text authoring choice → always honored (and never left as literal
        # double-bracket text the model would mispronounce).
        from services.pronunciation import apply_inline_overrides
        text = apply_inline_overrides(text)

    start_time = time.time()

    # ── The remote assignment ───────────────────────────────────────────────
    # Built even for a local render (it costs a dict) so the gateway owns the
    # branch rather than this route owning two of them.
    #
    # The worker runs the ENTIRE render as one op — sentence split, per-chunk
    # generate at ``seed + i``, crossfaded concat, effect chain, provenance
    # mark — because dispatching a chunk at a time would pay a round trip, a
    # progress lease and a slot per sentence against a worker whose
    # concurrency defaults to 1. So every knob that shapes the local render has
    # to be on the wire: a missing one is not an error, it is remote audio that
    # quietly differs from local audio (no sentence splitting, no per-chunk
    # seed variation, no crossfade).
    from services import gpu_gateway
    from services.watermark import is_enabled as _watermark_enabled

    _remote_params = {
        "text": text,
        "language": None if (language and language.lower() == "auto") else language,
        "ref_audio": ref_audio_path,
        "ref_text": ref_text,
        "instruct": instruct,
        "duration": duration,
        "speed": speed,
        "num_step": num_step,
        "guidance_scale": guidance_scale,
        "denoise": denoise,
        "postprocess_output": postprocess_output,
        "t_shift": t_shift,
        "layer_penalty_factor": layer_penalty_factor,
        "position_temperature": position_temperature,
        "class_temperature": class_temperature,
        "seed": used_seed,
        "max_chunk_chars": max_chunk_chars,
        "crossfade_ms": crossfade_ms,
        "effect_preset": effect_preset,
        # The requesting user's provenance preference, not the GPU owner's.
        "watermark": bool(_watermark_enabled()),
    }
    _remote_call = gpu_gateway.RemoteCall(
        engine=engine_id, operation=_REMOTE_OP, params=_remote_params,
    )

    async def _render_on_worker(on_state=None):
        """One whole render on the chosen worker → ``(tensor, sample_rate)``.

        The audio comes back already effect-chained and provenance-marked: the
        worker mirrors the local order (split → generate → concat → effects →
        mark) so a remote take and a local take of the same request differ
        only in which GPU produced them.
        """
        waveform, sample_rate = await gpu_gateway.run(
            _REMOTE_OP,
            local=gpu_gateway.LocalCall(
                _remote_only_local_call(_target_label),
                what="TTS generate",
                timeout=_generate_timeout_s(text, execution_device=_routing["effective_device"]),
                min_vram_gb=_engine_min_vram_gb,
            ),
            remote=_remote_call,
            decision=_decision,
            on_state=on_state,
        )
        if getattr(waveform, "ndim", 2) == 1:
            # `_safe_torchaudio_save` and the local paths deal in
            # (channels, samples); a mono artifact reads back flat.
            waveform = waveform.unsqueeze(0)
        return waveform, sample_rate

    # ── Streaming preview (feat: streaming-tts-preview) ─────────────────────
    # Long scripts used to mean staring at a spinner until the ENTIRE render
    # finished. With stream=true the existing text chunks (the Wave 1.2
    # sentence-boundary splitter — unchanged) are synthesized sequentially and
    # each chunk's audio is yielded the moment it's rendered, so playback can
    # begin after the first chunk. The final file is then assembled through
    # the SAME concat → effect-chain → watermark → save → history pipeline as
    # the classic path, so the on-disk take is identical to a non-streamed
    # one. [pause]-marker inputs and single-chunk (short) texts keep their
    # unchanged single-shot pipeline and stream as one chunk. All prep above
    # (engine warm under the #1039 model-load budget, routing gate, profile /
    # seed / normalization) already ran, so per-chunk jobs spend the generate
    # budget on generation only — and each chunk gets its own budget, so a
    # long script can't time out merely for being long.
    if stream and _remote:
        # ── Remote: the streaming PREVIEW is off, the render still streams ──
        # Progressive playback needs per-chunk dispatch, and per-chunk dispatch
        # to a worker means a round trip, a progress lease and a slot for every
        # sentence, serialised by a default concurrency of 1. So the render
        # goes as ONE op and there is no first chunk to play early.
        #
        # The NDJSON channel stays open anyway, because the desktop UI asks for
        # it whenever auto-play is on — which is the default. Answering with
        # the classic WAV shape here would make the client fall back to a
        # LOCAL re-render, i.e. exactly the bug this phase exists to fix: the
        # user picks gpu2, clicks Synthesize, and their laptop does the work.
        # What flows down it instead is coarse progress from the worker, then
        # the finished take as a single chunk.
        _remote_headers = _apply_routing_headers(
            {"X-Seed": str(used_seed) if used_seed is not None else "",
             "Cache-Control": "no-cache"},
            None, _decision,
        )

        _progress_q: asyncio.Queue = asyncio.Queue()

        def _push_progress(event):
            # Called from the control plane's own loop; never let a progress
            # frame break a render that is otherwise going fine.
            try:
                _progress_q.put_nowait(dict(event or {}))
            except Exception:  # noqa: BLE001
                logger.debug("dropped a remote progress frame", exc_info=True)

        async def _remote_stream_events():
            import json

            def _line(obj) -> bytes:
                return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")

            render = asyncio.ensure_future(_render_on_worker(_push_progress))
            try:
                # Relay progress until the render settles, then flush whatever
                # arrived in the gap so the last "generating (98%)" is not lost.
                while not render.done():
                    getter = asyncio.ensure_future(_progress_q.get())
                    done, _pending = await asyncio.wait(
                        {render, getter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if getter in done:
                        yield _line(_remote_progress_frame(getter.result(), _target_label))
                        continue
                    getter.cancel()
                while not _progress_q.empty():
                    yield _line(_remote_progress_frame(_progress_q.get_nowait(),
                                                       _target_label))
                audio_tensor, sample_rate = await render

                yield _line({
                    "type": "start", "sample_rate": sample_rate, "channels": 1,
                    "format": "pcm16", "total_chunks": 1, "crossfade_ms": 0,
                    "seed": used_seed,
                })
                # No second provenance mark: the worker marked at the tensor
                # stage before encoding, with the requesting user's preference
                # forced, and stacking a second AudioSeal payload over the
                # first degrades detection of both.
                yield _line({"type": "chunk", "seq": 0, "pcm": _pcm16_b64(audio_tensor)})

                _, meta = await _finalize_generation(
                    audio_tensor, sample_rate, text=text, history_mode=history_mode,
                    ref_audio_path=ref_audio_path, language=language,
                    instruct=instruct, resolved_profile_id=resolved_profile_id,
                    used_seed=used_seed, start_time=start_time, already_marked=True,
                )
                # #1330's dropped-chunk warning has no remote carrier yet: the
                # gateway hands back audio, not the worker's render metadata.
                # Reported as a cross-stream gap rather than faked as zero.
                yield _line({
                    "type": "done", "id": meta["id"], "audio_path": meta["filename"],
                    "duration": meta["duration"], "gen_time": meta["gen_time"],
                    "seed": used_seed, "sample_rate": sample_rate,
                })
            except (asyncio.CancelledError, GeneratorExit):
                # The user hit stop, or the request was abandoned. Cancelling
                # the render is what tells the worker to release its slot —
                # otherwise the 4090 keeps rendering audio nobody will hear,
                # holding what is often its only slot until the lease lapses.
                render.cancel()
                raise
            except ValueError:
                logger.error("Remote generation request rejected")
                from core.public_errors import stream_failure
                yield _line({"type": "error", **stream_failure("invalid_request")})
            except gpu_gateway.ModelNotDownloaded as e:
                logger.warning("Remote model missing on %s", _target_label)
                from core.public_errors import stream_failure
                yield _line({
                    "type": "error",
                    **stream_failure("model_not_downloaded"),
                    "engine": e.engine,
                    "repo_ids": e.repo_ids,
                    "target": e.target,
                    "target_label": e.target_label,
                    "downloadable": e.downloadable,
                })
            except gpu_gateway.RemoteJobFailed as e:
                logger.error("Remote generate failed on %s", _target_label)
                from core.public_errors import stream_failure
                yield _line({
                    "type": "error",
                    **stream_failure("generation_failed"),
                    "retryable": True,
                    "target_label": e.worker_label or _target_label,
                    "hint": e.hint,
                })
            except Exception as exc:
                # Mid-job remote failure is NOT quietly redone here: the client
                # treats a retryable error as "surface it", so the user decides
                # whether to spend the same minutes again on this machine. Like
                # the local streaming path, this in-band frame stands in for the
                # global 500 handler, so it journals the scrubbed failure and
                # names a recognized cause instead of the bare generic string
                # (#1607).
                logger.error("Remote generation failed", exc_info=True)
                from core.public_errors import stream_generation_failure
                from core import error_journal

                error_journal.record(
                    exc, route="/generate", trace=traceback.format_exc()
                )
                yield _line({"type": "error", **stream_generation_failure(exc)})
            finally:
                if not render.done():
                    render.cancel()
                if cleanup_ref and ref_audio_path:
                    with contextlib.suppress(OSError):
                        os.remove(ref_audio_path)

        return StreamingResponse(
            _remote_stream_events(),
            media_type="application/x-ndjson",
            headers=_remote_headers,
        )

    if stream:
        from omnivoice.utils.text import parse_pause_markers
        from services.chunked_tts import split_text_into_chunks

        _segments = parse_pause_markers(text)
        _has_pause = len(_segments) > 1 or (_segments and _segments[0][1] > 0)
        _text_chunks = [] if _has_pause else split_text_into_chunks(text, max_chunk_chars)
        # #1330 — see the non-streaming path: chunks the engine rendered to
        # nothing land here so the stream can say the take is missing text
        # instead of quietly handing back a short one.
        _dropped_sink: list = []

        def _render_stream_chunk(i: int, chunk_text: str):
            """One text chunk → (raw engine tensor, preview-DSP tensor, sr).

            Runs on the GPU pool. Mirrors ONE iteration of the multi-chunk
            loop in _run_inference/_run_backend_inference exactly (per-chunk
            deterministic seed, same generate kwargs), so concatenating the
            raw parts afterwards reproduces the non-streaming output. The
            preview copy gets the same effect chain the final file will get,
            so what the user hears mid-stream matches the saved take.
            """
            import torch
            try:
                if used_seed is not None:
                    torch.manual_seed(used_seed + i)
                if _backend is not None:
                    _lang = None if (language and language.lower() == "auto") else language
                    raw = _backend.generate(
                        chunk_text, duration=None, language=_lang,
                        ref_audio=ref_audio_path, ref_text=ref_text,
                        instruct=instruct, num_step=num_step,
                        guidance_scale=guidance_scale, speed=speed,
                        denoise=denoise, postprocess_output=postprocess_output,
                    )
                    sr = _backend.sample_rate
                    skip = getattr(_backend, "applies_own_mastering", False)
                else:
                    kwargs = {}
                    if t_shift is not None: kwargs["t_shift"] = t_shift
                    if layer_penalty_factor is not None: kwargs["layer_penalty_factor"] = layer_penalty_factor
                    if position_temperature is not None: kwargs["position_temperature"] = position_temperature
                    if class_temperature is not None: kwargs["class_temperature"] = class_temperature
                    # Same cached-reference path as _run_inference: chunk 0 encodes
                    # the reference, chunks 1..N hit the cache instead of re-encoding.
                    from services.tts_backend import generate_with_cached_ref
                    raw = generate_with_cached_ref(
                        _model, ref_audio=ref_audio_path, ref_text=ref_text,
                        text=chunk_text, language=language, instruct=instruct,
                        duration=None, num_step=num_step,
                        guidance_scale=guidance_scale, speed=speed, denoise=denoise,
                        postprocess_output=postprocess_output, **kwargs
                    )[0]
                    sr = _model.sampling_rate if hasattr(_model, "sampling_rate") else 24000
                    skip = False
                preview = _apply_effect_chain(raw, sr, effect_preset, skip_mastering=skip)
                # The STREAMED copy is provenance-marked by the caller (#1169
                # mark, moved off this GPU job in #1190): the preview PCM
                # leaves the app the moment it's yielded, before
                # _finalize_generation marks the assembled take, so it needs
                # its own mark — but AudioSeal embedding is CPU work, and
                # doing it here held the GPU worker for the whole embed on
                # every one of N chunks. `raw` stays unmarked — the saved
                # artifact gets exactly one whole-take mark in the finalize
                # path (no double-embed on the file users keep).
                return raw, preview, sr
            except ValueError:
                raise
            except Exception as e:
                _oom_friendly_reraise(e)

        def _assemble_stream_chunks(parts, sr):
            """Concat + whole-take effect chain — the same tail the
            non-streaming multi-chunk loop runs, as one pool job."""
            from services.chunked_tts import concatenate_audio_chunks
            try:
                audio_out = concatenate_audio_chunks(parts, sr, crossfade_ms,
                                                     texts=_text_chunks,
                                                     sink=_dropped_sink)
                skip = (getattr(_backend, "applies_own_mastering", False)
                        if _backend is not None else False)
                return _apply_effect_chain(audio_out, sr, effect_preset, skip_mastering=skip)
            except ValueError:
                raise
            except Exception as e:
                _oom_friendly_reraise(e)

        async def _stream_events():
            import json

            def _line(obj) -> bytes:
                return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")

            try:
                if _has_pause or len(_text_chunks) <= 1:
                    # Single-shot pipeline, unchanged — streamed as one chunk.
                    if _backend is not None:
                        audio_tensor = await run_on_gpu_pool_guarded(
                            functools.partial(
                                _run_backend_inference,
                                _backend, text, language, ref_audio_path, ref_text,
                                instruct, duration, num_step, guidance_scale, speed,
                                denoise, postprocess_output, used_seed, effect_preset,
                                max_chunk_chars, crossfade_ms, dropped_sink=_dropped_sink,
                            ),
                            what="TTS generate",
                            min_vram_gb=_engine_min_vram_gb,
                            timeout=_generate_timeout_s(text, execution_device=_routing["effective_device"]),
                        )
                        sample_rate = _backend.sample_rate
                    else:
                        audio_tensor = await run_on_gpu_pool_guarded(
                            functools.partial(
                                _run_inference,
                                _model, text, language, ref_audio_path, ref_text,
                                instruct, duration, num_step, guidance_scale, speed,
                                t_shift, denoise, postprocess_output,
                                layer_penalty_factor, position_temperature,
                                class_temperature, used_seed, effect_preset,
                                max_chunk_chars, crossfade_ms, dropped_sink=_dropped_sink,
                            ),
                            what="TTS generate",
                            min_vram_gb=_engine_min_vram_gb,
                            timeout=_generate_timeout_s(text, execution_device=_routing["effective_device"]),
                        )
                        sample_rate = _model.sampling_rate
                    yield _line({
                        "type": "start", "sample_rate": sample_rate, "channels": 1,
                        "format": "pcm16", "total_chunks": 1, "crossfade_ms": 0,
                        "seed": used_seed,
                    })
                    # Provenance-mark the streamed copy (#1169): these PCM
                    # bytes leave the app before _finalize_generation marks
                    # the saved take. Marking a copy keeps the artifact's
                    # single whole-take mark (embed_watermark returns a new
                    # tensor; audio_tensor itself is untouched).
                    # Runs on the dedicated watermark pool, not the GPU pool
                    # (#1190): AudioSeal embedding is CPU work that owns no
                    # VRAM, and on a 1-worker host it used to serialize
                    # directly ahead of the next generate.
                    from services.watermark import mark_synthetic_async
                    _preview = await mark_synthetic_async(
                        audio_tensor, sample_rate,
                        context="generate.stream_preview",
                    )
                    yield _line({"type": "chunk", "seq": 0, "pcm": _pcm16_b64(_preview)})
                else:
                    parts = []
                    sample_rate = None
                    for i, chunk_text in enumerate(_text_chunks):
                        # Bounded per chunk + pool-reset on hang (#730 class);
                        # a timeout surfaces as an "error" event below.
                        raw, preview, sample_rate = await run_on_gpu_pool_guarded(
                            functools.partial(_render_stream_chunk, i, chunk_text),
                            what="TTS generate",
                            min_vram_gb=_engine_min_vram_gb,
                            # Budget scaled to THIS chunk (#1190) — the flat
                            # 300s here is what made long streamed renders fail
                            # even after the v0.3.22 scaled budget shipped.
                            timeout=_generate_timeout_s(chunk_text, execution_device=_routing["effective_device"]),
                        )
                        parts.append(raw)
                        # Provenance-mark the streamed copy off the GPU pool
                        # (#1169 mark, #1190 placement): CPU-only AudioSeal
                        # work must not occupy a GPU worker between chunks.
                        from services.watermark import mark_synthetic_async
                        preview = await mark_synthetic_async(
                            preview, sample_rate,
                            context="generate.stream_preview",
                        )
                        if i == 0:
                            # After the first render so lazy-loading engines
                            # report their REAL sample rate (see /ws/tts).
                            yield _line({
                                "type": "start", "sample_rate": sample_rate,
                                "channels": 1, "format": "pcm16",
                                "total_chunks": len(_text_chunks),
                                "crossfade_ms": crossfade_ms, "seed": used_seed,
                            })
                        yield _line({"type": "chunk", "seq": i, "pcm": _pcm16_b64(preview)})
                    audio_tensor = await run_on_gpu_pool_guarded(
                        functools.partial(_assemble_stream_chunks, parts, sample_rate),
                        what="TTS assemble",
                        timeout=_generate_timeout_s(text, execution_device=_routing["effective_device"]),
                    )

                _, meta = await _finalize_generation(
                    audio_tensor, sample_rate, text=text, history_mode=history_mode,
                    ref_audio_path=ref_audio_path, language=language,
                    instruct=instruct, resolved_profile_id=resolved_profile_id,
                    used_seed=used_seed, start_time=start_time,
                )
                # #1330: before `done`, say what the take is missing. Its own
                # frame rather than a `done` field so a consumer that only
                # handles known types still surfaces it, and so the shape
                # matches `error` (which clients already special-case).
                if _dropped_sink:
                    yield _line({
                        "type": "warning", "code": "dropped_chunks",
                        "count": len(_dropped_sink),
                        "text": [t for t in _dropped_sink if t],
                    })
                yield _line({
                    "type": "done", "id": meta["id"], "audio_path": meta["filename"],
                    "duration": meta["duration"], "gen_time": meta["gen_time"],
                    "seed": used_seed, "sample_rate": sample_rate,
                    "dropped_chunks": len(_dropped_sink),
                })
            except (asyncio.CancelledError, GeneratorExit):
                # Client went away mid-stream — same semantics as aborting a
                # classic /generate mid-render: nothing is saved.
                raise
            except GpuPoolBusyError as e:
                # In-band error frame carries the machine-readable retryable
                # marker (#1190) — an NDJSON consumer can back off instead of
                # guessing from the prose.
                logger.error("Streaming generation capacity unavailable")
                from core.public_errors import stream_failure
                failure = stream_failure("generation_busy")
                failure["retry_after"] = getattr(e, "retry_after", 30)
                yield _line({"type": "error", **failure})
            except GpuJobTimeoutError:
                # The worker started and spent its full execution budget. That
                # is compute time, not queue pressure (#1588).
                logger.error("Streaming generation exceeded its compute budget")
                from core.public_errors import stream_failure
                failure = stream_failure("generation_timeout")
                failure["retry_after"] = 30
                yield _line({"type": "error", **failure})
            except ValueError:
                logger.error("Streaming generation request rejected")
                from core.public_errors import stream_failure
                yield _line({"type": "error", **stream_failure("invalid_request")})
            except Exception as exc:
                # A streaming request answers 200 and carries its failure as an
                # in-band error frame, so it never reaches the global 500
                # handler — which is where a classic /generate failure gets its
                # scrubbed journal entry (Diagnostics / recent errors) AND its
                # classified, actionable message. Both have to be reproduced
                # here or a streaming generation failure is invisible in the
                # diagnostic bundle and opaque to the user (#1607). The raw
                # exception is NOT logged: it can carry a reference-clip path or
                # a provider secret, and only the journal scrubs before storing.
                logger.error(
                    "Streaming generation failed unexpectedly (class=%s)",
                    type(exc).__name__,
                )
                from core.public_errors import stream_generation_failure
                from core import error_journal

                error_journal.record(
                    exc, route="/generate", trace=traceback.format_exc()
                )
                yield _line({"type": "error", **stream_generation_failure(exc)})
            finally:
                # Ownership of the temp reference clip moves to this generator
                # in stream mode (the route returns before rendering starts).
                if cleanup_ref and ref_audio_path:
                    with contextlib.suppress(OSError):
                        os.remove(ref_audio_path)

        # Routing notice (#21): known before the stream starts, so it rides the
        # same headers the classic path uses — and now also carries "your
        # chosen worker was unavailable, this ran here".
        _stream_headers = _apply_routing_headers({
            "X-Seed": str(used_seed) if used_seed is not None else "",
            "Cache-Control": "no-cache",
        }, _routing_notice, _decision)
        return StreamingResponse(
            _stream_events(),
            media_type="application/x-ndjson",
            headers=_stream_headers,
        )

    # #1330: text whose chunk rendered to nothing. The engine dropping a chunk
    # is silent in the waveform — the take sounds clean and is simply missing a
    # sentence — so the render collects what it lost here and the response says
    # so. A warning in a log the user never opens is a record of the bug, not a
    # fix for it.
    _dropped_text: list = []
    _already_marked = False
    try:
        if _remote:
            # One op, one worker, the whole render — including the chunk loop.
            audio_tensor, sample_rate = await _render_on_worker()
            _already_marked = True
        else:
            # The gateway owns the dispatch on both branches. Locally it still
            # lands in run_on_gpu_pool_guarded, so the #730 bound + pool reset
            # that keeps a wedged generate from bricking the backend is
            # unchanged.
            if _backend is not None:
                _local_render = functools.partial(
                    _run_backend_inference,
                    _backend, text, language, ref_audio_path, ref_text, instruct,
                    duration, num_step, guidance_scale, speed, denoise,
                    postprocess_output, used_seed, effect_preset,
                    max_chunk_chars, crossfade_ms, dropped_sink=_dropped_text,
                )
            else:
                _local_render = functools.partial(
                    _run_inference,
                    _model, text, language, ref_audio_path, ref_text, instruct, duration,
                    num_step, guidance_scale, speed, t_shift, denoise,
                    postprocess_output, layer_penalty_factor, position_temperature,
                    class_temperature, used_seed, effect_preset,
                    max_chunk_chars, crossfade_ms, dropped_sink=_dropped_text,
                )
            audio_tensor = await gpu_gateway.run(
                _REMOTE_OP,
                local=gpu_gateway.LocalCall(
                    _local_render, what="TTS generate",
                    timeout=_generate_timeout_s(text, execution_device=_routing["effective_device"]),
                    min_vram_gb=_engine_min_vram_gb,
                ),
                decision=_decision,
            )
            # Read after generation: engines with lazy model loading report
            # their real rate only once weights are up.
            sample_rate = (_backend.sample_rate if _backend is not None
                           else _model.sampling_rate)
        # Watermark → save → history → prune → emit, shared with the streaming
        # path (see _finalize_generation) so both flows produce identical takes.
        audio_tensor, _meta = await _finalize_generation(
            audio_tensor, sample_rate, text=text, history_mode=history_mode,
            ref_audio_path=ref_audio_path, language=language, instruct=instruct,
            resolved_profile_id=resolved_profile_id, used_seed=used_seed,
            start_time=start_time, already_marked=_already_marked,
        )
        audio_id = _meta["id"]
        audio_filename = _meta["filename"]
        audio_dur = _meta["duration"]
        gen_time = _meta["gen_time"]

        buffer = io.BytesIO()
        _safe_torchaudio_save(buffer, audio_tensor, sample_rate, format="wav")
        buffer.seek(0)
        wav_bytes = buffer.read()

        async def _stream_wav():
            chunk_size = 16384
            for i in range(0, len(wav_bytes), chunk_size):
                yield wav_bytes[i:i + chunk_size]

        _resp_headers = {
            "X-Audio-Id": audio_id,
            "X-Gen-Time": str(gen_time),
            "X-Audio-Path": audio_filename,
            "X-Seed": str(used_seed) if used_seed is not None else "",
            "X-Audio-Duration": str(audio_dur),
            "Content-Length": str(len(wav_bytes)),
        }
        # #1330: the body is a WAV, so the header channel carries the notice
        # that some of the text produced no audio. Count first (always exact),
        # then as much of the lost text as a header can safely hold.
        if _dropped_text:
            from services.engine_routing import header_safe_reason
            _resp_headers["X-OmniVoice-Dropped-Chunks"] = str(len(_dropped_text))
            _lost = header_safe_reason(" | ".join(t for t in _dropped_text if t))
            if _lost:
                _resp_headers["X-OmniVoice-Dropped-Text"] = _lost
        # Routing notice (#21): cpu_fallback, accelerated-with-caveat, or the
        # machine this render ran on. The WAV body is binary so the header
        # channel is the carrier.
        _apply_routing_headers(_resp_headers, _routing_notice, _decision)
        return StreamingResponse(
            _stream_wav(),
            media_type="audio/wav",
            headers=_resp_headers,
        )
    except HTTPException:
        raise
    except gpu_gateway.ModelNotDownloaded as e:
        size_bytes = None
        try:
            from api.routers.setup.models import KNOWN_MODELS

            sizes = [m.get("size_gb") for m in KNOWN_MODELS if m.get("repo_id") in e.repo_ids]
            if sizes and all(size is not None for size in sizes):
                size_bytes = int(sum(float(size) for size in sizes) * 1024**3)
        except Exception:
            pass
        raise HTTPException(status_code=409, detail={
            "error": "model_not_downloaded",
            "message": str(e),
            "engine": e.engine,
            "repo_ids": e.repo_ids,
            "size_bytes": size_bytes,
            "target": e.target,
            "target_label": e.target_label,
            "downloadable": e.downloadable,
        }) from e
    except gpu_gateway.RemoteJobFailed as e:
        # Rule 2 of the fallback policy: a single-shot interactive render that
        # failed ON the worker is reported, not silently redone here. Minutes
        # already went somewhere else, the user is watching, and quietly
        # re-rendering on the slower machine turns a 20-second wait into a
        # four-minute one with no explanation. The header names the target so
        # the client can offer "run it on this machine instead" — a resubmit
        # the user chose, with a wait they were told about.
        logger.error("Remote generate failed on %s: %s", _target_label, e)
        raise HTTPException(
            status_code=503,
            detail=f"{e} {e.hint or 'Run it on this machine instead, or pick another GPU.'}",
            headers={"X-OmniVoice-Retryable": "true",
                     "X-OmniVoice-Routing": "remote_failed",
                     "Retry-After": "10"},
        ) from e
    except GpuPoolBusyError as e:
        # Saturation, not failure (#1190): the job never started, so the caller
        # can retry the identical request. Retry-After + the retryable marker
        # make that machine-readable for scripted clients.
        logger.warning("Generate refused — GPU pool saturated: %s", e)
        raise HTTPException(
            status_code=503, detail=str(e),
            headers={"Retry-After": str(e.retry_after),
                     "X-OmniVoice-Retryable": "true"},
        ) from e
    except GpuJobTimeoutError as e:
        # A generate that really ran and overran its budget (#730 class). The
        # abandoned worker still holds the device until it drains — the message
        # says so, and Retry-After spaces the retry out accordingly.
        logger.error("Generate timed out: %s", e)
        raise HTTPException(
            status_code=503, detail=str(e),
            headers={"Retry-After": "30", "X-OmniVoice-Retryable": "true"},
        ) from e
    except InvalidBinaryError as e:
        # #1172 class: a managed engine binary is a placeholder / corrupt /
        # refused by the OS. The message carries the repair hint — surface it
        # as 503 (engine unavailable), not a generic 500.
        logger.error("Engine binary preflight failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        logger.error("Validation failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Inference failed: %s\n%s", e, tb)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Couldn't synthesize audio. See Settings → Logs → Backend for the full trace. "
                f"Underlying error: {_safe_exc_text(e)}"
            ),
        )
    finally:
        if cleanup_ref and ref_audio_path:
            with contextlib.suppress(OSError):
                os.remove(ref_audio_path)

def _safe_output_path(name):
    if not name:
        return None
    base = os.path.basename(name)
    if base != name:
        return None
    outputs_real = os.path.realpath(OUTPUTS_DIR)
    candidate = os.path.realpath(os.path.join(OUTPUTS_DIR, base))
    if not candidate.startswith(outputs_real + os.sep):
        return None
    return candidate


def _remove_wav_if_unreferenced(conn, audio_path, exclude_ids=()):
    """Delete a history WAV from OUTPUTS_DIR — but only when no *other*
    generation_history row still references the same file.

    History WAVs are uniquely owned by their row (lock/save-as-profile COPY
    into VOICES_DIR, exports copy to the user's destination), so this guard is
    normally a no-op — it exists so any future path that duplicates a row can
    never make a delete/prune yank audio out from under a surviving take."""
    if not audio_path:
        return
    p = _safe_output_path(audio_path)
    if not p or not os.path.exists(p):
        return
    placeholders = ",".join("?" for _ in exclude_ids)
    others = conn.execute(
        "SELECT COUNT(*) FROM generation_history WHERE audio_path=?"
        + (f" AND id NOT IN ({placeholders})" if exclude_ids else ""),
        (audio_path, *exclude_ids),
    ).fetchone()[0]
    if others:
        return
    with contextlib.suppress(OSError):
        os.remove(p)


# How many takes to keep before pruning the oldest UNstarred ones (rows + their
# WAVs). User-tunable via Settings → Storage; 0 = unlimited. The pref key is
# shared with api/routers/settings.py (the GET/PUT endpoint) — same pattern as
# perf.torch_compile_disabled, which settings.py and engine_env.py both name.
HISTORY_CAP_PREF_KEY = "generation_history_cap"
DEFAULT_HISTORY_CAP = 200


def _history_cap() -> int:
    from core import prefs

    try:
        cap = int(prefs.get(HISTORY_CAP_PREF_KEY, DEFAULT_HISTORY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_CAP
    return max(0, cap)


def _prune_history_over_cap() -> int:
    """Retention: keep the newest ``_history_cap()`` takes; delete the oldest
    UNstarred rows over the cap plus their WAVs (via the unreferenced guard).
    Starred takes are never pruned — even when they alone exceed the cap.
    Returns the number of rows pruned."""
    cap = _history_cap()
    if cap <= 0:
        return 0  # 0 = unlimited
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
        excess = total - cap
        if excess <= 0:
            return 0
        victims = conn.execute(
            "SELECT id, audio_path FROM generation_history "
            "WHERE COALESCE(starred, 0)=0 ORDER BY created_at ASC LIMIT ?",
            (excess,),
        ).fetchall()
        if not victims:
            return 0
        victim_ids = [r["id"] for r in victims]
        conn.executemany(
            "DELETE FROM generation_history WHERE id=?", [(i,) for i in victim_ids]
        )
        for r in victims:
            _remove_wav_if_unreferenced(conn, r["audio_path"], exclude_ids=victim_ids)
        logger.info("history retention: pruned %d takes over the %d cap", len(victims), cap)
        return len(victims)


@router.get("/history")
def list_history():
    """The newest 50 generations plus every starred take, newest first, kept to
    rows whose audio still exists on disk.

    Starred takes ride along past the 50-row window so a keeper can never age
    off the rail. Rows whose WAV was deleted out-of-band (cleared outputs dir,
    manual cleanup) used to come back anyway and render dead players that 404
    on every fetch; prune them here so the UI never sees them again."""
    query = (
        "SELECT * FROM generation_history WHERE COALESCE(starred, 0)=1 "
        "OR id IN (SELECT id FROM generation_history ORDER BY created_at DESC LIMIT 50) "
        "ORDER BY created_at DESC"
    )
    with db_conn() as conn:
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError:
            # Same class as #710/#552: a DB that missed init or the additive
            # `starred` column. Heal once and retry inside this connection.
            ensure_schema()
            rows = conn.execute(query).fetchall()
        alive, stale_ids = [], []
        for r in rows:
            p = _safe_output_path(r["audio_path"]) if r["audio_path"] else None
            if r["audio_path"] and (not p or not os.path.exists(p)):
                stale_ids.append(r["id"])
            else:
                alive.append(dict(r))
        if stale_ids:
            conn.executemany(
                "DELETE FROM generation_history WHERE id=?",
                [(i,) for i in stale_ids],
            )
            logger.info("pruned %d stale history rows (audio file gone)", len(stale_ids))
    return alive


class _StarBody(BaseModel):
    starred: bool


@router.put("/history/{history_id}/starred")
def set_history_starred(history_id: str, body: _StarBody):
    """Star/unstar a take. Starred takes survive the retention cap and always
    appear in GET /history regardless of the recency window."""
    def _update():
        with db_conn() as conn:
            cur = conn.execute(
                "UPDATE generation_history SET starred=? WHERE id=?",
                (1 if body.starred else 0, history_id),
            )
            return cur.rowcount

    try:
        changed = _update()
    except sqlite3.OperationalError as e:
        # `no such column: starred` on a pre-migration DB (or the #710
        # missing-table class) — heal the schema and retry once.
        logger.warning("star update failed (%s); healing schema + retrying", e)
        ensure_schema()
        changed = _update()
    if not changed:
        raise HTTPException(
            status_code=404,
            detail="That take no longer exists — it may have been pruned or deleted.",
        )
    event_bus.emit("generation_history", {"action": "starred", "id": history_id})
    return {"id": history_id, "starred": body.starred}

@router.delete("/history")
def clear_history():
    with db_conn() as conn:
        rows = conn.execute("SELECT audio_path FROM generation_history").fetchall()
        for r in rows:
            p = _safe_output_path(r["audio_path"])
            if p and os.path.exists(p):
                with contextlib.suppress(OSError):
                    os.remove(p)
        conn.execute("DELETE FROM generation_history")
    event_bus.emit("generation_history")
    return {"cleared": True}

@router.delete("/history/{history_id}")
def delete_single_history(history_id: str):
    with db_conn() as conn:
        row = conn.execute("SELECT audio_path FROM generation_history WHERE id=?", (history_id,)).fetchone()
        conn.execute("DELETE FROM generation_history WHERE id=?", (history_id,))
        if row:
            # Row first, file second — the WAV goes only if no surviving take
            # still references it (see _remove_wav_if_unreferenced).
            _remove_wav_if_unreferenced(conn, row["audio_path"], exclude_ids=(history_id,))
    event_bus.emit("generation_history", {"action": "deleted", "id": history_id})
    return {"deleted": True}
