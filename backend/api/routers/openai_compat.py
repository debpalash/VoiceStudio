"""
OpenAI-compatible TTS & STT API — Phase 3.2 (ROADMAP.md P0).

Drop-in replacement for OpenAI's audio endpoints so that any tool speaking the
OpenAI protocol (the official ``openai`` SDK, the OpenAI Agents SDK voice
pipeline, pipecat, LiveKit, litellm, n8n, etc.) can use VoiceStudio as a local
backend with zero code changes.

Endpoints
─────────
    POST /v1/audio/speech          → TTS  (text → mp3/opus/aac/flac/wav/pcm)
    POST /v1/audio/transcriptions  → STT  (audio file → text/json)
    POST /v1/audio/translations    → STT + translate to English (Whisper engines)
    GET  /v1/models                → OpenAI aliases + installed engines
    GET  /v1/models/{id}           → one model
    GET  /v1/audio/voices          → list available voices (VoiceStudio extension)

Errors on these routes use OpenAI's shape,
``{"error": {"message", "type", "param", "code"}}``, so the SDK raises typed
errors with a readable message; request-validation failures are 400 (OpenAI's
status) rather than FastAPI's 422. The original ``detail`` is kept alongside
for existing VoiceStudio clients.

The router delegates to the active TTS/ASR backends via the same adapter
protocol used by the rest of VoiceStudio, so engine selection, GPU offloading,
model loading, and invisible provenance watermarking (services.watermark,
#1169) all work identically.

Reference: https://platform.openai.com/docs/api-reference/audio
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import io
import json
import logging
import os
import re
import tempfile
from typing import Annotated, Any, Literal, Optional, Union

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.model_manager import _gpu_pool, run_on_gpu_pool_guarded
from core.http_headers import content_disposition

logger = logging.getLogger("omnivoice.openai_compat")


# ── OpenAI-shaped errors ────────────────────────────────────────────────────


class OpenAIError(HTTPException):
    """An HTTPException that also names OpenAI's ``param`` and ``code``."""

    def __init__(self, status_code: int, message: str, *, param: Optional[str] = None,
                 code: Optional[str] = None, headers: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.param = param
        self.code = code


def _error_type(status: int) -> str:
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "server_error"
    return "invalid_request_error"


def _openai_error_body(status: int, message: str, *, param=None, code=None, detail=None) -> dict:
    return {
        "error": {
            "message": message,
            "type": _error_type(status),
            "param": param,
            "code": code,
        },
        # Kept for VoiceStudio-aware clients that read FastAPI's `detail`.
        "detail": message if detail is None else detail,
    }


def _http_error_response(exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    param = getattr(exc, "param", None)
    code = getattr(exc, "code", None)
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or json.dumps(detail))
        code = code or (detail.get("error") if isinstance(detail.get("error"), str) else None)
    elif isinstance(detail, str):
        message = detail
    else:
        message = json.dumps(detail, default=str)
    return JSONResponse(
        status_code=exc.status_code,
        content=_openai_error_body(exc.status_code, message, param=param, code=code, detail=detail),
        headers=getattr(exc, "headers", None),
    )


def _validation_error_response(exc: RequestValidationError) -> JSONResponse:
    # Echo neither `input` (can be an audio-sized body) nor `ctx` (can hold
    # a non-JSON exception object) — same hygiene as main's 422 handler.
    errors = [
        {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
        for e in exc.errors()
    ]
    first = errors[0] if errors else {"loc": [], "msg": "Invalid request"}
    loc = [str(p) for p in first["loc"] if p not in ("body", "query", "path", "header")]
    param = ".".join(loc) or None
    message = f"Invalid value for '{param}': {first['msg']}" if param else first["msg"]
    return JSONResponse(
        status_code=400,
        content=_openai_error_body(400, message, param=param, code="invalid_value", detail=errors),
    )


class OpenAIErrorRoute(APIRoute):
    """Route class that renders every error raised by a /v1 handler in
    OpenAI's error shape. Scoped to this router only — the rest of the API
    keeps FastAPI's ``{"detail": ...}`` contract."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return _validation_error_response(exc)
            except StarletteHTTPException as exc:
                return _http_error_response(exc)

        return route


router = APIRouter(prefix="/v1", tags=["OpenAI-Compatible Audio API"], route_class=OpenAIErrorRoute)


# ── OpenAI model / voice aliases ────────────────────────────────────────────

#: OpenAI TTS model ids advertised by GET /v1/models. Any id matching
#: ``_OPENAI_TTS_MODEL_RE`` (dated snapshots, future ``gpt-*-tts`` ids) is
#: accepted too, so an SDK upgrade that changes its default cannot 400.
OPENAI_TTS_MODELS = ("tts-1", "tts-1-hd", "gpt-4o-mini-tts")
_OPENAI_TTS_MODEL_RE = re.compile(r"^(tts-1(-hd)?|gpt-[a-z0-9.]+(-mini)?-tts)(-[0-9a-z-]+)?$")

#: OpenAI STT model ids. /v1/audio/transcriptions always uses the active ASR
#: engine; these are listed so clients that validate the model list find them.
OPENAI_STT_MODELS = ("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe")

# OpenAI's named voices aren't real voices in VoiceStudio. Map them to the
# active engine's default voice so callers that hardcode "alloy" (or the
# Agents SDK's default "ash") don't get an engine-preset error.
_OPENAI_VOICE_ALIASES = {
    "alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin",
    "nova", "onyx", "sage", "shimmer", "verse",
}


def is_openai_tts_model(model_id: str) -> bool:
    return model_id in OPENAI_TTS_MODELS or bool(_OPENAI_TTS_MODEL_RE.match(model_id))


# ── Schemas ─────────────────────────────────────────────────────────────────

SpeechFormat = Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]


class SpeechRequest(BaseModel):
    """POST /v1/audio/speech — mirrors OpenAI's CreateSpeechRequest."""

    model: str = Field(
        default="omnivoice",
        description=(
            "TTS model to use. Maps to VoiceStudio engine IDs: "
            "'omnivoice', 'voxcpm2', 'cosyvoice', 'mlx-audio', 'kittentts', 'moss-tts-nano'. "
            "OpenAI model ids ('tts-1', 'tts-1-hd', 'gpt-4o-mini-tts', dated "
            "snapshots) are aliases for the active engine."
        ),
    )
    input: str = Field(
        ...,
        max_length=4096,
        description="The text to synthesize. Max 4096 characters.",
    )
    voice: Union[str, dict] = Field(
        default="default",
        description=(
            "Voice to use. For VoiceStudio: pass a voice profile ID, 'default', "
            "or a KittenTTS preset name (also accepted as OpenAI's "
            "{\"id\": ...} object). OpenAI voice names (alloy, ash, ballad, "
            "coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, "
            "cedar) are accepted and mapped to the engine default."
        ),
    )
    instructions: Optional[str] = Field(
        default=None,
        max_length=4096,
        description="OpenAI's voice-style instructions, forwarded as the engine's style "
        "instruction (OmniVoice keeps only its voice-design tags). VoiceStudio's "
        "`instruct` wins when both are sent.",
    )
    response_format: SpeechFormat = Field(
        default="mp3",
        description="Audio output format. `pcm` is raw 24 kHz 16-bit little-endian mono.",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speed of the generated audio (0.25 to 4.0).",
    )
    stream_format: Optional[Literal["audio", "sse"]] = Field(
        default=None,
        description="'audio' streams raw audio bytes (the default); 'sse' streams "
        "`speech.audio.delta` / `speech.audio.done` server-sent events.",
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

    @field_validator("voice", mode="before")
    @classmethod
    def _voice_id(cls, v: Any) -> Any:
        # OpenAI accepts a custom voice as {"id": "voice_..."}; VoiceStudio's
        # equivalent is a voice-profile id, so unwrap it.
        if isinstance(v, dict):
            vid = v.get("id")
            if not isinstance(vid, str) or not vid:
                raise ValueError("voice object must be {\"id\": \"<voice id>\"}")
            return vid
        return v


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
    words: Optional[list[dict]] = None


# ── TTS: POST /v1/audio/speech ──────────────────────────────────────────────


def _known_engine_ids() -> str:
    try:
        from services.tts_backend import list_backends
        return ", ".join(b["id"] for b in list_backends())
    except Exception:
        return "omnivoice, voxcpm2, cosyvoice, kittentts, indextts2"


def _resolve_engine(model_id: str):
    """Map an OpenAI model name to a VoiceStudio backend."""
    from services.tts_backend import (
        get_backend_class, get_active_tts_backend, get_engine_instance_for,
    )

    # Direct engine ID match first, so a real engine id always means that
    # engine; then OpenAI model ids as pass-through to the active engine.
    try:
        cls = get_backend_class(model_id)
    except ValueError:
        if is_openai_tts_model(model_id):
            return get_active_tts_backend()
        raise OpenAIError(
            400,
            f"Unknown model '{model_id}'. Use an OpenAI TTS model id "
            f"({', '.join(OPENAI_TTS_MODELS)}) for the active engine, or a "
            f"VoiceStudio engine id: {_known_engine_ids()}. "
            "GET /v1/models lists what this server offers.",
            param="model", code="model_not_found",
        )
    ok, msg = cls.is_available()
    if not ok:
        raise OpenAIError(
            400, f"Engine '{model_id}' is not available: {msg}",
            param="model", code="model_not_available",
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


#: OpenAI's `pcm` format: raw 24 kHz, 16-bit signed little-endian, mono.
PCM_SAMPLE_RATE = 24000

#: (mime type, file extension) per response_format. The body always matches.
_FORMAT_MEDIA = {
    "mp3": ("audio/mpeg", "mp3"),
    "opus": ("audio/ogg", "opus"),
    "aac": ("audio/aac", "aac"),
    "flac": ("audio/flac", "flac"),
    "wav": ("audio/wav", "wav"),
    "pcm": ("audio/pcm", "pcm"),
}

#: Formats encoded by ffmpeg: codec args, container, sample rates the codec
#: accepts (anything else is resampled to OpenAI's 24 kHz).
_FFMPEG_FORMATS = {
    "mp3": (["-c:a", "libmp3lame", "-b:a", "128k"], "mp3",
            {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}),
    "opus": (["-c:a", "libopus", "-b:a", "64k"], "ogg", {48000}),
    "aac": (["-c:a", "aac", "-b:a", "128k"], "adts",
            {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000}),
}


def _cpu_float(wav_tensor):
    import torch
    t = wav_tensor
    if t.device.type != "cpu":
        t = t.cpu()
    if t.dtype != torch.float32:
        t = t.to(torch.float32)
    return t


def _pcm24k(wav_tensor, sample_rate: int) -> bytes:
    """Raw OpenAI PCM: mono, 24 kHz, int16 LE — resampled from the engine rate
    (VoxCPM2 is 48 kHz, KittenTTS 24 kHz, others vary) so clients that assume
    OpenAI's documented rate don't play it at the wrong speed."""
    import torch
    t = _cpu_float(wav_tensor)
    if t.ndim == 2:
        t = t.mean(dim=0)
    elif t.ndim != 1:
        t = t.reshape(-1)
    if sample_rate != PCM_SAMPLE_RATE:
        import torchaudio.functional as AF
        t = AF.resample(t, sample_rate, PCM_SAMPLE_RATE)
    t = t.clamp(-1.0, 1.0).contiguous()
    pcm = (t * 32767).round().clamp(-32768, 32767).to(torch.int16)
    # int16 tobytes() is host order; every supported host is little-endian,
    # but make it explicit so a big-endian build can't flip the samples.
    return pcm.numpy().astype("<i2", copy=False).tobytes()


def _ffmpeg_or_error(fmt: str) -> str:
    """Resolve ffmpeg for a compressed format, or raise an OpenAI-shaped 400
    BEFORE any GPU time is spent (never a WAV body labelled as mp3)."""
    from services.ffmpeg_utils import find_ffmpeg
    exe = find_ffmpeg()
    if not exe:
        raise OpenAIError(
            400,
            f"response_format '{fmt}' needs ffmpeg, which VoiceStudio could not find. "
            "Request 'wav', 'flac' or 'pcm', or install ffmpeg (Settings → Audio tools) "
            "or set FFMPEG_PATH.",
            param="response_format", code="unsupported_response_format",
        )
    return exe


async def _encode_ffmpeg(ffmpeg: str, wav_tensor, sample_rate: int, fmt: str) -> bytes:
    from services.audio_io import _safe_torchaudio_save
    from services.ffmpeg_utils import run_ffmpeg

    codec_args, container, rates = _FFMPEG_FORMATS[fmt]
    out_rate = sample_rate if sample_rate in rates else (48000 if fmt == "opus" else PCM_SAMPLE_RATE)
    fd, src = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        await asyncio.to_thread(_safe_torchaudio_save, src, _cpu_float(wav_tensor), sample_rate, format="wav")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", src,
               "-ar", str(out_rate), *codec_args, "-f", container, "pipe:1"]
        rc, out, err = await run_ffmpeg(cmd, timeout=300.0)
    finally:
        try:
            os.unlink(src)
        except OSError:
            # Best-effort temp cleanup: a leftover temp WAV must not turn an
            # encoded response (or the real encode error) into a failure.
            pass
    if rc != 0 or not out:
        from core.failure import strip_ffmpeg_banner
        tail = strip_ffmpeg_banner((err or b"").decode("utf-8", "replace")).strip()[-300:]
        raise OpenAIError(
            500, f"Encoding the audio as '{fmt}' failed ({tail or f'ffmpeg exit {rc}'}). "
            "Request 'wav', 'flac' or 'pcm' instead.",
            param="response_format", code="encoding_failed",
        )
    return out


async def _encode_audio(wav_tensor, sample_rate: int, fmt: str,
                        ffmpeg: Optional[str] = None) -> tuple[bytes, str, str]:
    """Encode a torch tensor to the requested audio format.

    Returns ``(bytes, mime_type, file_ext)``; the bytes are always the format
    the mime type names — a missing encoder is an error, never a silent WAV.
    """
    from services.audio_io import _safe_torchaudio_save

    mime, ext = _FORMAT_MEDIA[fmt]
    if fmt == "pcm":
        return await asyncio.to_thread(_pcm24k, wav_tensor, sample_rate), mime, ext
    if fmt in ("wav", "flac"):
        buf = io.BytesIO()
        await asyncio.to_thread(_safe_torchaudio_save, buf, wav_tensor, sample_rate, format=fmt)
        return buf.getvalue(), mime, ext
    ffmpeg = ffmpeg or await asyncio.to_thread(_ffmpeg_or_error, fmt)
    return await _encode_ffmpeg(ffmpeg, wav_tensor, sample_rate, fmt), mime, ext


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
        return OpenAIError(400, str(e), param="input")
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


def _engine_instructions(backend, instructions: Optional[str]) -> Optional[str]:
    """OpenAI ``instructions`` for this engine, or None.

    OpenAI treats them as free-form, best-effort style prose (tts-1 ignores
    them), and the Agents SDK sends a prose default on every request. The
    OmniVoice family's ``instruct`` is a closed vocabulary of voice-design
    tags that rejects prose, so it gets only the tags it knows ("female,
    whisper" survives; "speak cheerfully" is dropped) instead of failing the
    request. Engines with free-text instructions get the text unchanged.
    """
    if not instructions:
        return None
    from services.tts_backend import OmniVoiceBackend
    if (
        isinstance(backend, OmniVoiceBackend)
        or getattr(backend, "supports_native_omnivoice_controls", False)
        or str(getattr(backend, "id", "")).startswith("omnivoice")
    ):
        from omnivoice.utils.voice_design import sanitize_instruct
        return sanitize_instruct(instructions) or None
    return instructions


_STREAM_CHUNK = 32 * 1024


def _sse_events(audio: bytes):
    """OpenAI's speech SSE stream: base64 `speech.audio.delta` events, then a
    `speech.audio.done`. VoiceStudio synthesises the whole clip first, so the
    events carry the finished audio in chunks (no token usage to report)."""
    for i in range(0, len(audio), _STREAM_CHUNK):
        delta = base64.b64encode(audio[i:i + _STREAM_CHUNK]).decode("ascii")
        yield f"data: {json.dumps({'type': 'speech.audio.delta', 'audio': delta})}\n\n"
    done = {"type": "speech.audio.done",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
    yield f"data: {json.dumps(done)}\n\n"


def _audio_chunks(audio: bytes):
    for i in range(0, len(audio), _STREAM_CHUNK):
        yield audio[i:i + _STREAM_CHUNK]


@router.post("/audio/speech")
async def create_speech(req: SpeechRequest):
    """Generate audio from text. Compatible with OpenAI's POST /v1/audio/speech."""
    backend = _resolve_engine(req.model)

    # Compressed formats need ffmpeg: fail fast, before any model load or GPU
    # work, and never fall back to a body that doesn't match the format.
    ffmpeg = None
    if req.response_format in _FFMPEG_FORMATS:
        ffmpeg = await asyncio.to_thread(_ffmpeg_or_error, req.response_format)

    # Routing gate (#21 — no silent CPU fallback), identical to REST /generate.
    from core.device_caps import detect_host_caps
    from services.engine_routing import routing_notice, runtime_compute_profile_async
    _routing = await runtime_compute_profile_async(
        backend, detect_host_caps()
    )
    if _routing["routing_status"] == "unavailable":
        raise OpenAIError(400, _routing["routing_reason"], param="model", code="model_not_available")
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
    # OpenAI's `instructions` maps onto VoiceStudio's `instruct` (it used to be
    # dropped silently); an explicit `instruct` wins.
    instruct = req.instruct or _engine_instructions(backend, req.instructions)
    if instruct:
        kw["instruct"] = instruct
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
                if row["instruct"] and not instruct:
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
            headers={"Retry-After": "30", "X-OmniVoice-Retryable": "true"},
        ) from e
    except Exception as e:
        if type(e).__name__ == "ModelLoadInterruptedByShutdown":
            raise
        # A sidecar engine's load can also hit the #1172 class (broken venv
        # interpreter / placeholder binary) — surface the typed 503 here too.
        http = _typed_speech_http_error(e)
        if http is None:
            # #2298: an untyped load failure — the weight download refused,
            # DNS gone, the mirror down — used to re-raise into the generic
            # 500. Same actionable sentence as /generate's twin catch; this
            # route keeps a string detail because its errors are read by
            # OpenAI-shaped clients.
            from core.public_errors import model_load_failure

            logger.error("OpenAI TTS model load failed")
            raise HTTPException(
                status_code=503,
                detail=str(model_load_failure(backend.id, e)["detail"]),
                headers={"Retry-After": "30", "X-OmniVoice-Retryable": "true"},
            ) from e
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

    audio_bytes, mime_type, ext = await _encode_audio(wav, sr, req.response_format, ffmpeg)

    _headers = {
        "Content-Disposition": content_disposition(f"speech.{ext}", disposition="inline"),
    }
    if _routing_notice:
        from services.engine_routing import header_safe_reason
        _headers["X-OmniVoice-Routing"] = _routing_notice[0]
        _hr = header_safe_reason(_routing_notice[1])
        if _hr:
            _headers["X-OmniVoice-Routing-Reason"] = _hr
    if req.stream_format == "sse":
        _headers.pop("Content-Disposition")
        return StreamingResponse(_sse_events(audio_bytes), media_type="text/event-stream",
                                 headers=_headers)
    if req.stream_format == "audio":
        # Chunked transfer (no Content-Length), like OpenAI's streamed audio.
        return StreamingResponse(_audio_chunks(audio_bytes), media_type=mime_type, headers=_headers)
    _headers["Content-Length"] = str(len(audio_bytes))
    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type=mime_type,
        headers=_headers,
    )


# ── STT: POST /v1/audio/transcriptions and /v1/audio/translations ──────────

_TRANSCRIPT_FORMATS = ("json", "text", "srt", "verbose_json", "vtt")


class _TaskUnsupported(Exception):
    """The active ASR engine cannot perform the requested task (translate)."""


class _ModelNotActive(Exception):
    """``model`` names a VoiceStudio ASR engine that is not the one serving."""

    def __init__(self, requested: str, active: str):
        super().__init__(requested)
        self.requested = requested
        self.active = active


def _is_asr_engine_id(model: Optional[str]) -> bool:
    if not model:
        return False
    from services.asr_backend import _REGISTRY
    try:
        return model in _REGISTRY
    except Exception:
        return False


def _backend_request_kwargs(backend, options: dict) -> dict:
    """The subset of OpenAI-derived decode options this backend's
    ``transcribe()`` declares. An engine that can't honour one (e.g. a
    CTC model has no prompt) is simply not handed it."""
    from services.asr_backend import TRANSCRIBE_REQUEST_OPTIONS
    try:
        params = inspect.signature(backend.transcribe).parameters
    except (TypeError, ValueError):
        return {}
    return {
        k: v for k, v in options.items()
        if k in TRANSCRIBE_REQUEST_OPTIONS and k in params and v is not None
    }


def _reported_language(result: dict, requested: Optional[str], task: str) -> str:
    if task == "translate":
        return "en"  # OpenAI reports the output language for translations
    lang = result.get("language")
    if isinstance(lang, str) and lang and lang.lower() not in ("auto", "unknown", "none"):
        return lang
    return requested or "unknown"


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _result_segments(result: dict) -> list[dict]:
    segments = result.get("segments") or []
    if segments:
        return segments
    # Engines that only return `chunks` (transformers pipeline shape).
    out = []
    for c in result.get("chunks") or []:
        ts = c.get("timestamp") or (None, None)
        out.append({"text": c.get("text", ""), "start": ts[0], "end": ts[1]})
    return out


def _openai_segments(segments: list[dict], temperature: Optional[float]) -> list[dict]:
    """OpenAI TranscriptionSegment shape; fields an engine doesn't report
    get neutral values rather than being omitted."""
    return [
        {
            "id": i,
            "seek": int(seg.get("seek", 0) or 0),
            "start": _num(seg.get("start")),
            "end": _num(seg.get("end"), _num(seg.get("start"))),
            "text": seg.get("text", ""),
            "tokens": list(seg.get("tokens") or []),
            "temperature": _num(seg.get("temperature"), temperature or 0.0),
            "avg_logprob": _num(seg.get("avg_logprob")),
            "compression_ratio": _num(seg.get("compression_ratio")),
            "no_speech_prob": _num(seg.get("no_speech_prob")),
        }
        for i, seg in enumerate(segments)
    ]


def _openai_words(segments: list[dict]) -> list[dict]:
    words = []
    for seg in segments:
        for w in seg.get("words") or []:
            token = w.get("word", w.get("text"))
            # Aligners leave numerals/symbols untimed; OpenAI words are timed.
            if token is None or w.get("start") is None or w.get("end") is None:
                continue
            words.append({"word": str(token).strip(), "start": _num(w["start"]), "end": _num(w["end"])})
    return words


async def _transcribe_request(
    *, task: str, file: UploadFile, model: Optional[str] = None,
    language: Optional[str], prompt: Optional[str],
    response_format: str, temperature: Optional[float],
    timestamp_granularities: Optional[list[str]] = None, stream: Optional[bool] = None,
):
    from services.asr_backend import (
        ASRModelMissingError,
        asr_model_missing_detail,
        asr_model_missing_error,
        load_active_asr_backend,
    )

    if response_format not in _TRANSCRIPT_FORMATS:
        raise OpenAIError(
            400, f"Unsupported response_format '{response_format}'. Use one of: "
            f"{', '.join(_TRANSCRIPT_FORMATS)}.",
            param="response_format", code="invalid_value",
        )
    if stream:
        raise OpenAIError(
            400, "stream=true is not supported here. Use WS /v1/audio/transcriptions/stream "
            "for live partial transcripts.", param="stream", code="unsupported_parameter",
        )
    if temperature is not None and not 0 <= temperature <= 1:
        raise OpenAIError(400, "temperature must be between 0 and 1.", param="temperature",
                          code="invalid_value")
    granularities = set(timestamp_granularities or [])
    bad = granularities - {"word", "segment"}
    if bad:
        raise OpenAIError(
            400, f"Unsupported timestamp_granularities {sorted(bad)}; use 'word' and/or 'segment'.",
            param="timestamp_granularities", code="invalid_value",
        )
    want_words = response_format == "verbose_json" and "word" in granularities

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
        raise OpenAIError(400, f"Could not read audio file: {e}", param="file")

    options = {
        "language": language or None,
        "initial_prompt": prompt or None,
        "temperature": temperature,
        "task": "translate" if task == "translate" else None,
    }

    try:
        # Run transcription in the thread pool to avoid blocking the event loop,
        # bounded so a stuck/starved ASR returns a 504 with guidance instead of
        # hanging the request forever (see run_transcribe_guarded).
        from services.asr_backend import run_transcribe_guarded
        # Word timestamps cost an alignment pass: only when they're returned.
        word_ts = want_words

        # `load_active_asr_backend`, not `get_active_asr_backend`: the latter is
        # a pure selector, so a backend whose shallow `is_available()` probe
        # passes but whose deep import chain is broken (whisperx →
        # ctranslate2 failing to dlopen on a hardened kernel) reached
        # `.transcribe()` and 500'd, even with a healthy engine next in line.
        # The loader does select + ensure_loaded + degrade (#1185). It loads
        # weights, so it belongs inside the pool with the transcribe call —
        # never on the event loop.
        # OpenAI model ids (and any other name) mean "the active engine". A
        # VoiceStudio ASR engine id is a concrete choice: serve it only when
        # it is the engine actually loaded, never silently substitute another.
        explicit_engine = model if _is_asr_engine_id(model) else None

        def _run():
            backend = load_active_asr_backend()
            if explicit_engine and getattr(backend, "id", None) != explicit_engine:
                raise _ModelNotActive(explicit_engine, getattr(backend, "id", "?"))
            extra = _backend_request_kwargs(backend, options)
            if task == "translate" and not (
                "task" in extra and getattr(backend, "supports_translation", lambda: False)()
            ):
                # Includes Whisper turbo / English-only checkpoints, which
                # would return the source language labelled as English.
                raise _TaskUnsupported(backend.id)
            return backend.transcribe(tmp_path, word_timestamps=word_ts, **extra)

        result = await run_transcribe_guarded(_gpu_pool, _run, what="OpenAI")

        segments = _result_segments(result)
        full_text = (result.get("text") or "").strip() if not segments else " ".join(
            (seg.get("text") or "").strip() for seg in segments
        ).strip()
        detected_lang = _reported_language(result, language, task)

        # Format response based on requested format
        if response_format == "text":
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(full_text)

        if response_format == "verbose_json":
            duration = _num(result.get("duration"))
            if not duration and segments:
                duration = _num(segments[-1].get("end"))
            return VerboseTranscriptionResponse(
                task=task,
                language=detected_lang,
                duration=duration,
                text=full_text,
                segments=_openai_segments(segments, temperature),
                words=_openai_words(segments) if want_words else None,
            ).model_dump(exclude_none=True)

        if response_format == "srt":
            from fastapi.responses import PlainTextResponse
            srt_lines = []
            for i, seg in enumerate(segments, 1):
                start = _num(seg.get("start"))
                end = _num(seg.get("end"), start)
                text = (seg.get("text") or "").strip()
                srt_lines.append(
                    f"{i}\n"
                    f"{_format_ts_srt(start)} --> {_format_ts_srt(end)}\n"
                    f"{text}\n"
                )
            return PlainTextResponse("\n".join(srt_lines), media_type="text/plain")

        if response_format == "vtt":
            from fastapi.responses import PlainTextResponse
            from services.srt_parser import escape_webvtt_text
            vtt_lines = ["WEBVTT\n"]
            for seg in segments:
                start = _num(seg.get("start"))
                end = _num(seg.get("end"), start)
                text = escape_webvtt_text((seg.get("text") or "").strip(), preserve_markup=False)
                vtt_lines.append(
                    f"{_format_ts_vtt(start)} --> {_format_ts_vtt(end)}\n{text}\n"
                )
            return PlainTextResponse("\n".join(vtt_lines), media_type="text/vtt")

        # Default: json
        return TranscriptionResponse(text=full_text)

    except HTTPException:
        raise
    except _ModelNotActive as e:
        raise OpenAIError(
            400,
            f"Speech-recognition engine '{e.requested}' is not the active engine "
            f"('{e.active}' is). Select it in Model Catalogue, or send an OpenAI model "
            "id such as 'whisper-1' to use the active engine.",
            param="model", code="model_not_active",
        )
    except _TaskUnsupported as e:
        raise OpenAIError(
            400,
            f"The active speech-recognition engine '{e}' cannot translate with its "
            "current model. Translation needs a multilingual Whisper checkpoint "
            "(large-v3, medium, …) on faster-whisper, whisperx, mlx-whisper or "
            "pytorch-whisper; turbo, distil and English-only (.en) models are "
            "transcription-only. Change it in Model Catalogue, or use "
            "/v1/audio/transcriptions.",
            param="model", code="unsupported_task",
        )
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
        from core.failure import NO_AUDIO_TRACK_MESSAGE, NoAudioTrackError
        from services.ffmpeg_utils import raise_for_audio_extract_failure
        try:
            await asyncio.to_thread(raise_for_audio_extract_failure, str(e), tmp_path)
        except NoAudioTrackError:
            raise OpenAIError(400, NO_AUDIO_TRACK_MESSAGE, param="file", code="no_audio_track")
        logger.exception("OpenAI transcription failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


_MODEL_DESC = (
    "ASR model. OpenAI ids ('whisper-1', 'gpt-4o-transcribe', "
    "'gpt-4o-mini-transcribe') are served by the active engine; a VoiceStudio "
    "ASR engine id must name the active engine (400 otherwise)."
)
#: The SDK sends list fields as `name[]` in multipart forms; accept both.
_Granularities = Annotated[
    Optional[list[str]],
    Form(alias="timestamp_granularities[]", description="'word' and/or 'segment' (verbose_json)."),
]
_GranularitiesPlain = Annotated[Optional[list[str]], Form(alias="timestamp_granularities")]
_Stream = Annotated[Optional[bool], Form(description="Streaming is not supported (400).")]


@router.post("/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form(default="whisper-1", description=_MODEL_DESC),
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
        description="Sampling temperature (0–1). Used by engines that support it.",
    ),
    timestamp_granularities: _Granularities = None,
    timestamp_granularities_plain: _GranularitiesPlain = None,
    stream: _Stream = None,
):
    """Transcribe audio to text. Compatible with OpenAI's POST /v1/audio/transcriptions."""
    return await _transcribe_request(
        task="transcribe", file=file, model=model, language=language, prompt=prompt,
        response_format=response_format, temperature=temperature,
        timestamp_granularities=(timestamp_granularities or []) + (timestamp_granularities_plain or []),
        stream=stream,
    )


@router.post("/audio/translations")
async def create_translation(
    file: UploadFile = File(..., description="Audio file to translate into English"),
    model: str = Form(default="whisper-1", description=_MODEL_DESC),
    prompt: Optional[str] = Form(default=None, description="Optional text to guide the model's style."),
    response_format: str = Form(default="json", description="Output format: json, text, verbose_json, srt, vtt."),
    temperature: Optional[float] = Form(default=None, description="Sampling temperature (0–1)."),
):
    """Translate speech into English text. Compatible with OpenAI's
    POST /v1/audio/translations; needs a Whisper-family ASR engine."""
    return await _transcribe_request(
        task="translate", file=file, model=model, language=None, prompt=prompt,
        response_format=response_format, temperature=temperature,
    )


# ── Models: GET /v1/models ──────────────────────────────────────────────────


def _model_entry(model_id: str, kind: str, alias: bool) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "voicestudio",
        # VoiceStudio extension: what the id does here.
        "voicestudio": {"kind": kind, "alias_for_active_engine": alias},
    }


def _model_list() -> list[dict]:
    """Every id listed here is one the audio routes actually honour: TTS
    requests route to any installed engine, while transcription always runs
    on the active ASR engine — so only that one is listed for STT."""
    data = [_model_entry(m, "tts", True) for m in OPENAI_TTS_MODELS]
    data += [_model_entry(m, "stt", True) for m in OPENAI_STT_MODELS]
    seen = {m["id"] for m in data}
    try:
        from services.tts_backend import list_backends
        for b in list_backends():
            if b.get("available") and b.get("id") not in seen:
                seen.add(b["id"])
                data.append(_model_entry(b["id"], "tts", False))
    except Exception:
        logger.warning("Could not list TTS engines for /v1/models", exc_info=True)
    try:
        from services.asr_backend import active_backend_id
        active = active_backend_id()
        if active and active not in seen:
            data.append(_model_entry(active, "stt", False))
    except Exception:
        logger.warning("Could not resolve the active ASR engine for /v1/models", exc_info=True)
    return data


@router.get("/models")
def list_models():
    """OpenAI-shaped model list: OpenAI aliases plus every installed engine."""
    return {"object": "list", "data": _model_list()}


@router.get("/models/{model_id:path}")
def retrieve_model(model_id: str):
    """One model from GET /v1/models (OpenAI's retrieve shape)."""
    for m in _model_list():
        if m["id"] == model_id:
            return m
    if is_openai_tts_model(model_id):
        return _model_entry(model_id, "tts", True)
    raise OpenAIError(404, f"The model '{model_id}' does not exist on this VoiceStudio server.",
                      param="model", code="model_not_found")


# ── Voices: GET /v1/audio/voices (VoiceStudio extension) ─────────────────────


@router.get("/audio/voices")
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
