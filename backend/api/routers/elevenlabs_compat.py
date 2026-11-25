"""ElevenLabs-compatible API under ``/elevenlabs``.

Apps, SDKs and automations written for ElevenLabs can use VoiceStudio by
changing their base URL to ``http://<host>:<port>/elevenlabs``. The routes
translate ElevenLabs requests onto the OpenAI-compatible handlers in
:mod:`api.routers.openai_compat`, so engine routing, licence gates, GPU
admission, pronunciation dictionaries and the synthetic-audio watermark behave
exactly as they do on ``/v1/audio/speech``.

Supported: text to speech (``/v1/text-to-speech/{voice_id}`` and ``/stream``,
every ``output_format`` family), speech to text (``/v1/speech-to-text``), the
voice changer (``/v1/speech-to-speech/{voice_id}``), sound effects
(``/v1/sound-generation``), voice isolation (``/v1/audio-isolation``), the
voice list (``/v1/voices``, ``/v2/voices``, ``/v1/voices/{voice_id}``), default
voice settings and the model list. ElevenLabs model ids (``eleven_*``) use the
active VoiceStudio engine; a VoiceStudio engine id picks that engine.

Credentials: loopback callers need none. Remote callers send VoiceStudio's API
key as ``xi-api-key`` (what ElevenLabs SDKs send) or as a Bearer token.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.dependencies import require_consumer

logger = logging.getLogger("omnivoice.elevenlabs_compat")

#: Path prefix of every route here; ``core.auth`` accepts ``xi-api-key`` only
#: below it.
ELEVENLABS_PREFIX = "/elevenlabs"

_SOURCE_RATE = 24000  # the OpenAI handler's raw PCM rate

#: output_format → (family, sample rate). Families: mp3/opus/wav go through
#: the OpenAI encoder; pcm/ulaw/alaw are built here from its 24 kHz PCM.
_OUTPUT_FORMAT_RE = re.compile(r"^(mp3|pcm|ulaw|alaw|opus|wav)_(\d{4,5})(?:_(\d{2,3}))?$")
_PCM_RATES = {8000, 16000, 22050, 24000, 44100, 48000}
_MEDIA = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "ulaw": "audio/basic",
    "alaw": "audio/x-alaw-basic",
}
#: OpenAPI: audio routes answer with audio in the requested format, not JSON.
AUDIO_RESPONSES = {200: {
    "description": "Audio in the requested output_format.",
    "content": {media: {"schema": {"type": "string", "format": "binary"}}
                for media in dict.fromkeys(_MEDIA.values())},
}}
_DEFAULT_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 1.0,
}
#: ElevenLabs voice ids are 20 alphanumerics. One that is not a VoiceStudio
#: profile is a 404, not an engine preset that would fail less clearly.
_ELEVENLABS_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{20}$")


# ── Errors: ElevenLabs' {"detail": {"status", "message"}} shape ─────────────


def _status_slug(exc: StarletteHTTPException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    detail = exc.detail
    if isinstance(detail, dict):
        for key in ("status", "code", "error"):
            if isinstance(detail.get(key), str):
                return detail[key]
    return {
        400: "invalid_request", 401: "invalid_api_key", 403: "permission_denied",
        404: "not_found", 409: "conflict", 422: "invalid_request",
        429: "too_many_concurrent_requests", 503: "service_unavailable",
    }.get(exc.status_code, "internal_error")


def _message(detail: Any) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or json.dumps(detail, default=str))
    if isinstance(detail, str):
        return detail
    return json.dumps(detail, default=str)


def _error_response(exc: StarletteHTTPException) -> JSONResponse:
    detail = {"status": _status_slug(exc), "message": _message(exc.detail)}
    if isinstance(exc.detail, dict):
        # Keep VoiceStudio's machine fields (e.g. the missing ASR model).
        detail.update({k: v for k, v in exc.detail.items() if k not in detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": detail},
                        headers=getattr(exc, "headers", None))


class ElevenLabsErrorRoute(APIRoute):
    """Render handler errors in ElevenLabs' shape; validation stays FastAPI's
    422 list, which is also what ElevenLabs returns."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route(request):
            try:
                return await handler(request)
            except StarletteHTTPException as exc:
                return _error_response(exc)

        return route


def _error(status: int, slug: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"status": slug, "message": message})


router = APIRouter(
    prefix=ELEVENLABS_PREFIX,
    tags=["ElevenLabs-Compatible API"],
    route_class=ElevenLabsErrorRoute,
    dependencies=[Depends(require_consumer)],
)


