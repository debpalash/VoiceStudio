"""
OpenAI-compatible TTS & STT API — Phase 3.2 (ROADMAP.md P0).

Drop-in replacement for OpenAI's audio endpoints so that any tool speaking the
OpenAI protocol (Claude, Cursor, LangChain, litellm, etc.) can use VoiceStudio
as a local backend with zero code changes.

Endpoints
─────────
    POST /v1/audio/speech          → TTS  (text → wav/mp3/opus/flac)
    POST /v1/audio/transcriptions  → STT  (audio file → text/json)
    GET  /v1/audio/voices          → list available voices (VoiceStudio extension)

The router delegates to the active TTS/ASR backends via the same adapter
protocol used by the rest of VoiceStudio, so engine selection, GPU offloading,
model loading, and invisible provenance watermarking (services.watermark,
#1169) all work identically.

Reference: https://platform.openai.com/docs/api-reference/audio
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.model_manager import _gpu_pool, run_on_gpu_pool_guarded
from core.http_headers import content_disposition

logger = logging.getLogger("omnivoice.openai_compat")

router = APIRouter(prefix="/v1/audio", tags=["OpenAI-Compatible Audio API"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class SpeechRequest(BaseModel):
    """POST /v1/audio/speech — mirrors OpenAI's CreateSpeechRequest."""

    model: str = Field(
        default="omnivoice",
        description=(
            "TTS model to use. Maps to VoiceStudio engine IDs: "
            "'omnivoice', 'voxcpm2', 'cosyvoice', 'mlx-audio', 'kittentts', 'moss-tts-nano'. "
            "Also accepts 'tts-1' and 'tts-1-hd' as aliases for the active engine."
        ),
    )
    input: str = Field(
        ...,
        max_length=4096,
        description="The text to synthesize. Max 4096 characters.",
    )
    voice: str = Field(
        default="default",
        description=(
            "Voice to use. For VoiceStudio: pass a voice profile ID, 'default', "
            "or a KittenTTS preset name. OpenAI voice names (alloy, echo, fable, "
            "onyx, nova, shimmer) are accepted but mapped to defaults."
        ),
    )
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = Field(
        default="mp3",
        description="Audio output format.",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speed of the generated audio (0.25 to 4.0).",
    )
    # VoiceStudio extensions (not part of OpenAI spec, but accepted if sent)
    language: Optional[str] = Field(default=None, description="Language code (ISO 639-1)")
    description: Optional[str] = Field(
        default=None,
        description="Voice description for voice design (VoxCPM2 only). "
        "E.g. 'young female, warm tone, slight British accent'.",
    )
    instruct: Optional[str] = Field(default=None, description="Style instruction for the TTS engine.")
    duration: Optional[float] = Field(
        default=None,
        gt=0,
        description="VoiceStudio extension: target output duration in seconds.",
    )
    seed: Optional[int] = Field(
        default=None,
        description="VoiceStudio extension: deterministic sampling seed.",
    )
    denoise: bool = Field(
        default=True,
        description="VoiceStudio extension: prepend denoise control when supported.",
    )
    preprocess_prompt: bool = Field(
        default=True,
        description="VoiceStudio extension: trim/preprocess reference prompt when supported.",
    )
    chunk_duration: Optional[float] = Field(
        default=None,
        ge=0,
        description="OmniVoice GGUF extension: long-form internal chunk duration.",
    )
    chunk_threshold: Optional[float] = Field(
        default=None,
        ge=0,
        description="OmniVoice GGUF extension: long-form internal chunk threshold.",
    )
    # #1014: these two were silently DISCARDED before (pydantic ignores
    # undeclared fields) — a 200 OK that quietly dropped the caller's quality
    # knobs. Declared now and passed through, matching the native /generate
    # form fields (defaults there: num_step=16, guidance_scale=2.0; the
    # model's documented "quality" preset is num_step=32).
    num_step: Optional[int] = Field(
        default=None,
        ge=1,
        le=128,
        description="VoiceStudio extension: iterative unmasking steps (app default 16; 32 = the model's documented quality preset).",
    )
    guidance_scale: Optional[float] = Field(
        default=None,
        gt=0,
        le=20,
        description="VoiceStudio extension: classifier-free guidance scale (app default 2.0).",
    )


class TranscriptionResponse(BaseModel):
    """Mirrors OpenAI's CreateTranscriptionResponse."""

    text: str


