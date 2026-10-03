"""
Tools router — Phase 4.6 (ROADMAP.md).

Standalone utilities exposed as first-class endpoints, independent of the
dub pipeline. The Tools page UI consumes these. Headless CLI consumers
(omnivoice-dub) will share the same service layer.

Shipped today:

    POST /tools/probe       → ffprobe-style metadata for a file path.
    POST /tools/incremental → plan what segments need regenerating.
    POST /tools/direction   → parse a natural-language direction into tokens.
    POST /tools/rate-fit    → LLM-assisted slot-fit for translated text.

More utilities (vocal separation, alignment, merge) are wired through
existing dub helpers and land in follow-up passes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from services import director, speech_rate, incremental
from services.ffmpeg_utils import find_ffprobe, spawn_subprocess
from api.dependencies import require_native_access
from core.path_security import UnsafePath, resolve_within

logger = logging.getLogger("omnivoice.tools")
router = APIRouter()


# ── Probe (ffprobe wrapper) ────────────────────────────────────────────────


class ProbeReq(BaseModel):
    path: str


@router.post("/tools/probe", dependencies=[Depends(require_native_access)])
async def probe(req: ProbeReq):
    target = os.path.realpath(os.path.expanduser(req.path))
    if not os.path.exists(target):
        raise HTTPException(
            status_code=404,
            detail="File not found. Provide an absolute path to an existing file.",
        )
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise HTTPException(
            status_code=501,
            detail="ffprobe binary not available. Install system ffmpeg or re-run the setup.",
        )
    proc = await spawn_subprocess(
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", target,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"ffprobe failed: {stderr.decode(errors='replace')[:400]}",
        )
    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": stdout.decode("utf-8", errors="replace")}


# ── Incremental plan (what needs regenerating) ─────────────────────────────


class IncrementalReq(BaseModel):
    segments: list[dict]
    stored_hashes: Optional[dict[str, str]] = None
    # P1.3 — the ACTIVE track's language code. When set, fingerprints are
    # scoped to that language (pass that language's stored hashes alongside);
    # omitted → legacy language-agnostic hashing, kept for old callers.
    lang: Optional[str] = None
    # Voice-identity mode the client will generate with (DubRequest.voice_match).
    # Only "consistent" changes the hash (per_line/omitted == legacy), so
    # flipping the Voice-match toggle marks every segment stale — the audio
    # really would come out with a different reference (#281 class).
    voice_match: Optional[str] = None


@router.post("/tools/incremental")
def plan_incremental(req: IncrementalReq):
    return incremental.plan_incremental(
        req.segments,
        stored_hashes=req.stored_hashes or {},
        track_lang=req.lang,
        voice_match=req.voice_match,
    )


# ── Directorial AI parse ───────────────────────────────────────────────────


class DirectionReq(BaseModel):
    text: str = Field(..., description="Natural-language direction, e.g. 'urgent and surprised'")


@router.post("/tools/direction")
def parse_direction(req: DirectionReq):
    d = director.parse(req.text)
    return {
        "tokens":          d.tokens,
        "instruct_prompt": d.instruct_prompt(),
        "translate_hint":  d.translate_hint(),
        "rate_bias":       d.rate_bias(),
        "method":          d.method,
        "error":           d.error,
        "taxonomy":        director.TAXONOMY,
    }


# ── Speech-rate fit ────────────────────────────────────────────────────────


class RateFitReq(BaseModel):
    text: str
    slot_seconds: float
    target_lang: str
    source_text: Optional[str] = None


@router.post("/tools/rate-fit")
def rate_fit(req: RateFitReq):
    return speech_rate.adjust_for_slot(
        req.text,
        slot_seconds=req.slot_seconds,
        target_lang=req.target_lang,
        source_text=req.source_text,
    )


# ── Audio effects presets ──────────────────────────────────────────────────


@router.get("/tools/effects")
def list_effects():
    """Return available audio effect presets (Broadcast, Cinematic, etc.)."""
    from services.audio_dsp import list_effect_presets
    return list_effect_presets()


# ── TTS Plugin SDK ─────────────────────────────────────────────────────────


@router.get("/tools/plugins")
def list_tts_plugins():
    """Return all registered TTS engine plugins and their availability."""
    from services.plugin_sdk import list_plugins
    return list_plugins()


# ── Video context analysis ─────────────────────────────────────────────────


@router.post("/tools/video-context/{job_id}")
async def analyse_video_context(job_id: str):
    """Analyse the source video's visual context for dubbing decisions.

    Returns per-segment mood, brightness, and complexity cues that
    can be used as TTS instruct hints.
    """
    import os
    from api.routers.dub_core import _get_job
    from core.config import DUB_DIR
    from services.video_context import analyse_video

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="Invalid job id")
    try:
        job_dir = resolve_within(DUB_DIR, job_id)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc
    job = await asyncio.to_thread(_get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    video_path = resolve_within(DUB_DIR, job_dir / "source.mp4")
    if not video_path.is_file():
        try:
            video_path = resolve_within(DUB_DIR, job.get("video_path", ""))
        except UnsafePath:
            return {"error": "Source video not found", "segments": {}}

    if not video_path.is_file():
        return {"error": "Source video not found", "segments": {}}

    segments = job.get("segments") or []
    ctx = await analyse_video(str(video_path), segments)
    return ctx.to_dict()


# Workflow audio is already synthetic. Re-mark after processing through the
# existing chokepoint rather than routing it through human mic cleanup.
def _decode_workflow_audio(data: bytes):
    import io
    import numpy as np
    import soundfile as sf
    import torch

    try:
        with sf.SoundFile(io.BytesIO(data)) as source:
            if source.format != "WAV" or source.channels not in (1, 2):
                raise ValueError("Expected mono or stereo WAV")
            if source.frames < 1 or source.frames * source.channels > 16_000_000:
                raise ValueError("Audio exceeds the processing limit")
            audio = source.read(dtype="float32", always_2d=True)
            rate = source.samplerate
        if not np.isfinite(audio).all():
            raise ValueError("Invalid audio samples")
        return torch.from_numpy(audio.T.copy()), rate
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail="Provide a valid WAV within the audio size limit.") from exc


def _encode_workflow_audio(audio, rate: int) -> bytes:
    import io
    from services.audio_io import _safe_soundfile_write

    output = io.BytesIO()
    _safe_soundfile_write(output, audio.detach().cpu().numpy().T, rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


@router.post("/tools/normalize-speech")
async def normalize_workflow_speech(
    audio: UploadFile = File(...),
    target_dbfs: float = Form(-2.0, ge=-24.0, le=-1.0),
):
    """Peak-normalize synthetic speech; bounded and entirely local."""
    from services.audio_dsp import normalize_audio
    from services.watermark import mark_synthetic_async

    data = await audio.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio exceeds 64 MiB.")
    waveform, rate = await run_in_threadpool(_decode_workflow_audio, data)
    waveform = await run_in_threadpool(normalize_audio, waveform, target_dBFS=target_dbfs)
    waveform = await mark_synthetic_async(waveform, rate, context="workflow.normalize")
    encoded = await run_in_threadpool(_encode_workflow_audio, waveform, rate)
    return Response(encoded, media_type="audio/wav")