# ── Output formats and audio conversion ─────────────────────────────────────


_MP3_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000}
# Bitrates libmp3lame encodes exactly (anything else is rounded): MPEG-1 for
# 32/44.1/48 kHz, MPEG-2 and 2.5 below that. The API floor is 32 kbps.
_MP3_KBPS_MPEG1 = {32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320}
_MP3_KBPS_MPEG2 = {32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160}


def _mp3_kbps(rate: int) -> set[int]:
    return _MP3_KBPS_MPEG1 if rate >= 32000 else _MP3_KBPS_MPEG2
_WAV_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}


def output_spec(value: str) -> tuple[str, int, Optional[int]]:
    """``"mp3_22050_32"`` → ``("mp3", 22050, 32)``; a 422 for any combination
    the encoders can't produce exactly as named."""
    m = _OUTPUT_FORMAT_RE.match(value or "")
    if m:
        family, rate = m.group(1), int(m.group(2))
        bitrate = int(m.group(3)) if m.group(3) else None
        ok = {
            "ulaw": rate == 8000 and bitrate is None,
            "alaw": rate == 8000 and bitrate is None,
            "pcm": rate in _PCM_RATES and bitrate is None,
            "wav": rate in _WAV_RATES and bitrate is None,
            "mp3": rate in _MP3_RATES and bitrate in _mp3_kbps(rate),
            "opus": rate == 48000 and bitrate is not None and 32 <= bitrate <= 256,
        }[family]
        if ok:
            return family, rate, bitrate
    raise _error(
        422, "invalid_output_format",
        f"Unsupported output_format '{value}'. Use mp3_44100_128 (default) or another "
        "mp3_<rate>_<kbps> at a bitrate MP3 supports for that rate, pcm_8000/16000/22050/24000/44100/48000, ulaw_8000, "
        "alaw_8000, opus_48000_<kbps> or wav_<rate>.",
    )


def parse_output_format(value: str) -> tuple[str, int]:
    """``"pcm_16000"`` → ``("pcm", 16000)``; raises a 422 for anything else."""
    family, rate, _ = output_spec(value)
    return family, rate


def lin2alaw(pcm16) -> bytes:
    """int16 PCM → G.711 A-law (Sun ``g711.c``; equals ``audioop.lin2alaw``)."""
    import numpy as np

    pcm = np.asarray(pcm16, dtype=np.int16).astype(np.int32) >> 3  # 13-bit
    positive = pcm >= 0
    mask = np.where(positive, 0xD5, 0x55)
    mag = np.where(positive, pcm, -pcm - 1)
    seg_end = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF], dtype=np.int32)
    seg = np.searchsorted(seg_end, mag, side="left")
    shift = np.where(seg < 2, 1, seg)
    aval = (seg << 4) | ((mag >> np.minimum(shift, 31)) & 0xF)
    aval = np.where(seg >= 8, 0x7F, aval)
    return (aval ^ mask).astype(np.uint8).tobytes()


def _encode_pcm16(pcm16, family: str) -> bytes:
    import numpy as np

    if family == "ulaw":
        from services.telephony.audio import lin2ulaw

        return lin2ulaw(pcm16)
    if family == "alaw":
        return lin2alaw(pcm16)
    return np.asarray(pcm16, dtype="<i2").tobytes()