class VerboseTranscriptionResponse(BaseModel):
    """Mirrors OpenAI's verbose_json transcription response."""

    task: str = "transcribe"
    language: str = ""
    duration: float = 0.0
    text: str = ""
    segments: list[dict] = Field(default_factory=list)


# ── OpenAI voice name mapping ──────────────────────────────────────────────

# OpenAI's 6 named voices aren't real voices in VoiceStudio. Map them to
# sensible defaults so callers that hardcode "alloy" don't get a 400.
_OPENAI_VOICE_ALIASES = {
    "alloy", "echo", "fable", "onyx", "nova", "shimmer",
}


# ── TTS: POST /v1/audio/speech ──────────────────────────────────────────────


def _resolve_engine(model_id: str):
    """Map an OpenAI model name to a VoiceStudio backend."""
    from services.tts_backend import (
        get_backend_class, get_active_tts_backend, get_engine_instance_for,
    )

    # Accept OpenAI model names as pass-through to the active engine.
    if model_id in ("tts-1", "tts-1-hd"):
        return get_active_tts_backend()

    # Direct engine ID match
    try:
        cls = get_backend_class(model_id)
        ok, msg = cls.is_available()
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"Engine '{model_id}' is not available: {msg}",
            )
        from services.tts_backend import OmniVoiceBackend
        if cls is OmniVoiceBackend:
            # OmniVoice only ever runs as the shared active engine — the
            # explicit-omnivoice request is the active-engine request.
            return get_active_tts_backend()
        # Cached singleton, not a fresh cls(): SubprocessBackend engines would
        # spawn a sidecar process and reload their model on EVERY request, and
        # register a new atexit hook each time (get_engine_instance's contract).
        # No router-local cache on top of it: the shared cache is keyed by
        # CLASS precisely so id rebinds/evictions can't serve a stale instance,
        # and cross-engine memory discipline is create_speech's
        # evict_other_tts_engines call (the same seam /generate uses) — not a
        # bespoke unload here.
        return get_engine_instance_for(model_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown model '{model_id}'. Use one of: "
                "omnivoice, voxcpm2, cosyvoice, mlx-audio, kittentts, "
                "moss-tts-nano, indextts2, gpt-sovits, sherpa-onnx, tts-1, tts-1-hd."
            ),
        )


def _encode_audio(wav_tensor, sample_rate: int, fmt: str) -> tuple[bytes, str, str]:
    """Encode a torch tensor to the requested audio format. Returns (bytes, mime_type, file_ext)."""
    from services.audio_io import _safe_torchaudio_save

    if fmt == "wav":
        buf = io.BytesIO()
        _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="wav")
        return buf.getvalue(), "audio/wav", "wav"

    if fmt == "flac":
        buf = io.BytesIO()
        _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="flac")
        return buf.getvalue(), "audio/flac", "flac"

    if fmt == "mp3":
        # torchaudio can write mp3 if ffmpeg backend is available.
        # Fall back to wav if it can't.
        buf = io.BytesIO()
        try:
            _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="mp3")
            return buf.getvalue(), "audio/mpeg", "mp3"
        except Exception:
            # ffmpeg not available — fall back to wav. Reset the buffer
            # in case the failed mp3 attempt wrote partial bytes.
            buf.seek(0)
            buf.truncate(0)
            _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="wav")
            return buf.getvalue(), "audio/wav", "wav"

    if fmt == "opus":
        buf = io.BytesIO()
        try:
            _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="ogg")
            return buf.getvalue(), "audio/ogg", "opus"
        except Exception:
            buf2 = io.BytesIO()
            _safe_torchaudio_save(buf2, wav_tensor, sample_rate, format="wav")
            return buf2.getvalue(), "audio/wav", "wav"

    if fmt == "pcm":
        # Raw 16-bit little-endian PCM, no header. Still apply the same
        # clamp + dtype + contig invariants the helper enforces; we just
        # can't go through it because this branch produces raw samples,
        # not a container.
        import torch
        t = wav_tensor
        if t.device.type != "cpu":
            t = t.cpu()
        if t.dtype != torch.float32:
            t = t.to(torch.float32)
        t = t.clamp(-1.0, 1.0).contiguous()
        pcm = (t * 32767).clamp(-32768, 32767).to(torch.int16)
        return pcm.numpy().tobytes(), "audio/pcm", "pcm"

    # AAC — not widely supported by torchaudio, fall back to wav
    buf = io.BytesIO()
    _safe_torchaudio_save(buf, wav_tensor, sample_rate, format="wav")
    return buf.getvalue(), "audio/wav", "wav"