class StreamResampler:
    """Gapless chunk-by-chunk resampling of 24 kHz int16 PCM.

    Each step resamples whole alignment blocks with ``_CONTEXT`` input samples
    of real audio on both sides, then keeps only the middle, so chunk edges
    come out identical to resampling the whole signal at once (no clicks at
    the boundaries a per-chunk resample would leave).
    """

    _MIN_CONTEXT = 128

    def __init__(self, target_rate: int, source_rate: int = _SOURCE_RATE):
        import numpy as np

        self.source = source_rate
        self.target = target_rate
        g = math.gcd(source_rate, target_rate)
        self.block_in = source_rate // g
        self.block_out = target_rate // g
        self.context = self.block_in * math.ceil(self._MIN_CONTEXT / self.block_in)
        self._left = np.zeros(0, dtype=np.float32)   # already emitted
        self._pending = np.zeros(0, dtype=np.float32)  # not yet emitted
        self._carry = b""  # odd trailing byte of a split int16 sample

    def _resample(self, samples):
        import torch
        import torchaudio.functional as AF

        if self.source == self.target or samples.size == 0:
            return samples
        return AF.resample(torch.from_numpy(samples), self.source, self.target).numpy()

    def _emit(self, count: int, right):
        import numpy as np

        body = self._pending[:count]
        left = self._left[-self.context:]
        window = np.concatenate([left, body, right])
        out = self._resample(window)
        start = len(left) // self.block_in * self.block_out
        out = out[start:start + count // self.block_in * self.block_out]
        self._left = np.concatenate([left, body])[-self.context:]
        self._pending = self._pending[count:]
        return out

    def feed(self, data: bytes):
        """Add PCM bytes; returns the int16 samples that are now final."""
        import numpy as np

        data = self._carry + data
        whole = len(data) - (len(data) % 2)
        self._carry = data[whole:]
        new = np.frombuffer(data[:whole], dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, new])
        ready = len(self._pending) - self.context
        count = (ready // self.block_in) * self.block_in if ready > 0 else 0
        if count <= 0:
            return np.zeros(0, dtype=np.int16)
        right = self._pending[count:count + self.context]
        return _to_int16(self._emit(count, right))

    def flush(self):
        """Emit everything left, padding the last block with silence."""
        import numpy as np

        if self._pending.size == 0:
            return np.zeros(0, dtype=np.int16)
        tail = len(self._pending)
        pad = (-tail) % self.block_in
        self._pending = np.concatenate([self._pending, np.zeros(pad, dtype=np.float32)])
        out = self._emit(len(self._pending), np.zeros(0, dtype=np.float32))
        keep = math.ceil(tail * self.target / self.source)
        return _to_int16(out[:keep])


def _to_int16(samples):
    import numpy as np

    return np.clip(np.round(np.asarray(samples) * 32768.0), -32768, 32767).astype(np.int16)


def convert_pcm24k(data: bytes, family: str, rate: int) -> bytes:
    """Whole 24 kHz int16 PCM body → ``family`` at ``rate``."""
    import numpy as np

    rs = StreamResampler(rate)
    samples = np.concatenate([rs.feed(data), rs.flush()])
    return _encode_pcm16(samples, family)


async def _convert_stream(body, family: str, rate: int):
    rs = StreamResampler(rate)
    async for chunk in body:
        out = rs.feed(chunk if isinstance(chunk, bytes) else bytes(chunk))
        if out.size:
            yield _encode_pcm16(out, family)
    out = rs.flush()
    if out.size:
        yield _encode_pcm16(out, family)


# ── Text to speech ──────────────────────────────────────────────────────────


class VoiceSettings(BaseModel):
    stability: Optional[float] = Field(default=None, ge=0, le=1)
    similarity_boost: Optional[float] = Field(default=None, ge=0, le=1)
    style: Optional[float] = Field(default=None, ge=0, le=1)
    use_speaker_boost: Optional[bool] = None
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0)


class TextToSpeechRequest(BaseModel):
    """ElevenLabs' text-to-speech body. ``stability``, ``similarity_boost``,
    ``style``, ``use_speaker_boost``, ``previous_text``/``next_text`` and
    pronunciation-dictionary locators have no VoiceStudio equivalent and are
    accepted without effect; VoiceStudio's own pronunciation dictionary still
    applies."""

    text: str = Field(..., min_length=1)
    model_id: Optional[str] = Field(default=None, description="'eleven_*' ids use the active engine; a VoiceStudio engine id picks that engine.")
    language_code: Optional[str] = None
    voice_settings: Optional[VoiceSettings] = None
    seed: Optional[int] = Field(default=None, ge=0, le=4294967295)

    model_config = {"extra": "allow"}


def _engine_model(model_id: Optional[str]) -> str:
    if not model_id or model_id.startswith("eleven_"):
        return "tts-1"  # the OpenAI alias for the active engine
    return model_id


def _profile_exists(voice_id: str) -> bool:
    from api.routers.openai_compat import _resolve_voice_profile

    return _resolve_voice_profile(voice_id) is not None


async def _speech(voice_id: str, body: TextToSpeechRequest, output_format: str, *, stream: bool):
    from api.routers.openai_compat import SpeechRequest, create_speech

    family, rate = parse_output_format(output_format)
    if _ELEVENLABS_VOICE_ID_RE.match(voice_id) and not _profile_exists(voice_id):
        raise _error(
            404, "voice_not_found",
            f"Voice '{voice_id}' is not a VoiceStudio voice. List the voices with "
            f"GET {ELEVENLABS_PREFIX}/v1/voices and use a voice_id from it.",
        )
    if len(body.text) > 4096:
        raise _error(
            400, "text_too_long",
            "VoiceStudio's text-to-speech API takes up to 4,096 characters per request. "
            "Split the text, or use the app's Long-form page for longer scripts.",
        )
    speed = body.voice_settings.speed if body.voice_settings and body.voice_settings.speed else 1.0
    # Raw families come from the handler's 24 kHz PCM (streamable); the rest
    # render WAV at the engine's own rate and are encoded here, so the rate
    # and bitrate named in output_format are the ones delivered.
    raw = family in ("pcm", "ulaw", "alaw")
    upstream_format = "pcm" if raw else "wav"
    if not raw:
        await _ffmpeg_ready(output_format)
    req = SpeechRequest(
        model=_engine_model(body.model_id),
        input=body.text,
        voice=voice_id,
        response_format=upstream_format,
        speed=speed,
        language=body.language_code,
        seed=body.seed,
        stream_format="audio" if stream and raw else None,
    )
    resp = await create_speech(req)
    media = _MEDIA[family]
    if not raw:
        import asyncio

        import soundfile as sf

        wav = b"".join([chunk async for chunk in resp.body_iterator])
        data, sr = await asyncio.to_thread(sf.read, io.BytesIO(wav), dtype="float32", always_2d=True)
        encoded = await _tensor_response(data, sr, output_format, _passthrough_headers(resp))
        if not stream:
            return encoded
        return StreamingResponse(_chunks(encoded.body), media_type=media,
                                 headers=_passthrough_headers(resp))
    if family == "pcm" and rate == _SOURCE_RATE:
        resp.headers["content-type"] = media
        return resp
    if stream:
        # Keep the original response object: it owns the native engine lock
        # and releases it when the client goes away.
        resp.body_iterator = _convert_stream(resp.body_iterator, family, rate)
        if "content-length" in resp.headers:
            del resp.headers["content-length"]
        resp.headers["content-type"] = media
        return resp
    data = b"".join([chunk async for chunk in resp.body_iterator])
    return Response(convert_pcm24k(data, family, rate), media_type=media,
                    headers=_passthrough_headers(resp))


def _passthrough_headers(resp) -> dict:
    """VoiceStudio's routing notices from the speech handler."""
    return {k: v for k, v in resp.headers.items() if k.lower().startswith("x-omnivoice-")}


def _chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


@router.post("/v1/text-to-speech/{voice_id}", response_class=Response, responses=AUDIO_RESPONSES)
async def text_to_speech(
    voice_id: str,
    body: TextToSpeechRequest,
    output_format: str = Query(default="mp3_44100_128"),
    enable_logging: Optional[bool] = Query(default=None, description="Accepted; VoiceStudio never sends requests anywhere."),
    optimize_streaming_latency: Optional[int] = Query(default=None, description="Accepted without effect."),
):
    """Speech from text, compatible with ElevenLabs' create-speech call."""
    return await _speech(voice_id, body, output_format, stream=False)


@router.post("/v1/text-to-speech/{voice_id}/stream", response_class=Response, responses=AUDIO_RESPONSES)
async def text_to_speech_stream(
    voice_id: str,
    body: TextToSpeechRequest,
    output_format: str = Query(default="mp3_44100_128"),
    enable_logging: Optional[bool] = Query(default=None),
    optimize_streaming_latency: Optional[int] = Query(default=None),
):
    """Streamed speech. PCM, μ-law and A-law arrive sentence by sentence
    (or chunk by chunk on engines with native streaming)."""
    return await _speech(voice_id, body, output_format, stream=True)


# ── Uploads ─────────────────────────────────────────────────────────────────

#: Recordings for transcription and isolation can be long; stream them to disk
#: in chunks under this cap rather than holding them in memory.
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
_UPLOAD_CHUNK = 1024 * 1024


async def _save_upload(upload: UploadFile, suffix_default: str = ".wav") -> str:
    """Stream ``upload`` to a new temp file and return its path."""
    import asyncio
    import os
    import tempfile

    from core.path_security import upload_suffix

    fd, path = tempfile.mkstemp(suffix=upload_suffix(upload.filename, suffix_default) or suffix_default)
    total = 0
    try:
        with os.fdopen(fd, "wb") as dest:
            while chunk := await upload.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise _error(413, "file_too_large", "The file is larger than 2 GB.")
                await asyncio.to_thread(dest.write, chunk)
    except BaseException:
        _unlink(path)
        raise
    return path