def _typed_speech_http_error(e: Exception) -> Optional[HTTPException]:
    """Map typed synthesis failures to actionable HTTP errors (#1172/#1173).

    - TTSInputError (bad caller input, e.g. nothing speakable) → 400,
      matching /generate's ValueError→400 mapping.
    - InvalidBinaryError (managed engine binary is a placeholder / corrupt /
      refused by the OS) → 503 with the repair hint, instead of the bare
      "[Errno 8] Exec format error" 500.
    - TimeoutError (#1190/#1202: pool saturation or a job that overran its
      execution budget) → 503 + Retry-After + X-OmniVoice-Retryable, instead of
      the 500 a scripted client can't distinguish from a real crash. Matched on
      the BUILTIN base, not GpuJobTimeoutError by name, so a mid-suite module
      reload can't break the isinstance check (same rationale as the load-path
      catch below).
    Returns None for anything else (caller falls through to the generic 500).
    """
    from services.binary_preflight import InvalidBinaryError
    from services.tts_backend import TTSInputError

    if isinstance(e, TTSInputError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, InvalidBinaryError):
        return HTTPException(status_code=503, detail=str(e))
    if isinstance(e, TimeoutError):
        return HTTPException(
            status_code=503, detail=str(e),
            headers={"Retry-After": str(getattr(e, "retry_after", 30)),
                     "X-OmniVoice-Retryable": "true"},
        )
    return None


def _run_tts(backend, text: str, kw: dict):
    """Run TTS inference in the GPU thread pool."""
    from services.audio_dsp import apply_mastering, normalize_audio
    from services.watermark import mark_synthetic
    wav = backend.generate(text, **kw)
    sr = backend.sample_rate
    # Engines that already emit mastered, studio-grade audio (e.g. VoxCPM2's
    # native 48 kHz) opt out of apply_mastering via `applies_own_mastering`.
    # That chain's highpass + Compressor is tuned for VoiceStudio's 24 kHz clone
    # output; applied to a studio engine it adds an audible level pump that
    # degrades the very output we want clean. Loudness normalisation still
    # runs — it's a benign peak scale, not dynamics.
    if not getattr(backend, "applies_own_mastering", False):
        wav = apply_mastering(wav, sample_rate=sr)
    wav = normalize_audio(wav, target_dBFS=-2.0)
    # Invisible AudioSeal provenance mark at the tensor stage, before any
    # container encoding (#1169 — this route used to return unmarked audio
    # while /generate marked the same text). Same failure semantics as
    # /generate: pref-gated, no-op without AudioSeal, passes audio through
    # unchanged on any failure — never blocks the response.
    wav = mark_synthetic(wav, sr, context="openai_compat.speech")
    return wav, sr


@router.post("/speech")
async def create_speech(req: SpeechRequest):
    """Generate audio from text. Compatible with OpenAI's POST /v1/audio/speech."""
    backend = _resolve_engine(req.model)

    # Routing gate (#21 — no silent CPU fallback), identical to REST /generate.
    from core.device_caps import detect_host_caps
    from services.engine_routing import routing_notice, runtime_compute_profile_async
    _routing = await runtime_compute_profile_async(
        backend, detect_host_caps()
    )
    if _routing["routing_status"] == "unavailable":
        raise HTTPException(status_code=400, detail=_routing["routing_reason"])
    _routing_notice = routing_notice(_routing)  # (status, reason) or None

    # Build kwargs for the backend's generate() method
    kw: dict = {
        "speed": req.speed,
        "denoise": req.denoise,
        "preprocess_prompt": req.preprocess_prompt,
    }
    if req.duration is not None:
        kw["duration"] = req.duration
    if req.seed is not None:
        kw["seed"] = req.seed
    if req.chunk_duration is not None:
        kw["chunk_duration"] = req.chunk_duration
    if req.chunk_threshold is not None:
        kw["chunk_threshold"] = req.chunk_threshold
    if req.num_step is not None:
        kw["num_step"] = req.num_step
    if req.guidance_scale is not None:
        kw["guidance_scale"] = req.guidance_scale
    if req.language:
        kw["language"] = req.language
    if req.instruct:
        kw["instruct"] = req.instruct
    if req.description:
        kw["description"] = req.description

    # Voice handling: if it's a known OpenAI alias, use defaults.
    # If it's a UUID-like string, treat it as a profile_id and resolve ref_audio.
    voice = req.voice
    if voice not in _OPENAI_VOICE_ALIASES and voice != "default":
        # Try to resolve as a voice profile ID
        try:
            from core.db import db_conn
            from core.config import VOICES_DIR
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM voice_profiles WHERE id=?", (voice,)
                ).fetchone()
            if row:
                if row["is_locked"] and row["locked_audio_path"]:
                    kw["ref_audio"] = os.path.join(VOICES_DIR, row["locked_audio_path"])
                elif row["ref_audio_path"]:
                    kw["ref_audio"] = os.path.join(VOICES_DIR, row["ref_audio_path"])
                if row["ref_text"]:
                    kw["ref_text"] = row["ref_text"]
                if row["instruct"] and not req.instruct:
                    kw["instruct"] = row["instruct"]
                if req.seed is None and row["seed"] is not None:
                    kw["seed"] = row["seed"]
            else:
                # Not a profile ID — forward as engine preset name
                kw["voice"] = voice
        except Exception:
            # Not a profile ID — might be a KittenTTS preset or similar
            kw["voice"] = voice

    # Engine-agnostic text normalization (junk strip, numbers→words,
    # abbreviations) at this route's text→engine choke point — the same
    # pre-pass as /generate, applied exactly once per request. `req.language`
    # is everything this route knows about the language (None → universal
    # safety filters only). Pref-gated (default ON), idempotent, never raises.
    from services.text_normalization import normalize_for_tts
    text = normalize_for_tts(req.input, req.language)

    # VRAM eviction runs in get_model()'s warm-return path now, covering every
    # native TTS generate (this route, WS TTS, dub, batch, audiobook).

    # Single-active-engine memory discipline (MM2-01), the same call /generate
    # makes before its load: hand back every OTHER resident TTS engine's model
    # before this one warms up, so switching `model` ids across requests —
    # explicit id → explicit id, or explicit id → the tts-1/omnivoice aliases —
    # can't stack multi-GB engines/sidecars. No-op when nothing else is
    # resident; opt out with OMNIVOICE_SINGLE_ENGINE_RESIDENT=0.
    from services.engine_memory import evict_other_tts_engines
    await evict_other_tts_engines(backend.id)

    # ── #1033/#1037/#1014: warm the engine under the LOAD budget before the
    # generate clock starts. The T4 verification (#1014) measured a fresh
    # install's first /v1/audio/speech burning its whole 300s generate budget
    # on the multi-GB checkpoint download (0% GPU util throughout) and dying
    # with a misleading "too heavy for the available compute" error. Model
    # loading gets OMNIVOICE_MODEL_LOAD_TIMEOUT (default 1200s); once warm
    # this is a per-request no-op.
    from services.model_manager import _model_load_timeout
    try:
        await run_on_gpu_pool_guarded(
            backend.ensure_ready,
            what=f"TTS engine '{backend.id}' model load",
            timeout=_model_load_timeout(),
        )
    # Catch the BUILTIN TimeoutError base, not GpuJobTimeoutError by name:
    # several tests reload services.model_manager mid-suite, so a class
    # imported at call time can differ in identity from the one the guard
    # (bound at this module's import) actually raises — the except would
    # silently miss. The builtin base has one identity forever. (Caught by
    # this exact test failing CI-only, in full-suite order.)
    except TimeoutError as e:
        logger.warning("engine load exceeded the model-load budget: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                f"TTS engine '{backend.id}' did not finish loading within its "
                f"model-load budget — on a first run this usually means the weight "
                f"download is slow or stalled (check the engine's Weights list in Model Catalogue for "
                f"progress), not that generation failed. Retry once the model "
                f"shows as installed."
            ),
        ) from e
    except Exception as e:
        # A sidecar engine's load can also hit the #1172 class (broken venv
        # interpreter / placeholder binary) — surface the typed 503 here too.
        http = _typed_speech_http_error(e)
        if http is None:
            raise
        logger.warning("OpenAI TTS engine load failed: %s", e)
        raise http from e

    # Admission control at SUBMIT (#1190/#1202). This is the scripted-client
    # surface: a script fanning out N requests at a 1-worker pool used to get N
    # silent multi-minute waits and then "too heavy for the available compute".
    # Refusing up front with 429 + Retry-After lets a client back off correctly,
    # and costs an interactive user nothing (the policy only trips when a full
    # wave of jobs is ALREADY queued — see check_gpu_admission).
    from services.model_manager import check_gpu_admission
    try:
        check_gpu_admission(what="OpenAI TTS generate")
    except TimeoutError as e:
        logger.warning("OpenAI TTS refused — GPU pool saturated: %s", e)
        raise HTTPException(
            status_code=429, detail=str(e),
            headers={"Retry-After": str(getattr(e, "retry_after", 30)),
                     "X-OmniVoice-Retryable": "true"},
        ) from e

    try:
        # Bounded + pool-reset on hang so a wedged TTS request can't starve the
        # GPU pool and brick the backend (#730 class). The budget is the shared
        # length-scaled one (#1190) — this route used to hardcode the flat 300s,
        # so long inputs failed here even after v0.3.22 shipped the scaling.
        from services.model_manager import generate_timeout_s
        wav, sr = await run_on_gpu_pool_guarded(
            lambda: _run_tts(backend, text, kw), what="OpenAI TTS generate",
            timeout=generate_timeout_s(text, engine=backend))
    except Exception as e:
        # #1172/#1173: typed failures get their real status + actionable
        # message (400 bad input / 503 broken engine binary) instead of a
        # generic 500 wrapping an errno or an ONNX abort.
        http = _typed_speech_http_error(e)
        if http is not None:
            logger.warning("OpenAI TTS failed (typed): %s", e)
            raise http from e
        logger.exception("OpenAI TTS failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    audio_bytes, mime_type, ext = _encode_audio(wav, sr, req.response_format)

    _headers = {
        "Content-Length": str(len(audio_bytes)),
        "Content-Disposition": content_disposition(f"speech.{ext}", disposition="inline"),
    }
    if _routing_notice:
        from services.engine_routing import header_safe_reason
        _headers["X-OmniVoice-Routing"] = _routing_notice[0]
        _hr = header_safe_reason(_routing_notice[1])
        if _hr:
            _headers["X-OmniVoice-Routing-Reason"] = _hr
    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type=mime_type,
        headers=_headers,
    )