def _unlink(path: str) -> None:
    import os

    try:
        os.unlink(path)
    except OSError:
        pass  # already gone, or the OS still holds it; a temp file is best-effort cleanup


# ── Voices and models ───────────────────────────────────────────────────────


def _voice_entry(voice_id: str, name: str, category: str, *, language: Optional[str] = None,
                 description: Optional[str] = None) -> dict:
    return {
        "voice_id": voice_id,
        "name": name,
        "category": category,
        "description": description,
        "labels": {"language": language} if language else {},
        "preview_url": None,
        "settings": None,
        "fine_tuning": None,
        "available_for_tiers": [],
        "high_quality_base_model_ids": [],
        "verified_languages": [{"language": language}] if language else [],
    }


def _voices() -> list[dict]:
    voices = [_voice_entry(
        "default", "Default", "premade",
        description="The active engine's default voice.",
    )]
    try:
        from core.db import db_conn

        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, language FROM voice_profiles ORDER BY name, id"
            ).fetchall()
        voices += [_voice_entry(r["id"], r["name"], "cloned", language=r["language"]) for r in rows]
    except Exception:
        logger.warning("Voice profiles could not be read for the ElevenLabs voice list")
    return voices


@router.get("/v1/voices")
def list_voices():
    """Every VoiceStudio voice profile, plus ``default``."""
    return {"voices": _voices()}


@router.get("/v2/voices")
def search_voices(
    search: Optional[str] = Query(default=None),
    page_size: int = Query(default=10, ge=1, le=100),
    next_page_token: Optional[str] = Query(default=None),
):
    """ElevenLabs' paginated voice search, over the same list."""
    voices = _voices()
    if search:
        needle = search.casefold()
        voices = [v for v in voices if needle in v["name"].casefold()]
    try:
        start = max(int(next_page_token or 0), 0)
    except ValueError:
        raise _error(422, "invalid_page_token", "next_page_token must come from a previous response.")
    page = voices[start:start + page_size]
    more = start + page_size < len(voices)
    return {
        "voices": page,
        "has_more": more,
        "total_count": len(voices),
        "next_page_token": str(start + page_size) if more else None,
    }


@router.get("/v1/voices/settings/default")
def default_voice_settings():
    return dict(_DEFAULT_VOICE_SETTINGS)


@router.get("/v1/voices/{voice_id}/settings")
def voice_settings(voice_id: str):
    _voice_or_404(voice_id)
    return dict(_DEFAULT_VOICE_SETTINGS)


def _voice_or_404(voice_id: str) -> dict:
    for voice in _voices():
        if voice["voice_id"] == voice_id:
            return voice
    raise _error(404, "voice_not_found", f"Voice '{voice_id}' was not found.")


@router.get("/v1/voices/{voice_id}")
def get_voice(voice_id: str):
    return _voice_or_404(voice_id)


def _model_entry(model_id: str, name: str, description: str) -> dict:
    return {
        "model_id": model_id,
        "name": name,
        "description": description,
        "can_be_finetuned": False,
        "can_do_text_to_speech": True,
        "can_do_voice_conversion": False,
        "can_use_style": False,
        "can_use_speaker_boost": False,
        "serves_pro_voices": False,
        "requires_alpha_access": False,
        "max_characters_request_free_user": 4096,
        "max_characters_request_subscribed_user": 4096,
        "languages": [],
    }


#: ElevenLabs model ids listed for clients that validate the id they send.
ELEVENLABS_MODEL_ALIASES = (
    "eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3",
)


@router.get("/v1/models")
def list_models():
    """ElevenLabs model aliases (served by the active engine) plus every
    installed VoiceStudio engine."""
    models = [
        _model_entry(m, m, "Served by the active VoiceStudio engine.")
        for m in ELEVENLABS_MODEL_ALIASES
    ]
    try:
        from services.tts_backend import list_backends

        for b in list_backends():
            if b.get("available") and b.get("id"):
                models.append(_model_entry(b["id"], b.get("display_name") or b["id"],
                                           "VoiceStudio engine."))
    except Exception:
        logger.warning("Could not list TTS engines for the ElevenLabs model list")
    return models


# ── Speech to text ──────────────────────────────────────────────────────────


def _scribe_words(words: list[dict], speakers: Optional[list[Optional[str]]] = None) -> list[dict]:
    """OpenAI words → ElevenLabs words, with the ``spacing`` items it puts
    between them; ``speaker_id`` when ``speakers`` labels each word."""
    out: list[dict] = []
    for i, w in enumerate(words):
        speaker = speakers[i] if speakers else None
        if i:
            prev_end = out[-1]["end"]
            spacing = {"text": " ", "start": prev_end, "end": max(prev_end, w["start"]),
                       "type": "spacing", "logprob": 0.0}
            if speaker:
                spacing["speaker_id"] = speaker
            out.append(spacing)
        item = {"text": w["word"], "start": w["start"], "end": w["end"],
                "type": "word", "logprob": 0.0}
        if speaker:
            item["speaker_id"] = speaker
        out.append(item)
    return out


def _speaker_ids(labels: list[Optional[str]]) -> list[Optional[str]]:
    """Engine speaker labels → ElevenLabs' ``speaker_0``, ``speaker_1``, … in
    order of first appearance."""
    names: dict[str, str] = {}
    out: list[Optional[str]] = []
    for label in labels:
        if label is None:
            out.append(None)
            continue
        out.append(names.setdefault(label, f"speaker_{len(names)}"))
    return out


async def _diarize_words(path: str, words: list[dict], num_speakers: Optional[int]):
    """Per-word speaker ids, or ``(None, error)`` when identification can't run."""
    from services.model_manager import run_on_gpu_pool_guarded
    from services.transcript_diarization import diarize_segments

    spans = [{"start": w["start"], "end": w["end"]} for w in words]
    try:
        labels, error = await run_on_gpu_pool_guarded(
            lambda: diarize_segments(path, spans, num_speakers),
            what="Speaker identification",
            timeout=max(300.0, 2.0 * (spans[-1]["end"] if spans else 0.0)),
        )
    except TimeoutError:
        return None, "DIARIZATION_TIMEOUT"
    if error:
        return None, error
    return _speaker_ids(labels), None


@router.post("/v1/speech-to-text")
async def speech_to_text(
    file: UploadFile = File(...),
    model_id: str = Form(default="scribe_v1", description="Any value; the active VoiceStudio speech-recognition engine transcribes."),
    language_code: Optional[str] = Form(default=None),
    timestamps_granularity: str = Form(default="word"),
    diarize: bool = Form(default=False),
    tag_audio_events: bool = Form(default=True, description="Accepted without effect."),
    num_speakers: Optional[int] = Form(default=None, ge=1, le=32),
):
    """Transcription in ElevenLabs' response shape, from the active ASR
    engine. ``timestamps_granularity=character`` returns word timings.
    ``diarize=true`` labels each word's speaker; when speaker identification
    can't run, the transcript comes back unlabelled with the reason in
    ``X-OmniVoice-Diarization-Error``."""
    from starlette.datastructures import Headers
    from starlette.datastructures import UploadFile as StarletteUpload

    from api.routers.openai_compat import _transcribe_request

    if timestamps_granularity not in ("none", "word", "character"):
        raise _error(422, "invalid_timestamps_granularity",
                     "timestamps_granularity must be 'none', 'word' or 'character'.")
    want_words = timestamps_granularity != "none" or diarize
    kept = None
    upload = file
    with contextlib.ExitStack() as cleanup:
        if diarize:
            # Transcription deletes its own copy; speaker identification needs one too.
            kept = await _save_upload(file)
            cleanup.callback(_unlink, kept)
            upload = StarletteUpload(
                cleanup.enter_context(open(kept, "rb")), filename=file.filename,
                headers=Headers({"content-type": file.content_type or "application/octet-stream"}),
            )
        result = await _transcribe_request(
            task="transcribe", file=upload, model=None, language=language_code, prompt=None,
            response_format="verbose_json", temperature=None,
            timestamp_granularities=["word", "segment"] if want_words else ["segment"],
        )
        raw_words = result.get("words") or []
        speakers, error = (None, None)
        if diarize and raw_words:
            speakers, error = await _diarize_words(kept, raw_words, num_speakers)
    words = _scribe_words(raw_words, speakers) if want_words else []
    if timestamps_granularity == "none":
        words = []
    body = {
        "language_code": result.get("language") or language_code or "",
        "language_probability": 1.0 if language_code else 0.0,
        "text": result.get("text", ""),
        "words": words,
    }
    if error:
        logger.info("ElevenLabs speech-to-text: speaker identification unavailable (%s)", error)
        return JSONResponse(body, headers={"X-OmniVoice-Diarization-Error": error})
    return body