# ── STT: POST /v1/audio/transcriptions ──────────────────────────────────────


@router.post("/transcriptions")
async def create_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form(
        default="whisper-1",
        description=(
            "ASR model. Accepts 'whisper-1' (maps to active engine), or an "
            "VoiceStudio engine ID: whisperx, faster-whisper, mlx-whisper, pytorch-whisper."
        ),
    ),
    language: Optional[str] = Form(
        default=None,
        description="Language of the input audio (ISO 639-1). Optional.",
    ),
    prompt: Optional[str] = Form(
        default=None,
        description="Optional text to guide the model's style or continue a previous segment.",
    ),
    response_format: str = Form(
        default="json",
        description="Output format: json, text, verbose_json, srt, vtt.",
    ),
    temperature: Optional[float] = Form(
        default=None,
        description="Sampling temperature (0–1). Not used by all backends.",
    ),
):
    """Transcribe audio to text. Compatible with OpenAI's POST /v1/audio/transcriptions."""
    from services.asr_backend import (
        ASRModelMissingError,
        asr_model_missing_detail,
        asr_model_missing_error,
        load_active_asr_backend,
    )

    # TTS-only install: no ASR model on disk → actionable 409, BEFORE any
    # backend load could silently auto-download multi-GB whisper weights.
    # Same typed detail shape as /transcribe (capture.py): the machine fields
    # (`error`, `missing_repo_id`, `recommended`) let VoiceStudio-aware clients
    # render the one-click download CTA, while `message` keeps a human-readable
    # line for generic OpenAI-compat clients.
    missing = await asyncio.to_thread(asr_model_missing_error)
    if missing is not None:
        raise HTTPException(
            status_code=409,
            detail={**missing, "message": asr_model_missing_detail(missing)},
        )

    # Write uploaded file to a temp location
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read audio file: {e}")

    try:
        # Run transcription in the thread pool to avoid blocking the event loop,
        # bounded so a stuck/starved ASR returns a 504 with guidance instead of
        # hanging the request forever (see run_transcribe_guarded).
        from services.asr_backend import run_transcribe_guarded
        word_ts = response_format == "verbose_json"

        # `load_active_asr_backend`, not `get_active_asr_backend`: the latter is
        # a pure selector, so a backend whose shallow `is_available()` probe
        # passes but whose deep import chain is broken (whisperx →
        # ctranslate2 failing to dlopen on a hardened kernel) reached
        # `.transcribe()` and 500'd, even with a healthy engine next in line.
        # The loader does select + ensure_loaded + degrade (#1185). It loads
        # weights, so it belongs inside the pool with the transcribe call —
        # never on the event loop.
        def _run():
            backend = load_active_asr_backend()
            return backend.transcribe(tmp_path, word_timestamps=word_ts)

        result = await run_transcribe_guarded(_gpu_pool, _run, what="OpenAI")

        # Extract the full text from segments
        segments = result.get("segments", [])
        chunks = result.get("chunks", [])
        full_text = " ".join(
            seg.get("text", "").strip()
            for seg in (segments if segments else chunks)
        ).strip()
        detected_lang = result.get("language", language or "en")

        # Format response based on requested format
        if response_format == "text":
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(full_text)

        if response_format == "verbose_json":
            duration = result.get("duration", 0.0)
            if not duration and segments:
                last = segments[-1]
                duration = last.get("end", 0.0)
            return VerboseTranscriptionResponse(
                task="transcribe",
                language=detected_lang,
                duration=duration,
                text=full_text,
                segments=[
                    {
                        "id": i,
                        "text": seg.get("text", ""),
                        "start": seg.get("start", 0.0),
                        "end": seg.get("end", 0.0),
                    }
                    for i, seg in enumerate(segments)
                ],
            )

        if response_format == "srt":
            from fastapi.responses import PlainTextResponse
            srt_lines = []
            for i, seg in enumerate(segments, 1):
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "").strip()
                srt_lines.append(
                    f"{i}\n"
                    f"{_format_ts_srt(start)} --> {_format_ts_srt(end)}\n"
                    f"{text}\n"
                )
            return PlainTextResponse("\n".join(srt_lines), media_type="text/plain")

        if response_format == "vtt":
            from fastapi.responses import PlainTextResponse
            vtt_lines = ["WEBVTT\n"]
            for seg in segments:
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "").strip()
                vtt_lines.append(
                    f"{_format_ts_vtt(start)} --> {_format_ts_vtt(end)}\n{text}\n"
                )
            return PlainTextResponse("\n".join(vtt_lines), media_type="text/vtt")

        # Default: json
        return TranscriptionResponse(text=full_text)

    except HTTPException:
        raise
    except ASRModelMissingError as e:
        # A degraded-to candidate has no weights on disk. Same typed 409 the
        # preflight above raises — never a 500, and never a silent multi-GB
        # auto-download.
        raise HTTPException(
            status_code=409,
            detail={**e.payload, "message": asr_model_missing_detail(e.payload)},
        )
    except TimeoutError as e:
        # ASRTimeoutError (subclass): backend alive, ASR too heavy for compute.
        logger.warning("OpenAI transcription timed out: %s", e)
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.exception("OpenAI transcription failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Voices: GET /v1/audio/voices (VoiceStudio extension) ─────────────────────


@router.get("/voices")
def list_voices():
    """List available voices. VoiceStudio extension to the OpenAI API."""
    from services.tts_backend import list_backends

    backends = list_backends()
    voices = []

    # Always include the OpenAI standard voice names as aliases
    for name in sorted(_OPENAI_VOICE_ALIASES):
        voices.append({
            "voice_id": name,
            "name": name.capitalize(),
            "type": "openai_alias",
            "description": f"OpenAI '{name}' voice — maps to the active VoiceStudio engine's default voice.",
        })

    # Include voice profiles from the database
    try:
        from core.db import db_conn
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, language FROM voice_profiles ORDER BY name"
            ).fetchall()
        for row in rows:
            voices.append({
                "voice_id": row["id"],
                "name": row["name"],
                "type": "profile",
                "language": row["language"],
            })
    except Exception:
        logger.warning("Voice profiles could not be loaded; returning built-in aliases only")

    return {"voices": voices, "engines": backends}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _format_ts_srt(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    from services.srt_parser import format_cue_timestamp
    return format_cue_timestamp(seconds, ",")


def _format_ts_vtt(seconds: float) -> str:
    """Format seconds as VTT timestamp: HH:MM:SS.mmm"""
    from services.srt_parser import format_cue_timestamp
    return format_cue_timestamp(seconds, ".")