# ── Files produced by the app's own handlers ────────────────────────────────


async def _audio_url_response(result: dict, output_format: str, headers: Optional[dict] = None):
    """Return the take an app handler saved (``{"audio_url": "/audio/<name>"}``)
    in ``output_format``."""
    import asyncio

    import soundfile as sf

    from core.config import OUTPUTS_DIR
    from core.path_security import contained_join

    name = str(result.get("audio_url") or "").rsplit("/", 1)[-1]
    path = contained_join(OUTPUTS_DIR, name)
    data, sr = await asyncio.to_thread(sf.read, path, dtype="float32", always_2d=True)
    return await _tensor_response(data, sr, output_format, headers)


async def _tensor_response(data, sr: int, output_format: str, headers: Optional[dict] = None):
    """``(frames, channels)`` float audio → ``output_format``, at exactly the
    rate and bitrate it names."""
    import asyncio

    import torch

    from api.routers.openai_compat import _pcm24k

    family, rate, bitrate = output_spec(output_format)
    wav = torch.from_numpy(data.T.copy())
    if family in ("pcm", "ulaw", "alaw"):
        pcm = await asyncio.to_thread(_pcm24k, wav, sr)
        body = await asyncio.to_thread(convert_pcm24k, pcm, family, rate)
    elif family == "wav":
        body = await asyncio.to_thread(_wav_bytes, wav, sr, rate)
    else:
        body = await _ffmpeg_encode(wav, sr, family, rate, bitrate)
    return Response(body, media_type=_MEDIA[family], headers=headers)


def _wav_bytes(wav, sr: int, rate: int) -> bytes:
    import torchaudio.functional as AF

    from services.audio_io import _safe_soundfile_write

    if sr != rate:
        wav = AF.resample(wav, sr, rate)
    buf = io.BytesIO()
    _safe_soundfile_write(buf, wav.clamp(-1.0, 1.0).T.numpy(), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def _ffmpeg_encode(wav, sr: int, family: str, rate: int, bitrate: int) -> bytes:
    import asyncio
    import os
    import tempfile

    from api.routers.openai_compat import _ffmpeg_or_error
    from services.ffmpeg_utils import run_ffmpeg

    ffmpeg = await asyncio.to_thread(_ffmpeg_or_error, family)
    codec, container = ("libmp3lame", "mp3") if family == "mp3" else ("libopus", "ogg")
    fd, src = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        await asyncio.to_thread(_write_wav, src, wav, sr)
        rc, out, err = await run_ffmpeg(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", src,
             "-ar", str(rate), "-c:a", codec, "-b:a", f"{bitrate}k", "-f", container, "pipe:1"],
            timeout=300.0,
        )
    finally:
        _unlink(src)
    if rc != 0 or not out:
        raise _error(500, "encoding_failed",
                     f"Encoding the audio as {family} failed. Request a pcm_ or wav_ output_format instead.")
    return out


def _write_wav(path: str, wav, sr: int) -> None:
    from services.audio_io import _safe_soundfile_write

    _safe_soundfile_write(path, wav.clamp(-1.0, 1.0).T.numpy(), sr, format="WAV", subtype="FLOAT")


async def _ffmpeg_ready(output_format: str) -> None:
    """Fail before any model work when the format needs a missing ffmpeg."""
    import asyncio

    from api.routers.openai_compat import _ffmpeg_or_error

    family, _, _ = output_spec(output_format)
    if family in ("mp3", "opus"):
        await asyncio.to_thread(_ffmpeg_or_error, family)


# ── Voice changer ───────────────────────────────────────────────────────────


@router.post("/v1/speech-to-speech/{voice_id}", response_class=Response, responses=AUDIO_RESPONSES)
async def speech_to_speech(
    voice_id: str,
    audio: UploadFile = File(...),
    model_id: Optional[str] = Form(default=None, description="Accepted; the app picks its voice-conversion engine."),
    voice_settings: Optional[str] = Form(default=None, description="Accepted without effect."),
    seed: Optional[int] = Form(default=None, description="Accepted without effect."),
    remove_background_noise: bool = Form(default=False, description="Accepted without effect."),
    file_format: Optional[str] = Form(default=None),
    output_format: str = Query(default="mp3_44100_128"),
):
    """Say the uploaded speech in a voice profile's voice, keeping its timing
    and intonation (the app's Convert → Keep my delivery). Without a
    voice-conversion engine it falls back to transcribing and re-speaking the
    clip, and says so in ``X-OmniVoice-Fallback``."""
    from api.routers.voice_convert import convert_speech

    await _ffmpeg_ready(output_format)
    if not _profile_exists(voice_id):
        raise _error(
            404, "voice_not_found",
            f"Voice '{voice_id}' is not a VoiceStudio voice profile. List them with "
            f"GET {ELEVENLABS_PREFIX}/v1/voices.",
        )
    from api.routers.openai_compat import _resolve_voice_profile

    profile_id = _resolve_voice_profile(voice_id)["id"]
    headers = None
    try:
        result = await convert_speech(audio=audio, profile_id=profile_id, match_duration=True,
                                      language="", keep_delivery=True)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("error") != "voice_conversion_engine_missing":
            raise
        logger.info("No voice-conversion engine; re-speaking the clip for the ElevenLabs voice changer")
        await audio.seek(0)
        result = await convert_speech(audio=audio, profile_id=profile_id, match_duration=True,
                                      language="", keep_delivery=False)
        headers = {"X-OmniVoice-Fallback": "respoken"}
    return await _audio_url_response(result, output_format, headers)


# ── Sound effects ───────────────────────────────────────────────────────────


class SoundGenerationRequest(BaseModel):
    """``prompt_influence``, ``loop`` and ``model_id`` have no equivalent in
    the app's sound-effect engine and are accepted without effect."""

    text: str = Field(..., min_length=1)
    duration_seconds: Optional[float] = Field(default=None, gt=0)
    prompt_influence: Optional[float] = Field(default=None, ge=0, le=1)
    loop: Optional[bool] = None
    model_id: Optional[str] = None

    model_config = {"extra": "allow"}


@router.post("/v1/sound-generation", response_class=Response, responses=AUDIO_RESPONSES)
async def sound_generation(
    body: SoundGenerationRequest,
    output_format: str = Query(default="mp3_44100_128"),
):
    """A sound effect from a description, on the app's Sound effects engine
    (Tools → Sound effects must be installed)."""
    from api.routers.sfx import SfxRequest, _limits, sfx_generate

    await _ffmpeg_ready(output_format)
    seconds = body.duration_seconds
    if seconds is None:
        seconds = _limits()["default_seconds"]
    result = await sfx_generate(SfxRequest(prompt=body.text, duration=seconds))
    return await _audio_url_response(result, output_format)


# ── Voice isolation ─────────────────────────────────────────────────────────


#: The htdemucs checkpoint Demucs fetches into the torch hub cache on first use
#: (demucs/remote/htdemucs.yaml → files.txt).
HTDEMUCS_CHECKPOINT = "955717e8-8726e21a.th"


def isolation_weights_cached() -> bool:
    import os

    import torch

    return os.path.isfile(os.path.join(torch.hub.get_dir(), "checkpoints", HTDEMUCS_CHECKPOINT))


@router.post("/v1/audio-isolation", response_class=Response, responses=AUDIO_RESPONSES)
async def audio_isolation(
    audio: UploadFile = File(...),
    file_format: Optional[str] = Form(default=None, description="Accepted; the upload's format is detected."),
    output_format: str = Query(default="mp3_44100_128"),
):
    """The voice from a recording with music and background noise removed
    (Demucs, as Dubbing uses)."""
    import asyncio
    import shutil
    import tempfile

    import soundfile as sf

    from services.video_voice_swap import separate_vocals, separation_available

    await _ffmpeg_ready(output_format)
    if not separation_available():
        raise _error(
            409, "audio_isolation_unavailable",
            "Voice isolation needs Demucs, which is missing from this install. "
            "Repair the install from Settings, then try again.",
        )
    if not await asyncio.to_thread(isolation_weights_cached):
        # An API request never starts a model download.
        raise _error(
            409, "audio_isolation_model_missing",
            "Voice isolation's model is not downloaded yet. Separate vocals once in "
            "the app (Dubbing or Clean audio) to download it, then try again.",
        )
    source = await _save_upload(audio)
    workdir = None
    try:
        workdir = tempfile.mkdtemp(prefix="el-isolate-")
        stems = await separate_vocals(source, workdir)
        if stems is None:
            raise _error(
                500, "audio_isolation_failed",
                "Voice isolation failed on this file. Check that it is playable audio and try again.",
            )
        data, sr = await asyncio.to_thread(sf.read, stems[0], dtype="float32", always_2d=True)
    finally:
        _unlink(source)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return await _tensor_response(data, sr, output_format)
