import os
import uuid
import asyncio
import logging
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import soundfile as sf
import torch
from typing import Optional
from fastapi import Request
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from core.db import db_conn
from core.config import PREVIEW_DIR
from core.tasks import task_manager
from core.logging_utils import log_safe
from core import event_bus
from schemas.requests import DubIngestUrlRequest, ParseSubtitleTextRequest
from services.srt_parser import CUE_SOURCE_FIELDS, CUE_SOURCE_ID
from services.model_manager import get_model, _gpu_pool, _cpu_pool, get_diarization_pipeline, offload_tts_for_asr, restore_tts_after_asr, should_preload_tts_asr, release_device_cache
from services.asr_backend import (
    ASR_TRANSCRIBE_TIMEOUT_S,
    ASRTimeoutError,
    reset_pool_after_wedge,
    run_transcribe_guarded,
)
from services.audio_io import _safe_soundfile_write
from services.ffmpeg_utils import find_ffmpeg
from services.segmentation import (
    segment_transcript,
    assign_speakers_from_diarization,
    assign_speakers_from_turns,
    assign_speakers_heuristic,
    resplit_segments_by_diarization,
    resplit_segments_by_turns,
    _words_from_whisper,
    clean_up_segments,
)
from services.onset_align import snap_segment_starts
from services import dub_pipeline

router = APIRouter()
logger = logging.getLogger("omnivoice.api")

_MAX_COOKIE_EXPORT_BYTES = 1024 * 1024


def _cookie_transport_allowed(
    scheme: str, client_host: str | None, origin: str | None
) -> bool:
    """Credentials may cross HTTP only from a local UI to a loopback peer."""
    from api.dependencies import is_local_host

    if scheme == "https":
        return True
    try:
        origin_host = urlsplit(origin or "").hostname or ""
    except ValueError:
        return False
    return is_local_host(client_host or "") and (
        is_local_host(origin_host) or origin_host == "tauri.localhost"
    )


def _stage_cookie_export(contents: str | None) -> str | None:
    """Write an explicitly supplied cookies.txt export to a private temp file."""
    if contents is None:
        return None
    cookie_bytes = contents.encode("utf-8")
    if len(cookie_bytes) > _MAX_COOKIE_EXPORT_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cookie file is too large (maximum 1 MB). Export cookies in "
                "Netscape cookies.txt format and try again."
            ),
        )
    first_line = contents.lstrip("\ufeff\r\n ").splitlines()[0] if contents.strip() else ""
    if not first_line.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is not a Netscape cookies.txt export. Export cookies as "
                "cookies.txt from your browser, then choose that file."
            ),
        )
    fd, cookie_path = tempfile.mkstemp(
        prefix="voicestudio-ytdlp-", suffix=".cookies.txt",
    )
    try:
        os.chmod(cookie_path, 0o600)
        with os.fdopen(fd, "wb") as cookie_handle:
            cookie_handle.write(cookie_bytes)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass  # Best effort: fdopen may already have consumed/closed the descriptor.
        try:
            os.unlink(cookie_path)
        except OSError:
            pass  # Best effort: preserve the original staging error.
        raise
    return cookie_path


# ── Legacy-name aliases to services/dub_pipeline.py ────────────────────────
# Phase 2.4 moved the business logic into a service. Other routers
# (dub_generate, dub_translate, dub_export) + internal call sites below still
# reference the `_get_job` / `_save_job` / `_active_procs` names; those
# aliases let the transition happen without a repo-wide rename pass.
#
# New code should import from `services.dub_pipeline` directly. Aliases can
# disappear once every caller updates.
_dub_jobs           = dub_pipeline._dub_jobs
_active_procs       = dub_pipeline._active_procs
_active_procs_lock  = dub_pipeline._active_procs_lock
_DUB_DIR_REAL       = dub_pipeline._DUB_DIR_REAL

_compute_file_hash = dub_pipeline.compute_file_hash
_find_cached_job   = dub_pipeline.find_cached_job
_safe_job_dir      = dub_pipeline.safe_job_dir
_register_proc     = dub_pipeline.register_proc
_unregister_proc   = dub_pipeline.unregister_proc
_kill_job_procs    = dub_pipeline.kill_job_procs
_get_job           = dub_pipeline.get_job
_save_job          = dub_pipeline.save_job

# Pasted subtitle text is a transcript, not a media file: a feature-length
# film's .srt is ~150 KB. 2 MB of characters is ~13x the worst realistic case
# and still cheap to regex — past that we refuse rather than let a stray
# paste (or a mis-aimed binary) burn CPU in the parser.
_MAX_SUBTITLE_PASTE_CHARS = 2_000_000

# The imported cue syntax belongs to the new cue. A prior segment's would
# restore stale markup on an unchanged export; dropping the new one loses it.
_SRT_REPLACED_FIELDS = {
    "id",
    "start",
    "end",
    "text",
    "text_original",
    "translations",
    "translate_error",
    "translate_degraded",
    *CUE_SOURCE_FIELDS,
    CUE_SOURCE_ID,
}


def _best_overlapping_segment(cue: dict, existing: list[dict]) -> dict | None:
    """Return the prior segment with the strongest temporal overlap."""
    cue_start = float(cue.get("start") or 0.0)
    cue_end = float(cue.get("end") or cue_start)
    cue_mid = (cue_start + cue_end) / 2.0
    best = None
    best_key = None
    for index, segment in enumerate(existing):
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        overlap = min(cue_end, end) - max(cue_start, start)
        if overlap <= 0:
            continue
        midpoint_distance = abs(cue_mid - ((start + end) / 2.0))
        key = (overlap, -midpoint_distance, -index)
        if best_key is None or key > best_key:
            best = segment
            best_key = key
    return best


def _carry_srt_voice_metadata(
    cues: list[dict],
    existing: list[dict],
    segment_clones: dict | None,
    speaker_clones: dict | None = None,
) -> tuple[list[dict], dict]:
    """Replace subtitle content while retaining the source cast assignment."""
    source_clones = dict(segment_clones or {})
    source_speaker_clones = dict(speaker_clones or {})
    # Replacement cues get new positional ids. Starting from the old map would
    # let an unmatched cue whose new id happens to equal an old id inherit an
    # unrelated reference. Only explicitly overlap-matched references survive.
    clones = {}
    merged_segments = []
    for new_id, cue in enumerate(cues):
        prior = _best_overlapping_segment(cue, existing)
        metadata = {
            key: value
            for key, value in (prior or {}).items()
            if key not in _SRT_REPLACED_FIELDS
        }
        merged = {
            **metadata,
            "id": new_id,
            "start": cue.get("start", 0.0),
            "end": cue.get("end", 0.0),
            "text": cue.get("text", ""),
            "text_original": cue.get("text", ""),
            **{key: cue[key] for key in (*CUE_SOURCE_FIELDS, CUE_SOURCE_ID) if key in cue},
        }
        if not merged.get("speaker_id"):
            merged["speaker_id"] = cue.get("speaker_id") or "Speaker 1"
        if prior is not None:
            prior_id = str(prior.get("id", ""))
            clone = source_clones.get(prior_id)
            if clone is None:
                clone = source_speaker_clones.get(prior.get("speaker_id"))
            if clone is not None:
                clones[str(new_id)] = clone
                if merged.get("profile_id") == f"auto-seg:{prior_id}":
                    merged["profile_id"] = f"auto-seg:{new_id}"
        merged_segments.append(merged)
    return merged_segments, clones


@router.post("/dub/parse-subtitle-text")
def dub_parse_subtitle_text(req: ParseSubtitleTextRequest):
    """Parse pasted subtitle text into timed cues. Stateless — no job, no I/O.

    A thin wrapper over `services.srt_parser.parse_srt` so the client's
    "paste a translation" flow reuses the exact lenient parser the .srt
    import path uses (BOM / CRLF / `.`-vs-`,` ms / missing indices, plus
    de-overlapping). Unlike `/dub/import-srt/{job_id}` this mutates
    nothing: the caller maps these cues onto the segments it already has,
    keeping the existing timings and `text_original`.
    """
    text = req.text or ""
    if len(text) > _MAX_SUBTITLE_PASTE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Pasted text is too large ({len(text)} characters). "
                f"Limit is {_MAX_SUBTITLE_PASTE_CHARS} characters."
            ),
        )

    from services.srt_parser import parse_srt
    result = parse_srt(text)
    if not result.segments:
        raise HTTPException(
            status_code=400,
            detail=(
                "No timed cues found in the pasted text. "
                f"Skipped {result.skipped_cues} malformed cue(s). "
                "Expected timestamp lines like '00:00:01,000 --> 00:00:04,500'."
            ),
        )
    return {
        "segments": [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result.segments
        ],
        "skipped_cues": result.skipped_cues,
        "dropped_overlaps": result.dropped_overlaps,
    }


@router.post("/dub/import-srt/{job_id}")
async def dub_import_srt(job_id: str, file: UploadFile = File(...)):
    """Replace `job["segments"]` with timestamps + text parsed from an SRT
    file. Used as a fallback when Whisper mis-transcribes — the user can
    point at their own pre-synced subtitles and skip ASR entirely.

    Returns the new segment list plus counts of any cues we had to skip or
    re-time (overlap shifts). The caller surfaces these so the user knows
    if the import wasn't lossless.
    """
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        raw_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file: {e}") from e
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded SRT file is empty.")
    # Most SRT files are UTF-8, but Windows subtitle tools also save UTF-16
    # (with a BOM) and Windows-1252; decode those instead of finding no cues.
    from services.text_upload import decode_text_upload
    text = decode_text_upload(raw_bytes)

    from services.srt_parser import parse_srt
    result = parse_srt(text)
    if not result.segments:
        raise HTTPException(
            status_code=400,
            detail=(
                "No valid cues found in the uploaded file. "
                f"Skipped {result.skipped_cues} malformed cue(s). "
                "Expected SubRip (.srt) format: index, then 'HH:MM:SS,ms --> HH:MM:SS,ms', then text, blank line."
            ),
        )

    # Clamp cues that run past the source media's known duration. Pipeline
    # downstream code assumes segment.end <= duration; without this, dub
    # generation would try to time-stretch into negative slack.
    duration = float(job.get("duration") or 0.0)
    clamped = 0
    if duration > 0:
        kept = []
        for seg in result.segments:
            if seg["start"] >= duration:
                continue
            if seg["end"] > duration:
                seg = {**seg, "end": round(duration, 3)}
                clamped += 1
            kept.append(seg)
        # Re-id after clamp drops.
        segments = [{**s, "id": i} for i, s in enumerate(kept)]
    else:
        segments = result.segments

    prior_segments = [
        segment for segment in (job.get("segments") or []) if isinstance(segment, dict)
    ]
    segments, segment_clones = _carry_srt_voice_metadata(
        segments,
        prior_segments,
        job.get("segment_clones"),
        job.get("speaker_clones"),
    )
    job["segments"] = segments
    job["segment_clones"] = segment_clones
    # A pooled speaker clone is keyed only by a display label. Replacement
    # cues can reuse that label without overlapping the original speaker, so
    # retain matched pooled references as segment-specific clones above and
    # drop the global map before rebuilding the cast.
    job["speaker_clones"] = {}
    if segment_clones:
        from services.speaker_clone import build_cast_sources

        job["cast_sources"] = build_cast_sources(
            segments,
            None,
            segment_clones,
        )
    else:
        job.pop("cast_sources", None)
    # `source_lang` stays whatever the user (or the upload step) set; we
    # don't try to language-detect off the cue text — that's noisy and the
    # user usually knows what their .srt is.
    _save_job(job_id, job)
    logger.info(
        "Imported %d cue(s) from .srt for job %s (skipped=%d, overlap_shifted=%d, clamped=%d)",
        len(segments), log_safe(job_id), result.skipped_cues, result.dropped_overlaps, clamped,
    )
    return {
        "segments": segments,
        "stats": {
            "imported": len(segments),
            "skipped_malformed": result.skipped_cues,
            "dropped_overlap": result.dropped_overlaps,
            "clamped_to_duration": clamped,
        },
    }


def _select_downloaded_caption_track(
    tracks: dict[str, list[dict]], preferred: str | None,
) -> str | None:
    """Choose the closest original-language caption track deterministically."""
    available = [key for key, cues in tracks.items() if isinstance(cues, list) and cues]
    if not available:
        return None
    preferred_tag = (preferred or "").strip().lower().replace("_", "-")
    preferred_base = preferred_tag.split("-", 1)[0]

    def rank(key: str) -> tuple[int, int, int, str]:
        tag = key.strip().lower().replace("_", "-")
        base = tag.split("-", 1)[0]
        if preferred_tag:
            language_rank = 0 if tag == preferred_tag else 1 if base == preferred_base else 2
        else:
            language_rank = 0
        return (
            language_rank,
            0 if tag.endswith("-orig") else 1,
            0 if "-" not in tag else 1,
            tag,
        )

    return min(available, key=rank)


def _prepare_downloaded_caption_segments(cues: list[dict], duration: float) -> list[dict]:
    """Normalize downloaded VTT cues into safe, sequential Dub segments."""
    def cue_start(cue: dict) -> float:
        try:
            return float(cue.get("start") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def remove_repeated_prefix(previous: str, current: str, whole: bool = False) -> str:
        previous_words = previous.split()
        current_words = current.split()
        folded_previous = [word.casefold() for word in previous_words]
        folded_current = [word.casefold() for word in current_words]
        shortest = len(previous_words) if whole else 1
        for count in range(min(len(previous_words), len(current_words)), shortest - 1, -1):
            if folded_previous[-count:] == folded_current[:count]:
                return " ".join(current_words[count:])
        return current

    prepared: list[dict] = []
    previous_end = 0.0
    rollup_bridge_end: float | None = None
    ordered = sorted((cue for cue in cues if isinstance(cue, dict)), key=cue_start)
    for index, cue in enumerate(ordered):
        try:
            raw_start = max(0.0, float(cue.get("start") or 0.0))
            end = float(cue.get("end") or raw_start)
        except (TypeError, ValueError):
            continue
        text = " ".join(str(cue.get("text") or "").split())
        if duration > 0:
            if raw_start >= duration:
                continue
            end = min(end, duration)
        # YouTube roll-ups insert a tiny repeated cue between spoken lines.
        # Touching timestamps plus equal text alone are not evidence of a
        # roll-up: a speaker can deliberately repeat a word or entire phrase.
        # Only trim at that boundary for the bridge itself or its next cue.
        touching = raw_start == previous_end
        short_bridge = 0 < end - raw_start <= 0.05
        follows_bridge = rollup_bridge_end == raw_start
        rollup_bridge_end = None
        if prepared and (
            raw_start < previous_end or (touching and (short_bridge or follows_bridge))
        ):
            text = remove_repeated_prefix(prepared[-1]["text"], text, whole=touching)
            if not text:
                prepared[-1]["end"] = round(max(previous_end, end), 3)
                previous_end = max(previous_end, end)
                if touching and short_bridge:
                    rollup_bridge_end = previous_end
                continue
        # Caption hosts commonly emit slightly overlapping cues. Dubbing needs
        # a monotonic timeline, so trim the later cue rather than manufacture
        # overlapping speech slots.
        start = max(raw_start, previous_end)
        if not text or end <= start:
            continue
        prepared.append({
            "id": str(index),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "speaker_id": "Speaker 1",
        })
        previous_end = end

    cleaned = clean_up_segments(prepared)
    return [
        {
            **segment,
            "id": index,
            "text_original": segment.get("text", ""),
        }
        for index, segment in enumerate(cleaned)
    ]


@router.post("/dub/use-downloaded-captions/{job_id}")
def dub_use_downloaded_captions(job_id: str):
    """Seed a prepared Dub job from its downloaded caption track."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tracks = job.get("youtube_subs")
    if not isinstance(tracks, dict):
        raise HTTPException(status_code=404, detail="No downloaded captions are available")
    caption_lang = _select_downloaded_caption_track(
        tracks,
        job.get("source_lang_override") or job.get("source_lang"),
    )
    if caption_lang is None:
        raise HTTPException(status_code=404, detail="No downloaded captions are available")
    segments = _prepare_downloaded_caption_segments(
        tracks[caption_lang],
        float(job.get("duration") or 0.0),
    )
    if not segments:
        raise HTTPException(status_code=422, detail="Downloaded captions contain no usable cues")

    source_lang = job.get("source_lang_override") or _detected_source_lang(caption_lang)
    job["segments"] = segments
    job["source_lang"] = source_lang
    job["full_transcript"] = " ".join(segment["text"] for segment in segments)
    # Caption files contain timing and text, but no trustworthy speaker or
    # reference-audio attribution. Never retain stale clone maps from a prior
    # transcript on the same job.
    job["segment_clones"] = {}
    job["speaker_clones"] = {}
    job.pop("cast_sources", None)
    _save_job(job_id, job)
    return {
        "segments": segments,
        "source_lang": source_lang,
        "caption_lang": caption_lang,
        "available": sorted(tracks.keys()),
    }


@router.post("/dub/cleanup-segments/{job_id}")
def dub_cleanup_segments(job_id: str):
    """Re-run merge/stitch passes on a job's existing segments to drop fragments."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    segments = job.get("segments") or []
    cleaned = clean_up_segments(segments)
    job["segments"] = cleaned
    _save_job(job_id, job)
    return {"segments": cleaned, "before": len(segments), "after": len(cleaned)}


@router.post("/dub/abort/{job_id}")
def dub_abort(job_id: str):
    """Cancel in-flight upload/transcribe subprocesses for a job."""
    with _active_procs_lock:
        had_procs = bool(_active_procs.get(job_id))
    _kill_job_procs(job_id)
    try:
        had_task = task_manager.cancel_task(job_id)
    except Exception as exc:
        logger.warning("Dub task cancellation failed")
        raise HTTPException(
            status_code=503,
            detail="The dub could not be fully aborted. Retry the abort operation.",
        ) from exc
    job = _dub_jobs.get(job_id)
    if job is not None:
        job["aborted"] = True
    # Cancellation is idempotent: a missing active task means it already
    # stopped between the renderer aborting its stream and this request.
    return {
        "aborted": True,
        "had_active_procs": had_procs,
        "had_active_task": had_task,
    }


@router.get("/dub/history")
def list_dub_history():
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM dub_history ORDER BY created_at DESC LIMIT 30").fetchall()
    return [dict(r) for r in rows]

@router.delete("/dub/history")
def clear_dub_history():
    """Delete persisted dub rows and their on-disk dirs (scoped to known IDs)."""
    with db_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM dub_history").fetchall()]

    def _delete_rows():
        with db_conn() as conn:
            conn.execute("DELETE FROM dub_history")

    # Row-delete + in-memory evict together, so an ingest finishing right now
    # can't re-save a job the user just cleared (#1252 review). This path
    # never evicted from memory at all before, so an in-flight job survived
    # "clear history" outright.
    dub_pipeline.purge_jobs(ids, delete_rows=_delete_rows, include_inflight=True)
    for jid in ids:
        safe = _safe_job_dir(jid)
        if safe and os.path.isdir(safe):
            shutil.rmtree(safe, ignore_errors=True)
    event_bus.emit("dub_history")
    return {"cleared": True, "count": len(ids)}

@router.delete("/dub/history/{history_id}")
def delete_single_dub_history(history_id: str):
    def _delete_row():
        with db_conn() as conn:
            conn.execute("DELETE FROM dub_history WHERE id=?", (history_id,))

    # #1331 (deletion half): the content-hash cache points newer jobs' paths
    # (vocals, and pre-fix clone refs) into this dir. Check BEFORE the row is
    # deleted — the scan reads dub_history, and after _delete_row this row's
    # neighbours are all that's left to consult either way.
    holders = dub_pipeline.job_dir_referenced_by_others(history_id)

    # Atomic with the evict — see purge_jobs (#1252 review).
    dub_pipeline.purge_jobs([history_id], delete_rows=_delete_row)
    safe = _safe_job_dir(history_id)
    if holders:
        # Keep the directory: another saved dub still renders from files in
        # it. Disk is the cheap thing here; a job that silently loses its
        # cloned voice on every regen is not. The row is gone, so the entry
        # disappears from history either way.
        logger.info(
            "dub delete %s: history row removed but directory kept — still "
            "referenced by job(s) %s (#1331)", log_safe(history_id), log_safe(", ".join(holders)),
        )
    elif safe and os.path.isdir(safe):
        shutil.rmtree(safe, ignore_errors=True)
    event_bus.emit("dub_history", {"action": "deleted", "id": history_id})
    return {"deleted": True, "dir_kept_for": holders}

@router.post("/preview/upload")
async def preview_upload(video: UploadFile = File(...)):
    ext = os.path.splitext(video.filename or "video.mp4")[1].lower()
    safe_name = f"{uuid.uuid4().hex[:12]}"
    vid_path = os.path.join(PREVIEW_DIR, f"{safe_name}{ext}")
    wav_path = os.path.join(PREVIEW_DIR, f"{safe_name}.wav")
    payload = await video.read()

    def _write_and_extract() -> bool:
        with open(vid_path, "wb") as f:
            f.write(payload)
        if ext in {".wav", ".mp3", ".m4a", ".aac"}:
            return False
        try:
            ffmpeg_cmd = [
                find_ffmpeg(), "-y", "-i", vid_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                wav_path
            ]
            subprocess.run(
                ffmpeg_cmd, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300,
            )
            return True
        except Exception as e:
            logger.warning("FFmpeg extraction failed: %s", log_safe(e))
            return False

    # File writes and ffmpeg are blocking operations. Keep them on the bounded
    # CPU pool so a large preview cannot stall unrelated API requests (#1667).
    has_audio = await asyncio.get_running_loop().run_in_executor(
        _cpu_pool, _write_and_extract
    )

    return {
        "url": f"/preview/{safe_name}{ext}",
        "audioUrl": f"/preview/{safe_name}.wav" if has_audio else f"/preview/{safe_name}{ext}",
        "filename": video.filename,
    }

@router.get("/preview/{filename}")
async def preview_serve(filename: str):
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "Invalid preview filename")
    preview_real = os.path.realpath(PREVIEW_DIR)
    path = os.path.realpath(os.path.join(PREVIEW_DIR, filename))
    if not path.startswith(preview_real + os.sep):
        raise HTTPException(400, "Invalid preview filename")
    if not os.path.isfile(path):
        raise HTTPException(404, "Preview not found")
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", 
        ".mkv": "video/x-matroska", ".webm": "video/webm", 
        ".avi": "video/x-msvideo", ".wav": "audio/wav", 
        ".mp3": "audio/mpeg"
    }
    return FileResponse(path, media_type=media_types.get(ext, "application/octet-stream"))

# ── Legacy aliases for the extracted ingest pipeline (Phase 2.4 finish) ────
_run_proc_factory = dub_pipeline.run_proc_factory
_yt_download_sync = dub_pipeline.yt_download_sync
_prep_event       = dub_pipeline.prep_event
_ingest_gen       = dub_pipeline.ingest_pipeline


#: Recognised audio extensions for audio-only dubbing (#119). When the client
#: declares input_type=audio we refuse anything that isn't a known audio
#: container so a mislabelled video can't slip past the video-skipping branch.
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

# Source-language choices exposed by the first-party dub UI, plus every
# language code Whisper can write back after auto-detection. A restored job
# may reuse that detected value as the next upload's override, so rejecting our
# own persisted codes strands otherwise valid dubbing sessions (#1737).
# Keeping this an allow-list still rejects language names and private-use
# BCP-47 tags. Values are normalized to lowercase below.
_DUB_SOURCE_LANG_CODES = frozenset({
    "af", "sq", "am", "ar", "hy", "az", "eu", "be", "bn", "bs", "bg",
    "my", "ca", "cmn-hans", "cmn-hant", "hr", "cs", "da", "nl", "en",
    "et", "fi", "fr", "gl", "ka", "de", "el", "gu", "ht", "ha", "haw",
    "he", "hi", "hu", "is", "id", "it", "ja", "jw", "kn", "kk", "km",
    "ko", "ku", "ky", "lo", "la", "lv", "lt", "mk", "ms", "ml", "mt",
    "mi", "mr", "mn", "ne", "no", "ps", "fa", "pl", "pt", "pa", "ro",
    "ru", "sm", "gd", "sr", "sn", "sd", "si", "sk", "sl", "so", "es",
    "su", "sw", "sv", "tg", "ta", "te", "th", "tr", "uk", "ur", "uz",
    "vi", "cy", "xh", "yi", "yo", "zu",
    "as", "ba", "bo", "br", "fo", "lb", "ln", "mg", "nn", "oc", "sa",
    "tk", "tl", "tt", "yue", "zh",
})


def _source_lang_override(value: str | None) -> str | None:
    """Normalize a user-selected source language; auto/und means detect.

    A rejection NAMES the code it rejected. "Invalid source language code" on
    its own cannot be acted on or reported usefully: it does not say which of
    the ninety-odd codes was wrong, so neither the user nor a maintainer
    reading the auto-filed issue can tell whether the picker offered something
    the backend does not accept, or a stale preference from an older build is
    still being sent (#1960).

    The value is a language code the user chose from a menu — not private
    data — and the neighbouring engine validator already echoes its input the
    same way.
    """
    code = (value or "").strip().lower()
    if code in {"", "auto", "und"}:
        return None
    if code not in _DUB_SOURCE_LANG_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid source language code: {code!r}. Pick a language from "
                "the Dubbing source-language menu, or leave it on auto-detect."
            ),
        )
    return code


def _detected_source_lang(value: str | None) -> str:
    """Normalize an ASR language without truncating valid three-letter codes."""
    code = (value or "en").split("_", 1)[0].strip().lower()
    if code in _DUB_SOURCE_LANG_CODES:
        return code
    short = code[:2]
    return short if short in _DUB_SOURCE_LANG_CODES else "en"


@router.post("/dub/upload")
async def dub_upload(
    video: UploadFile = File(...),
    job_id: Optional[str] = Form(None),
    input_type: str = Form("video"),
    source_lang: Optional[str] = Form(None),
):
    """Accept a media upload, write to disk, queue background prep task.

    `input_type` is "video" (default) or "audio". Audio-only jobs (#119) skip
    scene detection, thumbnailing, and the final video mux — the transcribe →
    translate → TTS core is identical.

    Returns 202 with {job_id, task_id, filename}. Client should open SSE on
    /tasks/stream/{task_id} to monitor extract/demucs stages and wait for the
    'ready' event before starting transcription.
    """
    input_type = (input_type or "video").lower()
    if input_type not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="input_type must be 'video' or 'audio'")

    job_id = job_id or str(uuid.uuid4())[:8]
    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid job_id. Must be alphanumeric + hyphens/underscores only, ≤64 chars. Generate a fresh job_id or omit it to auto-create one.",
        )
    ext = os.path.splitext(video.filename or "video.mp4")[1]
    if input_type == "audio" and ext.lower() not in _AUDIO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Audio-only dubbing needs an audio file ({', '.join(sorted(_AUDIO_EXTS))}); got '{ext or 'no extension'}'.",
        )

    source_lang_override = _source_lang_override(source_lang)
    os.makedirs(job_dir, exist_ok=True)

    video_path = os.path.join(job_dir, f"original{ext}")

    def _stream_upload_to_disk() -> None:
        # UploadFile is already a spooled file. Copy it in bounded chunks on a
        # worker thread instead of materialising a multi-GB video in RAM and
        # blocking every API request while the event loop writes it.
        video.file.seek(0)
        with open(video_path, "wb") as output:
            shutil.copyfileobj(video.file, output, length=1024 * 1024)

    try:
        await asyncio.to_thread(_stream_upload_to_disk)
    finally:
        await video.close()

    filename = video.filename or f"video{ext}"
    task_id = f"prep_{job_id}"
    await task_manager.add_task(
        task_id, "prep",
        _ingest_gen, job_id, job_dir,
        {
            "kind": "file",
            "path": video_path,
            "input_type": input_type,
            "source_lang": source_lang_override,
        },
        filename,
    )
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "task_id": task_id, "filename": filename},
    )


@router.post("/dub/ingest-url")
async def dub_ingest_url(req: DubIngestUrlRequest, request: Request):
    """Ingest a remote video URL via yt-dlp. Queues background prep task.

    Returns 202 immediately with {job_id, task_id}. All work (download,
    audio extract, Demucs, scene detect, thumbnail) happens in the background
    task and progress is streamed via /tasks/stream/{task_id}.
    """
    url = (req.url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://. Paste a full video link (e.g. https://youtube.com/watch?v=…) or drop a local file instead.",
        )
    source_lang_override = _source_lang_override(req.source_lang)

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="URL ingest needs yt-dlp, but it isn't installed. Install it (`pip install yt-dlp`) and restart the server — or drop a local video file instead.",
        )

    job_id = req.job_id or str(uuid.uuid4())[:8]
    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid job_id. Must be alphanumeric + hyphens/underscores only, ≤64 chars. Generate a fresh job_id or omit it to auto-create one.",
        )
    if req.cookie_file and not _cookie_transport_allowed(
        request.url.scheme,
        request.client.host if request.client else None,
        request.headers.get("origin"),
    ):
        raise HTTPException(
            status_code=403,
            detail="Cookie exports require HTTPS or the local desktop app.",
        )
    os.makedirs(job_dir, exist_ok=True)
    cookie_path = _stage_cookie_export(req.cookie_file)

    task_id = f"prep_{job_id}"
    source = {
        "kind": "url",
        "url": url,
        "fetch_subs": bool(req.fetch_subs),
        "sub_langs": req.sub_langs or None,
        "cookie_file": cookie_path,
        "source_lang": source_lang_override,
    }
    try:
        await task_manager.add_task(
            task_id, "prep",
            _ingest_gen, job_id, job_dir,
            source, None,
        )
    except Exception:
        if cookie_path:
            try:
                os.unlink(cookie_path)
            except OSError:
                pass  # Best effort: do not hide the task-enqueue failure.
        raise
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "task_id": task_id, "filename": ""},
    )


TRANSCRIBE_CHUNK_S = float(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_S", "30.0"))
TRANSCRIBE_CHUNK_TIMEOUT_S = float(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S", "120.0"))
#: How many times to attempt each transcribe chunk before giving up on it. A
#: transient wedge (esp. the first chunk, where whisperx cold-loads its model)
#: shouldn't silently drop that whole window — retry once on a fresh pool so the
#: transcript doesn't come back "missing the beginning".
_CHUNK_TRANSCRIBE_ATTEMPTS = max(1, int(os.environ.get("OMNIVOICE_TRANSCRIBE_CHUNK_ATTEMPTS", "2")))
#: Seconds between SSE keepalive comments while the transcribe preflight loads
#: the ASR backend (#1196). A first-run load can download multi-GB weights —
#: minutes with zero bytes on the wire — and byte-silent streams get severed
#: by Chrome's ~5 min no-response cap and by reverse-proxy idle timeouts,
#: which the UI can only report as the generic "stream dropped" guess.
ASR_LOAD_KEEPALIVE_S = float(os.environ.get("OMNIVOICE_ASR_LOAD_KEEPALIVE_S", "15.0"))
#: Seconds between `ping` events while a post-transcript step runs on an
#: executor (diarization, clone extraction, reference-text refinement, the
#: ASR unload / TTS restore). #2108: the per-segment refinement re-runs ASR
#: once per segment — 113 passes, ~19 min on an M1 Pro CPU — and was awaited
#: bare, so the stream went byte-silent for the whole stretch, the webview
#: severed it, and the UI could only say "stream ended early".
POST_ASR_PING_S = 5.0


_sse_event = dub_pipeline.sse_event


class _ASRWorkLifetime:
    """Keep model cleanup behind native work even after its waiter is cancelled."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._cleaned = False

    def run(self, fn):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("Transcription stream has ended")
            return fn()

    def stop(self):
        # Reject queued work before it can touch an unloaded model.
        self._closed.set()

    def cleanup(self, fn):
        self.stop()
        with self._lock:
            if self._cleaned:
                return
            # Claim cleanup before invoking even a non-idempotent unload.
            self._cleaned = True
            return fn()


async def _ping_while(fut):
    """Yield `ping` events every POST_ASR_PING_S until ``fut`` settles.

    Every await in the transcribe stream body that can outlast a few seconds
    goes through here so the connection never goes byte-silent. The result
    (or exception) stays on ``fut`` for the caller to read.

    Leaving early — the client disconnected, or the body raised at a `yield` —
    cancels ``fut`` exactly as a bare ``await fut`` would have, so a wrapped
    run_transcribe_guarded still runs its abandon path instead of refining on
    after disconnection. Native threads are not cancelled by Future.cancel();
    _ASRWorkLifetime orders model cleanup behind their actual completion. Nothing is awaited in the finally: it also runs under GeneratorExit.
    A failure that lands after we left is still marked retrieved, so it cannot
    surface later as "exception was never retrieved" (CodeRabbit, #2138) —
    the same done-callback the TTS-load keepalive uses.
    """
    fut.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        while True:
            done, _ = await asyncio.wait({fut}, timeout=POST_ASR_PING_S)
            if done:
                return
            yield _sse_event("ping", {})
    finally:
        if not fut.done():
            fut.cancel()
_prep_event_helper = dub_pipeline.prep_event  # alias; we keep the module-local _prep_event below for the inline one-liner shape

#: User-facing warning emitted when auto voice cloning is skipped because the
#: speaker labels came from the silence-gap heuristic (see _diarize /
#: extract_speaker_clones — gap-based labels routinely mix two people's audio
#: into one reference, which is how "made up" clone voices happen).
CLONE_SKIP_HEURISTIC_MSG = (
    "auto voice cloning skipped: speaker labels are gap-based estimates — "
    "set up diarization (Model Catalogue → Other weights → pyannote) for per-speaker clones"
)


def _clamp_num_speakers(value) -> Optional[int]:
    """Clamp the user's speaker-count hint to a sane 1–20 range.

    Shared by the SSE and legacy transcribe endpoints so the two can't drift.
    None / non-int / out-of-range → None (auto-detect), so a bad query string
    can never break a diarization call.
    """
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 20 else None


def _recover_from_phrase_embeddings(
    diar_pipe,
    diarized_segments: list[dict],
    *,
    phrases: list[dict],
    requested_speakers: int | None,
    audio_target: str,
    segments: list[dict],
    words: list,
):
    """Recover rapid turns when pyannote collapses a two-speaker exchange.

    Uses ASR phrase boundaries and the embedding/audio components already
    loaded by speaker-diarization-3.1. Weak or imbalanced clusters are rejected
    so ordinary single-speaker recordings remain untouched. Returns
    ``(segments, separation)`` or ``None``.
    """
    present = {
        str(seg.get("speaker_id")) for seg in diarized_segments
        if seg.get("speaker_id")
    }
    if len(present) > 1:
        return None
    usable_phrases = [
        phrase for phrase in phrases
        if phrase.get("text")
        and float(phrase.get("end", 0.0)) - float(phrase.get("start", 0.0)) >= 0.75
    ]
    if len(usable_phrases) < 4:
        return None
    requested = int(requested_speakers) if requested_speakers else 2
    if requested != 2:
        return None
    embedding = getattr(diar_pipe, "_embedding", None)
    audio = getattr(diar_pipe, "_audio", None)
    if embedding is None or audio is None:
        return None
    try:
        import numpy as np
        from pyannote.core import Segment as _PyannoteSegment
        from sklearn.cluster import AgglomerativeClustering

        vectors = []
        durations = []
        for phrase in usable_phrases:
            start, end = float(phrase["start"]), float(phrase["end"])
            duration = end - start
            waveform, _ = audio.crop(
                audio_target, _PyannoteSegment(start, end),
                duration=duration, mode="pad",
            )
            vector = np.asarray(embedding(waveform[None])).reshape(-1)
            if not np.isfinite(vector).all():
                return None
            vectors.append(vector)
            durations.append(duration)
        matrix = np.vstack(vectors)
        labels = np.asarray(AgglomerativeClustering(
            n_clusters=2, metric="cosine", linkage="average",
        ).fit_predict(matrix))
        if len(set(labels.tolist())) != 2:
            return None

        counts = [int(np.sum(labels == cluster)) for cluster in (0, 1)]
        cluster_durations = [
            float(sum(duration for duration, label in zip(durations, labels) if label == cluster))
            for cluster in (0, 1)
        ]
        if min(counts) < 2 or min(cluster_durations) < 1.5:
            return None

        normalized = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
        similarities = normalized @ normalized.T
        within, cross = [], []
        for left in range(len(labels)):
            for right in range(left + 1, len(labels)):
                target = within if labels[left] == labels[right] else cross
                target.append(float(similarities[left, right]))
        if not within or not cross:
            return None
        separation = float(np.mean(within) - np.mean(cross))
        min_separation = 0.12 if requested_speakers == 2 else 0.18
        if separation < min_separation:
            logger.info(
                "phrase-embedding speaker recovery rejected (separation=%.3f < %.3f)",
                separation, min_separation,
            )
            return None

        speaker_map = {}
        turns = []
        for phrase, label in zip(usable_phrases, labels.tolist()):
            if label not in speaker_map:
                speaker_map[label] = f"Speaker {len(speaker_map) + 1}"
            turns.append({
                "start": float(phrase["start"]),
                "end": float(phrase["end"]),
                "speaker": speaker_map[label],
            })
        # Assignment mutates segment dictionaries. Work on copies so a recovery
        # rejected by the final two-speaker check cannot leak partial labels
        # into the ordinary pyannote result.
        assigned = assign_speakers_from_turns([dict(item) for item in segments], turns)
        recovered = resplit_segments_by_turns(assigned, words, turns)
        if len({item.get("speaker_id") for item in recovered if item.get("speaker_id")}) < 2:
            return None
        return recovered, separation
    except Exception:
        logger.exception("phrase-embedding speaker recovery failed")
        return None


@router.get("/dub/transcribe-stream/{job_id}")
async def dub_transcribe_stream(
    job_id: str,
    num_speakers: Optional[int] = None,
    per_segment_refs: bool = True,
):
    """Stream per-chunk segments via SSE, then emit diarized final pass.

    Pre-flight checks (missing job, missing audio, ASR not loaded) are emitted
    as in-stream `error` events rather than HTTP errors, because EventSource
    on the client can't read non-2xx response bodies — a 503 there surfaces
    as an opaque "network error" instead of the actionable message we want.

    `num_speakers` is an optional hint passed straight to pyannote. Left unset,
    pyannote auto-detects the count — but its auto-detect can collapse a
    multi-speaker clip to a single speaker (issue #274). When the user knows
    the exact count, supplying it forces pyannote to return that many speakers.
    On paths that can't honor the hint exactly (inline ASR turns, the
    silence-gap heuristic) it is never silently dropped: the heuristic cycles
    the requested count and a `warning` SSE event tells the user how far the
    labels can be trusted.
    """
    # Clamp to a sane range; ignore anything non-positive / absurd so a bad
    # query string can never break the diarization call. None → auto-detect.
    num_speakers = _clamp_num_speakers(num_speakers)

    # VRAM guard: _gen_body unloads the ASR backend on its normal completion
    # path only — a crash mid-stream, an early `return` (e.g. "audio load
    # failed"), or a client disconnect (GeneratorExit) used to skip that
    # unload and retain the model in VRAM for the rest of the process.
    # _gen_body parks the loaded backend here; the normal unload clears it;
    # gen()'s `finally` unloads whatever is still parked, on EVERY exit.
    _loaded_asr: dict = {"backend": None}
    _asr_work = _ASRWorkLifetime()
    # Same shape, same reason, for the TTS offload (#1191): offload_tts_for_asr()
    # moves the TTS model to CPU, and only _gen_body's success path moved it
    # back — so an abort/error/disconnect stranded it there, silently making
    # every subsequent /generate run on CPU. Set on a successful offload,
    # cleared by the normal restore, honoured by gen()'s `finally` on EVERY exit.
    _tts_offloaded: dict = {"v": False}

    def _log_bg_failure(f, what):
        """Retrieve a fire-and-forget future's exception so it isn't swallowed."""
        if not f.cancelled() and f.exception():
            logger.warning("%s failed: %s", what, f.exception())

    def _restore_tts_bg():
        """Move the TTS model back to the GPU without awaiting (#1191).

        Defined out here rather than inside gen()'s `finally` on purpose: the
        restore has to be dispatchable from a `finally` that also runs under
        GeneratorExit (where awaiting is illegal), and keeping the control flow
        out of the finally itself keeps that block free of the return/break
        pattern that silently swallows in-flight exceptions.
        """
        try:
            _r = asyncio.get_running_loop().run_in_executor(
                _cpu_pool, restore_tts_after_asr
            )
            _r.add_done_callback(
                lambda f: _log_bg_failure(f, "restore_tts_after_asr")
            )
        except RuntimeError:
            # No running loop (interpreter teardown) — best effort, inline.
            try:
                restore_tts_after_asr()
            except Exception as e:
                logger.warning("restore_tts_after_asr failed: %s", e)

    async def _gen_body():
        # ── Preflight — run INSIDE the stream, never before it (#1196) ──
        # This whole block used to run in the endpoint body, before the
        # StreamingResponse existed — i.e. OUTSIDE the stream's terminal-event
        # contract (#516). Two real-world consequences (issue #1196):
        #   * an exception on any unguarded line became an HTTP 500, whose
        #     body EventSource cannot read — the UI could only show the
        #     generic "Transcribe stream dropped … likely ASR backend failed"
        #     guess while a perfectly alive backend knew the real cause;
        #   * not a single byte (not even response headers) went out until
        #     the ASR load finished — a first-run weight download can mean
        #     minutes of total silence, tripping Chrome's hard ~5 min
        #     no-response timeout (and any reverse-proxy timeout in front of
        #     a Docker install), severing the stream with that same generic
        #     message.
        # In here, headers + a first comment go out immediately, keepalive
        # comments flow while the ASR backend loads, and ANY preflight crash
        # lands in gen()'s last-resort finalizer as a structured `error` +
        # terminal `done`.
        # Crash forensics (#1164): transcription is a prime OOM-kill site (ASR
        # model loading on top of a resident TTS model). Record that one started
        # so an unclean death is attributable. Kind only — never media content.
        from core.run_sentinel import touch_activity
        touch_activity("transcribe", "dub")

        job = _get_job(job_id)

        # The durable job is written before the terminal SSE events below. If
        # the renderer, proxy, or backend connection drops in that narrow
        # window, reconnecting must replay the completed result instead of
        # running a second whole-file ASR pass. This is deliberately gated by
        # an explicit completion marker so partial work and imported subtitle
        # rows still take their established paths.
        if job and job.get("transcription_complete") and isinstance(job.get("segments"), list):
            yield _sse_event("final", {
                "segments": job["segments"],
                "source_lang": job.get("source_lang") or "en",
                "full_transcript": job.get("full_transcript") or "",
                "speaker_clones": job.get("cast_sources", {}),
                "cast_sources": job.get("cast_sources", {}),
            })
            yield _sse_event("done", {})
            return

        preflight_error: Optional[str] = None
        # Extra machine-readable fields merged into the preflight `error` SSE event
        # (e.g. the typed asr_model_missing payload → download-CTA in the UI).
        preflight_payload: Optional[dict] = None
        asr_audio_target: Optional[str] = None
        _asr_backend = None
        scene_cuts: list = []
        # Defaulted here, not just inside the preflight block below: it is read from
        # _gen_body (separated_vocals=), so a preflight that bails early would
        # otherwise leave it unbound and raise NameError instead of the real error.
        asr_on_vocals = False

        if not job:
            preflight_error = "Job not found. It may have been cleaned up or was never created."
        else:
            # The TTS core model is loaded here for exactly one reason: to harvest a
            # preloaded `_asr_pipe` off it (passed to get_active_asr_backend below).
            # That attribute is only ever set by VoiceStudio.from_pretrained under
            # OMNIVOICE_PRELOAD_TTS_ASR, which is off by default — so in the default
            # config this loaded ~3 GB, harvested None, and then offload_tts_for_asr()
            # freed it again 60 lines below. On unified memory that offload is a full
            # UNLOAD (#1119), so dub_generate later cold-reloaded the same model (~8s).
            # Every dub paid load → unload → reload for an attribute that was always
            # None. Load it only when there is actually something to harvest.
            _model = None
            if should_preload_tts_asr():
                # Guard the model load: if it raises, the SSE stream would otherwise die
                # before emitting any event, and the UI shows a misleading generic
                # "stream dropped" message instead of the real cause (issue #255).
                try:
                    # Same keepalive treatment as the ASR load below: a cold
                    # TTS load can outlast a reverse proxy's per-read idle
                    # timeout (~60-120 s nginx/Caddy defaults) — the initial
                    # open comment stops the browser's no-response clock but
                    # does not reset a proxy's idle timer.
                    _model_task = asyncio.ensure_future(get_model())
                    _model_task.add_done_callback(
                        lambda f: f.cancelled() or f.exception()
                    )
                    while True:
                        _done, _ = await asyncio.wait(
                            {_model_task}, timeout=ASR_LOAD_KEEPALIVE_S
                        )
                        if _done:
                            break
                        yield b": tts-load keepalive\n\n"
                    _model = _model_task.result()
                except Exception as e:
                    logger.error(
                        "transcribe preflight: model load failed (job=%s): %s",
                        log_safe(job_id), log_safe(e),
                    )
                    from core.failure import build_failure
                    f = build_failure(e, stage="transcribe-preflight", include_diagnostic=False)
                    preflight_error = f["reason"] + (f" — {f['hint']}" if f.get("hint") else "")
                    _model = None
            if preflight_error is None:
                asr_audio_target = job.get("vocals_path")
                if not asr_audio_target or not os.path.exists(asr_audio_target):
                    asr_audio_target = job.get("audio_path")
                # #963: onset snapping is only trustworthy on the Demucs vocals
                # track. When separation failed/was skipped, dub_pipeline sets
                # vocals_path to the mixed audio_path — so compare paths instead
                # of trusting the key's presence.
                asr_on_vocals = bool(asr_audio_target) and asr_audio_target != job.get("audio_path")
                if not asr_audio_target or not os.path.exists(asr_audio_target):
                    preflight_error = "No audio available for transcription."
                else:
                    from services.asr_backend import (
                        ASRModelMissingError,
                        active_backend_id,
                        asr_model_missing_detail,
                        asr_model_missing_error,
                        load_active_asr_backend,
                    )
                    # TTS-only install: no ASR model on disk. Bail BEFORE any
                    # backend is constructed/loaded — the whisper backends would
                    # otherwise silently auto-download multi-GB weights from HF.
                    # Typed payload → the UI renders a one-click download CTA.
                    # A preloaded `_asr_pipe` only substitutes for the
                    # *pytorch-whisper* backend (its sole consumer) — any other
                    # active backend still loads its own weights, so the preflight
                    # must run for them even when the pipe is present.
                    _missing = None
                    _skip_preflight = (
                        getattr(_model, "_asr_pipe", None) is not None
                        and active_backend_id() == "pytorch-whisper"
                    )
                    if not _skip_preflight:
                        _missing = await asyncio.get_running_loop().run_in_executor(
                            None, asr_model_missing_error
                        )
                    if _missing is not None:
                        preflight_error = asr_model_missing_detail(_missing)
                        preflight_payload = _missing
                    if _missing is None:
                        try:
                            # Free recoverable TTS VRAM before ASR chooses its
                            # device. Probing first falsely routed Whisper to
                            # CPU even when this offload made CUDA viable.
                            try:
                                await asyncio.get_running_loop().run_in_executor(
                                    _cpu_pool, offload_tts_for_asr
                                )
                                _tts_offloaded["v"] = True
                            except Exception as e:
                                logger.warning("offload_tts_for_asr failed (continuing): %s", e)
                            # The PyTorch-Whisper backend lazily builds its own pipeline
                            # when no preloaded `_asr_pipe` is present (issue #255), so it
                            # no longer needs OMNIVOICE_PRELOAD_TTS_ASR=1.
                            #
                            # Select + eagerly load in ONE call so a real load failure
                            # (e.g. WhisperX: missing weights, CTranslate2/cuDNN
                            # mismatch, the torch-2.6 weights-only VAD regression)
                            # surfaces once, with its actual cause, as a clean preflight
                            # `error` event — instead of being buried in N cryptic
                            # per-chunk failures and retried on every chunk (#578) —
                            # and so a backend whose deep import chain is rotted (e.g.
                            # `No module named 'lightning_fabric'` from a partial
                            # install, #1185) is marked unavailable and skipped in
                            # favor of the next engine instead of failing ASR init
                            # wholesale. Run in a thread so the (blocking) load
                            # doesn't stall the event loop.
                            import functools
                            _load_fut = asyncio.get_running_loop().run_in_executor(
                                _gpu_pool,
                                functools.partial(
                                    load_active_asr_backend,
                                    asr_pipe=getattr(_model, "_asr_pipe", None),
                                ),
                            )
                            # On client disconnect the ASGI server cancels this
                            # generator mid-wait; the executor load keeps
                            # running (and still caches its result). Retrieve
                            # its eventual exception so asyncio never logs
                            # "Task exception was never retrieved" into the
                            # crash forensics log.
                            _load_fut.add_done_callback(
                                lambda f: f.cancelled() or f.exception()
                            )
                            # Keepalive while the load runs (#1196): a first-run
                            # load may download weights for minutes, and a
                            # byte-silent stream gets severed by Chrome's
                            # ~5 min no-response cap or a reverse proxy's idle
                            # timeout — which the UI can only render as the
                            # generic "stream dropped" guess. SSE comment
                            # lines are invisible to EventSource, so no client
                            # changes are needed.
                            while True:
                                _done, _ = await asyncio.wait(
                                    {_load_fut}, timeout=ASR_LOAD_KEEPALIVE_S
                                )
                                if _done:
                                    break
                                yield b": asr-load keepalive\n\n"
                            _asr_backend = _load_fut.result()
                            _loaded_asr["backend"] = _asr_backend
                        except ASRModelMissingError as e:
                            # A broken primary fell through to a fallback whose
                            # weights aren't installed — same typed payload
                            # (and download CTA) as the initial preflight.
                            preflight_error = asr_model_missing_detail(e.payload)
                            preflight_payload = e.payload
                        except Exception as e:
                            logger.error("Transcription preflight ASR load failed")
                            from core.failure import build_failure
                            f = build_failure(e, stage="transcribe-preflight", include_diagnostic=False)
                            preflight_error = "ASR backend initialization failed: " + f["reason"] + (
                                f" — {f['hint']}" if f.get("hint") else ""
                            )
                    scene_cuts = job.get("scene_cuts") or []

        if preflight_error:
            # Always follow a terminal `error` with `done` so the stream closes
            # via a named event, not a raw connection drop. A bare error+close
            # races the browser's native EventSource error (which carries no
            # `data`); if that native error wins, the client falls back to the
            # misleading generic "stream dropped … ASR backend failed" message
            # and the real cause (in `detail`) is lost (#578).
            yield _sse_event("error", {"detail": preflight_error, "retryable": True,
                                       **(preflight_payload or {})})
            yield _sse_event("done", {})
            return
        import math
        import tempfile
        loop = asyncio.get_running_loop()

        def _load():
            audio_np, sr = sf.read(asr_audio_target, dtype="float32")
            if audio_np.ndim > 1:
                audio_np = audio_np.mean(axis=1)
            return audio_np, sr

        try:
            audio_np, sr = await loop.run_in_executor(_cpu_pool, _load)
        except Exception:
            # Terminal error → always emit `done` (see preflight note, #578).
            from core.public_errors import stream_failure
            yield _sse_event("error", stream_failure("transcription_failed"))
            yield _sse_event("done", {})
            return

        total = float(len(audio_np)) / float(sr) if sr else 0.0
        global_speaker_clustering = bool(
            getattr(
                _asr_backend,
                "requires_full_audio_for_speaker_consistency",
                False,
            )
        )
        transcribe_chunk_s = (
            total
            if global_speaker_clustering and total > 0
            else TRANSCRIBE_CHUNK_S
        )
        transcribe_timeout_s = (
            ASR_TRANSCRIBE_TIMEOUT_S
            if global_speaker_clustering
            else TRANSCRIBE_CHUNK_TIMEOUT_S
        )
        transcribe_timeout_env = (
            "OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S"
            if global_speaker_clustering
            else "OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S"
        )
        chunks_n = (
            max(1, int(math.ceil(total / transcribe_chunk_s)))
            if total > 0
            else 1
        )
        yield _sse_event("start", {
            "duration": total,
            "chunks": chunks_n,
            "chunk_s": transcribe_chunk_s,
        })

        all_segments: list[dict] = []
        # Words (global-timeline) retained so diarization can re-split a segment
        # that spans two speakers' turns at the word boundary (#486).
        all_words: list = []
        # Preserve the ASR backend's natural phrase boundaries before
        # segment_transcript merges short neighboring phrases. Pyannote 3.1
        # occasionally collapses rapid exchanges into one dominant speaker; in
        # that narrow case these phrase spans give its own WeSpeaker embedding
        # model clean candidate utterances for a conservative recovery pass.
        asr_phrase_segments: list[dict] = []
        detected_lang = None
        next_seg_id = 0
        chunk_errors: list[str] = []
        chunk_error_codes: list[str] = []
        # Speaker turns from an ASR backend that diarizes inline (FunASR cam++).
        # When present, _diarize() uses them and skips pyannote (Phase 2, #182).
        asr_speaker_turns: list[dict] = []

        for i in range(chunks_n):
            if job.get("aborted"):
                yield _sse_event("aborted", {})
                return
            t0 = i * transcribe_chunk_s
            t1 = min(total, t0 + transcribe_chunk_s)
            s_from = int(t0 * sr)
            s_to = int(t1 * sr)
            chunk_arr = audio_np[s_from:s_to]
            if len(chunk_arr) == 0:
                continue

            def _transcribe_chunk(arr=chunk_arr, offset=t0, local_sr=sr):
                # Route through the active backend (WhisperX by default).
                # Backends all take a file path, so write the chunk first.
                try:
                    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                    tmp.close()
                    try:
                        _safe_soundfile_write(tmp.name, arr, local_sr)
                        r = _asr_backend.transcribe(tmp.name, word_timestamps=True)
                    finally:
                        try: os.remove(tmp.name)
                        except OSError: pass
                    shifted = []
                    for c in r.get("chunks", []) or []:
                        ts = c.get("timestamp", (0.0, 0.0)) or (0.0, 0.0)
                        a0 = (ts[0] if ts[0] is not None else 0.0) + offset
                        a1 = (ts[1] if ts[1] is not None else 0.0) + offset
                        shifted.append({"text": c.get("text", ""), "timestamp": (a0, a1)})
                    # Inline-diarization speaker turns (FunASR cam++), offset-shifted
                    # to the full-audio timeline so _diarize() can use them.
                    turns = []
                    for seg in r.get("segments", []) or []:
                        spk = seg.get("speaker")
                        s0, s1 = seg.get("start"), seg.get("end")
                        if spk is None or s0 is None or s1 is None:
                            continue
                        turns.append({"start": s0 + offset, "end": s1 + offset, "speaker": spk})
                    return {"chunks": shifted, "language": r.get("language"), "speaker_turns": turns}
                except Exception as exc:
                    # Keep diagnostics local and fixed-shape. In particular,
                    # CUDA OOM is a distinct, actionable recovery class rather
                    # than the generic "no segments" dead end.
                    is_memory = isinstance(exc, torch.OutOfMemoryError)
                    logger.error(
                        "Chunk transcription failed (backend=%s; class=%s; details withheld)",
                        _asr_backend.id,
                        type(exc).__name__,
                    )
                    from core.public_errors import stream_failure
                    failure = stream_failure(
                        "transcription_memory" if is_memory else "transcription_failed"
                    )
                    return {
                        "chunks": [],
                        "language": None,
                        "error": failure["detail"],
                        "error_code": failure["code"],
                    }

            # Retry an ordinary completed failure once. A timed-out native call
            # is different: its thread is still executing and must not overlap
            # a retry against the same backend (#1669).
            part = None
            timed_out = False
            for _attempt in range(1, _CHUNK_TRANSCRIBE_ATTEMPTS + 1):
                # Run as a task and poll so pings keep the EventSource alive.
                task = asyncio.ensure_future(run_transcribe_guarded(
                    _gpu_pool, lambda: _asr_work.run(_transcribe_chunk),
                    what=f"Dub chunk {i + 1}/{chunks_n}",
                    timeout=transcribe_timeout_s,
                    timeout_env=transcribe_timeout_env,
                ))
                while True:
                    done, _pending = await asyncio.wait({task}, timeout=5.0)
                    if done:
                        break
                    yield _sse_event("ping", {})
                try:
                    part = task.result()
                except ASRTimeoutError:
                    # Python cannot kill an in-process native transcribe. Do
                    # not swap pools and retry over the still-running call:
                    # concurrent whisperx/CTranslate2 access caused the native
                    # Windows access violation in #1669. Stop this transcript;
                    # the worker remains honestly occupied until it exits.
                    timed_out = True
                    logger.error(
                        "Transcribe chunk %d/%d timed out after %.0fs (attempt %d/%d, job=%s)",
                        i + 1, chunks_n, transcribe_timeout_s, _attempt,
                        _CHUNK_TRANSCRIBE_ATTEMPTS, log_safe(job_id),
                    )
                    from core.public_errors import stream_failure
                    failure = stream_failure("transcription_timeout")
                    part = {
                        "chunks": [],
                        "language": None,
                        "error": failure["detail"],
                        "error_code": failure["code"],
                    }
                # Success → keep it. Failure/timeout → retry once on a fresh
                # worker (the internal _transcribe_chunk except returns an
                # error-part; the timeout path already reset the pool).
                if part is not None and not part.get("error"):
                    break
                if timed_out:
                    break
                if not timed_out and _attempt < _CHUNK_TRANSCRIBE_ATTEMPTS:
                    logger.warning(
                        "Retrying transcribe chunk %d/%d after failure/timeout (next attempt %d/%d, job=%s)",
                        i + 1, chunks_n, _attempt + 1, _CHUNK_TRANSCRIBE_ATTEMPTS, log_safe(job_id),
                    )
                    # A completed exception did not leave native work behind,
                    # so retrying this same audio window is safe.
            if part.get("error"):
                chunk_errors.append(part["error"])
                if part.get("error_code"):
                    chunk_error_codes.append(part["error_code"])
                logger.warning("Chunk %d/%d error: %s", i + 1, chunks_n, log_safe(part["error"]))
            if timed_out:
                break
            if detected_lang is None and part.get("language"):
                detected_lang = part["language"]
            asr_speaker_turns.extend(part.get("speaker_turns") or [])
            for _phrase in part.get("chunks", []) or []:
                _pts = _phrase.get("timestamp") or (None, None)
                _ptext = (_phrase.get("text") or "").strip()
                try:
                    _ps, _pe = float(_pts[0]), float(_pts[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if _ptext and _pe > _ps:
                    asr_phrase_segments.append({
                        "start": _ps, "end": _pe, "text": _ptext,
                    })
            chunk_segs = segment_transcript(part, duration=t1, scene_cuts=scene_cuts)
            # Same word source segment_transcript used (already global-timeline),
            # kept for the post-diarization speaker re-split (#486).
            try:
                all_words.extend(_words_from_whisper(part))
            except Exception:
                pass
            # #280: Whisper often stretches a segment's start back over
            # leading music/silence (classic case: speech begins at 0:03,
            # transcript says 0.0 → the dub plays 3 s early). Snap starts
            # forward to the actual speech onset. `audio_np` is the same
            # track ASR ran on — vocals.wav when Demucs succeeded. #963:
            # when it didn't (mixed audio), snapping is disabled — every
            # footstep/sigh/score cue is a false onset candidate there.
            try:
                snap_segment_starts(chunk_segs, audio_np, sr,
                                    separated_vocals=asr_on_vocals)
            except Exception as e:
                logger.warning("onset alignment skipped for chunk %d: %s", i, e)
            # Provisional per-chunk labels for the streaming UI only — the
            # final diarization pass below overwrites them. Honor the user's
            # speaker-count hint here too so the interim view doesn't flip
            # between 2 and N speakers.
            chunk_segs = assign_speakers_heuristic(chunk_segs, num_speakers)
            for s in chunk_segs:
                s["id"] = f"s{next_seg_id:05x}"
                s["text_original"] = s.get("text", "")
                next_seg_id += 1
            all_segments.extend(chunk_segs)
            yield _sse_event("segments", {
                "chunk": i, "total_chunks": chunks_n,
                "segments": chunk_segs,
                "progress": (i + 1) / chunks_n,
                "error": part.get("error"),
                "error_code": part.get("error_code"),
            })

        if job.get("aborted"):
            yield _sse_event("aborted", {})
            return

        # Empty-transcription guard: if every chunk came back with zero
        # segments we can't proceed to diarization/clone extraction. Emit an
        # actionable error so the UI can surface a Retry instead of silently
        # landing in an empty editor. Commonly caused by a first-run model
        # download failure, a PyTorch 2.6 weights_only regression inside
        # whisperx's VAD load, or an unsupported audio format.
        if not all_segments:
            # Deduplicate while preserving order so one root cause doesn't
            # repeat N times in the UI toast. Sanitize each message so home
            # paths / tokens from a backend traceback never leak (#255).
            from core.failure import sanitize, build_failure
            seen = set()
            uniq: list[str] = []
            for msg in chunk_errors:
                s = sanitize(msg)
                if s and s not in seen:
                    seen.add(s)
                    uniq.append(s)
            if uniq:
                # Chunk failures already carry a complete recovery message.
                # Do not prepend another generic sentence to it.
                detail = " | ".join(uniq[:3])
                # Add the actionable hint for a recognized failure class
                # (e.g. pkg_resources missing → install setuptools).
                hint = build_failure(" ".join(uniq), stage="transcribe", include_diagnostic=False).get("hint")
                if hint:
                    detail += f" — {hint}"
            else:
                detail = (
                    "Transcription produced no segments. The audio may be silent, "
                    "too short, or in an unsupported format. Try re-uploading or "
                    "check that the source has an audible speech track."
                )
            logger.error("transcribe yielded 0 segments (job=%s): %s", log_safe(job_id), log_safe(detail))
            payload = {"detail": detail, "retryable": True}
            if chunk_error_codes:
                payload["code"] = chunk_error_codes[0]
            yield _sse_event("error", payload)
            yield _sse_event("done", {})
            return

        def _diarize():
            """Returns (segments, warning_payload_or_None, labels_source).

            `labels_source` records where the speaker labels came from —
            `"pyannote"` | `"turns"` | `"heuristic"` — so downstream
            auto-clone extraction can refuse to cut reference audio from
            gap-based estimates (a mixed-speaker reference is how "made up"
            clone voices happen).

            `warning_payload` is a structured dict
            `{detail, error_class, docs_url}` whenever we silently fell back
            to the silence-gap heuristic (no HF_TOKEN, model unavailable,
            license not accepted, or pyannote raised) — or whenever the
            user's `num_speakers` hint could not be honored exactly. The
            heuristic only detects speaker turns from >1.2s silences, so a
            rapid-fire man↔woman exchange will read as one speaker. Issue
            #78 — we attach an `error_class` so the front-end's errorDocsMap
            can render a "See docs" deeplink instead of a dead-end toast.
            """
            from services.model_manager import (
                DIARIZATION_ERR_LICENSE,
                DIARIZATION_ERR_NO_TOKEN,
                DIARIZATION_ERR_MISSING,
            )
            from core import error_docs_map

            def _hint_suffix() -> str:
                """Honest caveat appended to heuristic-fallback warnings when a
                multi-speaker hint is set: the count is now honored, but the
                heuristic can't attribute voices. (A hint of 1 IS fully
                honored — one label — so it needs no caveat.)"""
                if not num_speakers or num_speakers < 2:
                    return ""
                return (
                    f" Your speaker-count setting ({num_speakers}) is only "
                    f"approximately honored: the heuristic cycles "
                    f"{num_speakers} speaker labels on silence gaps instead "
                    f"of recognizing voices, so lines may be attributed to "
                    f"the wrong speaker."
                )

            def _use_turns(crash: Exception | None = None, err_sentinel=None):
                """Label from the ASR backend's inline speaker turns; warn when
                that means the user's explicit count can't be enforced."""
                logger.info(
                    "Using inline ASR diarization (%d turns)%s.",
                    len(asr_speaker_turns),
                    "" if crash else "; skipping pyannote",
                )
                assigned = assign_speakers_from_turns(all_segments, asr_speaker_turns)
                # #486: split any segment that spans two speakers' turns at the
                # word boundary (single-speaker segments pass through unchanged).
                resplit = resplit_segments_by_turns(assigned, all_words, asr_speaker_turns)
                if not num_speakers:
                    return resplit, None, "turns"
                error_class = (
                    "HF_AUTH_FAILED"
                    if err_sentinel == DIARIZATION_ERR_NO_TOKEN
                    else "PYANNOTE_LICENSE_REQUIRED"
                )
                if crash:
                    detail = (
                        f"Speaker diarization crashed mid-run "
                        f"({type(crash).__name__}); falling back to the ASR "
                        f"engine's built-in speaker turns. Speaker-count hint "
                        f"ignored: the detected count may differ from the "
                        f"{num_speakers} you set."
                    )
                else:
                    detail = (
                        f"Speaker-count hint ignored: pyannote diarization is "
                        f"unavailable, so the ASR engine's built-in speaker "
                        f"turns were used and the detected count may differ "
                        f"from the {num_speakers} you set. Set up diarization "
                        f"(Model Catalogue → Other weights → pyannote) to enforce an exact "
                        f"speaker count."
                    )
                return resplit, {
                    "detail": detail,
                    "error_class": error_class,
                    "docs_url": error_docs_map.lookup(error_class),
                    "speaker_hint": {"requested": num_speakers, "status": "ignored"},
                }, "turns"

            # The active ASR backend already diarized inline (FunASR cam++):
            # its turns are the fast path and skip pyannote entirely (#182) —
            # but ONLY when the user didn't set an explicit speaker count.
            # Inline turns can't be forced to N speakers through the shared ASR
            # contract, so a set num_speakers prefers pyannote — the one engine
            # that honors an exact count. When pyannote can't load, the turns
            # are still the best labels available; use them and say so instead
            # of silently eating the hint.
            diar_pipe = None
            err_sentinel = None
            if asr_speaker_turns:
                if num_speakers:
                    diar_pipe, err_sentinel = get_diarization_pipeline(return_error=True)
                if not diar_pipe:
                    return _use_turns(err_sentinel=err_sentinel)
                logger.info(
                    "num_speakers=%d set: preferring pyannote over %d inline "
                    "ASR turns (only pyannote honors an exact count).",
                    num_speakers, len(asr_speaker_turns),
                )
            else:
                diar_pipe, err_sentinel = get_diarization_pipeline(return_error=True)
            if not diar_pipe:
                # Phase 1 AUTH-01: ask the resolver (App → Env → HF-CLI),
                # not just the env var. This is the #35 fix — users who
                # ran `huggingface-cli login` previously saw the "no
                # HF_TOKEN" branch even though the library would have
                # read the token. Now the cascade is honoured.
                from services import token_resolver
                resolved = token_resolver.resolve()

                if err_sentinel == DIARIZATION_ERR_MISSING:
                    from services.diarization_runtime import SORTFORMER, selected_backend
                    native_selected = selected_backend() == SORTFORMER
                    detail = (
                        "Native Sortformer files are missing. Install audiocpp_cli beside "
                        "the audio.cpp native bundle in Settings > Models > "
                        "Diarisation, then retry transcription. "
                        "Using silence gaps for now; rapid speaker turns may be merged."
                    ) if native_selected else (
                        "Speaker diarization files are missing or incomplete. "
                        "Install or repair pyannote in Settings > Models > Diarisation, "
                        "then retry transcription. No models were downloaded during "
                        "this job. Using silence gaps for now; rapid speaker turns "
                        "may be merged."
                    )
                    error_class = "DIARIZATION_MODEL_MISSING"
                elif err_sentinel == DIARIZATION_ERR_NO_TOKEN:
                    detail = (
                        "Speaker diarization is disabled because no HuggingFace token "
                        "was found in any source (Settings → API Keys, the HF_TOKEN "
                        "env var, or ~/.cache/huggingface/token from `huggingface-cli "
                        "login`). To detect multiple speakers, set a token in one of "
                        "those places and accept the pyannote/speaker-diarization-3.1 "
                        "license at huggingface.co. Falling back to a silence-gap "
                        "heuristic — turns with no audible pause between them will "
                        "be merged into one speaker."
                    )
                    error_class = "HF_AUTH_FAILED"
                elif err_sentinel == DIARIZATION_ERR_LICENSE:
                    who = resolved.username if resolved else "(not signed in)"
                    detail = (
                        f"Speaker diarization model is gated — the "
                        f"pyannote/speaker-diarization-3.1 license has not been "
                        f"accepted on HuggingFace by this account "
                        f"(user={who}). Visit "
                        f"huggingface.co/pyannote/speaker-diarization-3.1 AND "
                        f"huggingface.co/pyannote/segmentation-3.0 while signed "
                        f"in and click 'Agree and access repository' on both, "
                        f"then restart this dub job. Falling back to a "
                        f"silence-gap heuristic; rapid speaker turns may be "
                        f"merged into one speaker."
                    )
                    error_class = "PYANNOTE_LICENSE_REQUIRED"
                else:
                    # err_sentinel == DIARIZATION_ERR_LOAD (or unexpected None
                    # with a resolved token — historical safety net).
                    detail = (
                        f"The installed speaker diarization model failed to load. "
                        f"See Settings > Logs > Backend for "
                        f"the underlying error. Falling back to a silence-gap "
                        f"heuristic; rapid speaker turns may be merged."
                    )
                    error_class = "DIARIZATION_LOAD_FAILED"
                warning = {
                    "detail": detail + _hint_suffix(),
                    "error_class": error_class,
                    "docs_url": error_docs_map.lookup(error_class),
                }
                if num_speakers:
                    warning["speaker_hint"] = {
                        "requested": num_speakers,
                        "status": "approximate" if num_speakers > 1 else "honored",
                    }
                return (
                    assign_speakers_heuristic(all_segments, num_speakers),
                    warning,
                    "heuristic",
                )
            try:
                # Pass the user's speaker-count hint through to pyannote when
                # provided (#274). pyannote's apply() accepts num_speakers;
                # omit it entirely when None so we don't depend on the kwarg
                # existing in every pyannote build.
                from services.diarization_native import NativeSortformer
                if isinstance(diar_pipe, NativeSortformer):
                    diar = diar_pipe(
                        asr_audio_target, num_speakers=num_speakers, job_id=job_id,
                        cancel_check=lambda: bool(job.get("aborted")) or task_manager.is_cancelled(job_id),
                    )
                elif num_speakers:
                    logger.info("Diarizing with num_speakers=%d (user hint)", num_speakers)
                    diar = diar_pipe(asr_audio_target, num_speakers=num_speakers)
                else:
                    diar = diar_pipe(asr_audio_target)
                assigned = assign_speakers_from_diarization(all_segments, diar)
                # #486: split any segment that spans two speakers' turns at the
                # word boundary (single-speaker segments pass through unchanged).
                resplit = resplit_segments_by_diarization(assigned, all_words, diar)
                recovered = _recover_from_phrase_embeddings(
                    diar_pipe,
                    resplit,
                    phrases=asr_phrase_segments,
                    requested_speakers=num_speakers,
                    audio_target=asr_audio_target,
                    segments=all_segments,
                    words=all_words,
                )
                if recovered is not None:
                    recovered_segments, separation = recovered
                    logger.info(
                        "Recovered rapid two-speaker exchange from ASR phrase embeddings "
                        "(phrases=%d, separation=%.3f).",
                        len(asr_phrase_segments), separation,
                    )
                    return recovered_segments, None, "phrase_embeddings"
                return resplit, None, "audiocpp-sortformer" if isinstance(diar_pipe, NativeSortformer) else "pyannote"
            except Exception as e:
                logger.exception("Diarization failed")
                # Inline ASR turns beat the silence-gap heuristic as a crash
                # fallback (this path is reachable with turns present since a
                # set num_speakers routes turns-jobs through pyannote).
                if asr_speaker_turns:
                    return _use_turns(crash=e)
                # Mid-run failure — classify against the same sentinels so a
                # post-load 401 (rare but possible after a token rotation)
                # still gets the right docs deeplink.
                from services.model_manager import _classify_diarization_error
                err_class_post = _classify_diarization_error(e)
                error_class = (
                    "PYANNOTE_LICENSE_REQUIRED"
                    if err_class_post == DIARIZATION_ERR_LICENSE
                    else "PYANNOTE_LICENSE_REQUIRED"  # LOAD failures land here too
                )
                warning = {
                    "detail": (
                        f"Speaker diarization crashed mid-run "
                        f"({type(e).__name__}); falling back to a silence-gap "
                        f"heuristic. Rapid speaker turns may be merged."
                        + _hint_suffix()
                    ),
                    "error_class": error_class,
                    "docs_url": error_docs_map.lookup(error_class),
                }
                if num_speakers:
                    warning["speaker_hint"] = {
                        "requested": num_speakers,
                        "status": "approximate" if num_speakers > 1 else "honored",
                    }
                return (
                    assign_speakers_heuristic(all_segments, num_speakers),
                    warning,
                    "heuristic",
                )

        fut_diar = loop.run_in_executor(_gpu_pool, lambda: _asr_work.run(_diarize))
        async for _ping in _ping_while(fut_diar):
            yield _ping
        final_segs, diar_warning, labels_source = fut_diar.result()
        if job.get("aborted") or task_manager.is_cancelled(job_id):
            yield _sse_event("aborted", {})
            return
        if diar_warning:
            logger.warning("diarization fallback: %s", diar_warning.get("detail"))
            payload = {
                "detail": diar_warning.get("detail"),
                "source": "diarization",
                "error_class": diar_warning.get("error_class"),
                "docs_url": diar_warning.get("docs_url"),
            }
            # Machine-readable trail of what happened to the user's
            # speaker-count hint (the `detail` text carries the human story).
            if diar_warning.get("speaker_hint"):
                payload["speaker_hint"] = diar_warning["speaker_hint"]
            yield _sse_event("warning", payload)

        from services.segmentation import deduplicate_chunk_segments
        final_segs = deduplicate_chunk_segments(final_segs)
        job["segments"] = final_segs

        # Auto-speaker-clone: sample each detected speaker's voice from the
        # Demucs-isolated vocals track and assign `auto:speaker_N` as the
        # default profile for their segments. This is what lets a user add a
        # new target language and have the ORIGINAL speaker speak it — the
        # central pro-grade dubbing promise.
        try:
            from services.speaker_clone import (
                auto_profile_id,
                build_cast_sources,
                extract_speaker_clones,
            )
            vocals_for_clone = job.get("vocals_path") or asr_audio_target
            clones = {}
            if labels_source == "heuristic":
                # Clone-purity guard: heuristic labels are silence-gap
                # estimates, not voice identity — a per-speaker reference cut
                # from them routinely concatenates two people's audio and the
                # clone sounds "made up". Skip auto-clones and say so instead
                # of shipping bad ones. (extract_speaker_clones enforces the
                # same guard internally; this branch exists to surface the
                # warning to the user.)
                logger.info(
                    "auto speaker clones skipped (labels_source=heuristic, job=%s)",
                    log_safe(job_id),
                )
                yield _sse_event("warning", {
                    "detail": CLONE_SKIP_HEURISTIC_MSG,
                    "source": "speaker_clone",
                })
            else:
                # Clones are written into THIS job's dir, never alongside the
                # vocals (#1331): on a content-hash cache hit vocals_path
                # points into an OLDER job's dir, so dirname(vocals) wrote the
                # new job's clone refs into a directory the user can delete by
                # removing that older history entry — after which every
                # single-segment regen silently rendered in the default voice.
                _clone_dir = _safe_job_dir(job_id) or os.path.dirname(vocals_for_clone)
                os.makedirs(_clone_dir, exist_ok=True)
                fut_clones = loop.run_in_executor(
                    _cpu_pool, lambda: extract_speaker_clones(
                        vocals_for_clone, final_segs,
                        _clone_dir,
                        labels_source=labels_source,
                    ),
                )
                async for _ping in _ping_while(fut_clones):
                    yield _ping
                clones = fut_clones.result()
                if clones:
                    from services.speaker_clone import refine_ref_texts
                    # Bound the re-transcribe like every other ASR dispatch in
                    # this file (#730): a wedged transcribe would otherwise hold
                    # the GPU-pool worker forever and starve later work into a
                    # "can't reach backend". On timeout the guard resets the pool
                    # and raises — keep the original (unrefined) clones, matching
                    # refine_ref_text's own "failure is a strict no-op" fallback.
                    try:
                        fut_refine = asyncio.ensure_future(run_transcribe_guarded(
                            _gpu_pool,
                            lambda: _asr_work.run(lambda: refine_ref_texts(clones, _asr_backend)),
                            what="Dub clone ref-text refine",
                        ))
                        async for _ping in _ping_while(fut_refine):
                            yield _ping
                        clones = fut_refine.result()
                    except ASRTimeoutError as e:
                        logger.warning(
                            "clone ref-text refine timed out; keeping original ref_text: %s", e
                        )
            # Wave 3.2: per-segment clone refs. Cut each long-enough segment's
            # own reference from the vocals so the dub of each line matches the
            # prosody of its source line. Short lines fall back to the
            # per-speaker clone below. Default on; the user can force
            # per-speaker by disabling it (job["per_segment_refs"]).
            seg_clones = {}
            job["per_segment_refs"] = per_segment_refs
            if per_segment_refs:
                try:
                    from services.speaker_clone import extract_segment_refs
                    seg_ids_for_clone = [s.get("id", i) for i, s in enumerate(final_segs)]
                    # Same #1331 rule as the per-speaker extraction above, and
                    # this is the DEFAULT path: per-segment references must
                    # live in THIS job's dir, or a cache-hit job's clips die
                    # with the older job they were written next to (both
                    # reviewers, on the first version of this fix).
                    _seg_clone_dir = _safe_job_dir(job_id) or os.path.dirname(vocals_for_clone)
                    os.makedirs(_seg_clone_dir, exist_ok=True)
                    fut_seg_refs = loop.run_in_executor(
                        _cpu_pool, lambda: extract_segment_refs(
                            vocals_for_clone, final_segs,
                            _seg_clone_dir,
                            seg_ids=seg_ids_for_clone,
                        ),
                    )
                    async for _ping in _ping_while(fut_seg_refs):
                        yield _ping
                    seg_clones = fut_seg_refs.result()
                    if seg_clones:
                        from services.speaker_clone import refine_ref_texts
                        # Same guard as the per-speaker refine above (#730):
                        # keep the original seg_clones on a wedge/timeout.
                        try:
                            fut_refine = asyncio.ensure_future(run_transcribe_guarded(
                                _gpu_pool,
                                lambda: _asr_work.run(lambda: refine_ref_texts(seg_clones, _asr_backend)),
                                what="Dub segment ref-text refine",
                            ))
                            async for _ping in _ping_while(fut_refine):
                                yield _ping
                            seg_clones = fut_refine.result()
                        except ASRTimeoutError as e:
                            logger.warning(
                                "segment ref-text refine timed out; keeping original ref_text: %s", e
                            )
                        job["segment_clones"] = seg_clones
                except Exception as e:
                    logger.warning("per-segment clone refs skipped: %s", e)

            cast_sources = build_cast_sources(final_segs, clones, seg_clones)
            job["cast_sources"] = cast_sources
            if cast_sources:
                if clones:
                    job["speaker_clones"] = clones
                # Default each segment's profile_id to its detected speaker's
                # auto-clone — but only if the user hasn't already assigned
                # something. (#486)
                #
                # We prefer the UI-visible `auto:{speaker}` id over the
                # per-segment `auto-seg:{id}` id even when a per-segment ref
                # exists, because the dub editor's Voice dropdown only renders
                # `auto:` options ("From Video → Speaker N"). An `auto-seg:`
                # value matches no <option>, so the row silently read
                # "Default" while the speaker was actually bound — exactly the
                # reported bug. The per-segment ref is NOT lost: dub_generate's
                # `auto:` branch transparently prefers this segment's own
                # per-segment ref (job["segment_clones"][seg_id]) when present,
                # so a row shown as "Speaker 1" still clones from its own line
                # when that line is long enough.
                for s in final_segs:
                    if s.get("profile_id"):
                        continue
                    spk = s.get("speaker_id") or "Speaker 1"
                    if spk in cast_sources:
                        # Keep one UI-visible value for pooled and per-segment
                        # sources. Generation resolves this line's own clip
                        # first and falls back to the speaker's best clip.
                        s["profile_id"] = auto_profile_id(spk)
        except Exception as e:
            logger.warning("speaker_clone extraction skipped: %s", e)

        job["source_lang"] = job.get("source_lang_override") or _detected_source_lang(
            detected_lang
        )
        job["full_transcript"] = " ".join(s.get("text", "") for s in final_segs)
        job["transcription_complete"] = True
        _save_job(job_id, job)

        # Restore TTS model to GPU now that ASR is done. unload() blocks
        # (gc.collect + CUDA cache drop) — run it on the GPU pool so the
        # event loop stays responsive; await it, because the TTS restore
        # below must not contend with the ASR weights for VRAM
        # (CodeRabbit review, #1198 — normal-completion half).
        if _asr_backend:
            try:
                fut_unload = loop.run_in_executor(_gpu_pool, lambda: _asr_work.cleanup(_asr_backend.unload))
                async for _ping in _ping_while(fut_unload):
                    yield _ping
                fut_unload.result()
            except Exception as e:
                logger.warning("Failed to unload ASR backend: %s", e)
            # Unload attempted once — don't retry from gen()'s finally.
            _loaded_asr["backend"] = None

        fut_restore = loop.run_in_executor(_cpu_pool, restore_tts_after_asr)
        async for _ping in _ping_while(fut_restore):
            yield _ping
        fut_restore.result()
        # Debt paid — don't make gen()'s finally repeat it.
        _tts_offloaded["v"] = False

        # The offload dance above is what made room; hand back whatever the
        # allocator is still holding on the accelerator this host actually
        # synthesizes on (MPS-only here left CUDA, XPU and Ascend NPU hosts
        # holding the freed blocks).
        fut_release = loop.run_in_executor(_gpu_pool, release_device_cache)
        async for _ping in _ping_while(fut_release):
            yield _ping
        fut_release.result()

        yield _sse_event("final", {
            "segments": final_segs,
            "source_lang": job["source_lang"],
            "full_transcript": job["full_transcript"],
            # The client only needs labels and durations. Never send host
            # paths or reference transcripts through this public event.
            "speaker_clones": job.get("cast_sources", {}),
            "cast_sources": job.get("cast_sources", {}),
        })
        yield _sse_event("done", {})

    async def gen():
        # First byte out the moment the stream starts (#1196): the browser's
        # no-response clock stops, buffering middlemen flush the headers, and
        # EventSource reports the stream open — all BEFORE the preflight
        # (which may load models for minutes) runs inside _gen_body. A
        # comment line is invisible to client event handlers.
        yield b": transcribe-stream open\n\n"
        # Terminal-event guard (#516): the SSE stream must NEVER close without a
        # terminal event. Any unanticipated exception in the body (e.g. an ASR
        # load that escapes the per-chunk handler) previously dropped the
        # connection, which the frontend can only report as "stream dropped,
        # likely ASR failed" — hiding the real cause. Emit a structured `error`
        # (with the actionable hint from build_failure) then `done`, so the user
        # sees the real failure + a Retry instead of a silent disconnect.
        try:
            async for ev in _gen_body():
                yield ev
        except Exception:  # noqa: BLE001 — last-resort stream finalizer
            logger.error("Transcription stream failed unexpectedly")
            from core.public_errors import stream_failure
            yield _sse_event("error", stream_failure("transcription_failed"))
            yield _sse_event("done", {})
        finally:
            _asr_work.stop()
            # Last-resort VRAM release (see _loaded_asr above): covers crashes,
            # early terminal-error returns, and client disconnects
            # (GeneratorExit bypasses the except, never this finally).
            _b = _loaded_asr.get("backend")
            _loaded_asr["backend"] = None
            # Pay the TTS-restore debt on every exit path (#1191). Leaving it
            # unpaid is what stranded the TTS model on CPU after an abort or a
            # disconnect, degrading every later generation by 10-50x.
            _restore_tts = _tts_offloaded["v"]
            _tts_offloaded["v"] = False

            def _submit_tts_restore(_f=None):
                if _f is not None:
                    _log_bg_failure(_f, "Unloading ASR backend")
                if _restore_tts:
                    _restore_tts_bg()

            if _b is not None:
                # unload() blocks (gc.collect + CUDA cache drop can take
                # seconds) and this finally also runs under GeneratorExit,
                # where awaiting is illegal — so hand it to the GPU pool
                # fire-and-forget and retrieve the eventual exception
                # (CodeRabbit review, #1198).
                try:
                    _fut = asyncio.get_running_loop().run_in_executor(
                        _gpu_pool, lambda: _asr_work.cleanup(_b.unload)
                    )
                    # Restore the TTS model only AFTER the ASR weights are
                    # freed — the same ordering the success path enforces, so
                    # the two never contend for VRAM.
                    _fut.add_done_callback(_submit_tts_restore)
                except RuntimeError:
                    # No running loop (interpreter teardown) — best effort.
                    try:
                        _asr_work.cleanup(_b.unload)
                    except Exception as e:
                        logger.warning("Failed to unload ASR backend: %s", e)
                    _submit_tts_restore()
            else:
                _submit_tts_restore()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/dub/transcribe/{job_id}")
async def dub_transcribe(job_id: str, num_speakers: Optional[int] = None):
    """Legacy synchronous transcribe (kept for the headless CLI).

    `num_speakers` mirrors the SSE endpoint's query param (same 1–20 clamp):
    an exact speaker count forwarded to pyannote, or cycled by the silence-gap
    heuristic when pyannote is unavailable. None → auto-detect.
    """
    num_speakers = _clamp_num_speakers(num_speakers)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Same as the streaming preflight: the only use of the TTS core here is the
    # last-resort `_model._asr_pipe` fallback below, which exists solely under
    # OMNIVOICE_PRELOAD_TTS_ASR — and when it is off, that branch raises "fallback
    # is not preloaded" anyway. Loading ~3 GB to reach a None attribute (and then
    # having offload_tts_for_asr free it) was pure cost.
    _model = await get_model() if should_preload_tts_asr() else None

    # TTS-only install: no ASR model on disk → typed 409 with a download CTA,
    # BEFORE any backend is constructed (the whisper backends auto-download
    # multi-GB weights from HF on first load). Same gate as the SSE preflight:
    # a preloaded `_asr_pipe` only substitutes for the *pytorch-whisper*
    # backend (its sole consumer), so it only skips the preflight there.
    from services.asr_backend import (
        active_backend_id,
        asr_model_missing_detail,
        asr_model_missing_error,
    )
    if not (getattr(_model, "_asr_pipe", None) is not None
            and active_backend_id() == "pytorch-whisper"):
        missing = await asyncio.to_thread(asr_model_missing_error)
        if missing is not None:
            raise HTTPException(
                status_code=409,
                detail={**missing, "message": asr_model_missing_detail(missing)},
            )

    def _transcribe():

        asr_audio_target = job.get("vocals_path")
        if not asr_audio_target or not os.path.exists(asr_audio_target):
            asr_audio_target = job.get("audio_path")
        # #963: same source-awareness as the SSE endpoint — vocals_path
        # falls back to the mixed audio_path when Demucs failed/skipped.
        asr_on_vocals = bool(asr_audio_target) and asr_audio_target != job.get("audio_path")

        import torch

        detected_lang = None

        # Route through services.asr_backend — picks WhisperX / faster-whisper
        # / mlx / pytorch based on what's installed + user preference. Works
        # identically on all platforms; the older mlx-vs-pytorch branching
        # here duplicated the logic in asr_backend.py and skipped WhisperX.
        # `load_*`, not `get_*`: the plain selector hands back engines whose
        # shallow probe passed but whose deep import chain is broken, which
        # then dies at `.transcribe()`. The loader degrades (#1185).
        from services.asr_backend import load_active_asr_backend
        _asr = load_active_asr_backend(asr_pipe=getattr(_model, "_asr_pipe", None))
        try:
            try:
                logger.info("Transcribing full audio via %s ...", _asr.id)
                result = _asr.transcribe(asr_audio_target, word_timestamps=True)
                detected_lang = result.get("language")
            except Exception as e:
                logger.exception("ASR backend %s failed", _asr.id)
                if getattr(_model, "_asr_pipe", None) is None:
                    raise RuntimeError(
                        f"ASR backend {_asr.id} failed and PyTorch Whisper fallback is not preloaded: {e}"
                    ) from e
                # Last-resort fallback — in-memory pytorch whisper via the TTS
                # model's pipeline when explicitly preloaded.
                audio_np, sr = sf.read(asr_audio_target, dtype="float32")
                if audio_np.ndim > 1: audio_np = audio_np.mean(axis=1)
                bs = 16 if torch.cuda.is_available() else 1
                result = _model._asr_pipe(
                    {"array": audio_np, "sampling_rate": sr},
                    return_timestamps=True, chunk_length_s=15, batch_size=bs,
                )
                detected_lang = (result.get("language") if isinstance(result, dict) else None)
        finally:
            try:
                _asr.unload()
            except Exception as e:
                logger.warning("Failed to unload ASR backend: %s", e)

        job["source_lang"] = job.get("source_lang_override") or _detected_source_lang(
            detected_lang
        )

        scene_cuts = job.get("scene_cuts") or []
        segments = segment_transcript(result, duration=job.get("duration", 0.0), scene_cuts=scene_cuts)

        # #280: snap segment starts forward to the actual speech onset so the
        # dub doesn't begin seconds before the original speaker does. #963:
        # only on the separated vocals track — on mixed audio every ambient
        # sound is a false onset candidate, so snapping is disabled.
        try:
            audio_for_onset, onset_sr = sf.read(asr_audio_target, dtype="float32")
            snap_segment_starts(segments, audio_for_onset, onset_sr,
                                separated_vocals=asr_on_vocals)
        except Exception as e:
            logger.warning("onset alignment skipped: %s", e)

        diar_pipe = get_diarization_pipeline()
        if diar_pipe:
            try:
                diar_target = job.get("vocals_path") or job.get("audio_path")
                # Same hint pass-through as the SSE endpoint (#274): omit the
                # kwarg entirely when unset so we don't depend on it existing
                # in every pyannote build.
                if num_speakers:
                    logger.info("Diarizing with num_speakers=%d (user hint)", num_speakers)
                    diarization = diar_pipe(diar_target, num_speakers=num_speakers)
                else:
                    diarization = diar_pipe(diar_target)
                segments = assign_speakers_from_diarization(segments, diarization)
            except Exception:
                logger.exception("Pyannote diarization failed during inference. Falling back to heuristic.")
                segments = assign_speakers_heuristic(segments, num_speakers)
        else:
            segments = assign_speakers_heuristic(segments, num_speakers)

        # Previously ran `segment_for_subtitles(segments)` here. Removed 2026-04-21 —
        # that splitter enforces Netflix's 17 CPS reading-speed ceiling which
        # trips on normal speech (15–25 CPS) and recurses to word-level.
        # For dubbing, keep the sentence-level output. Apply subtitle rules at
        # SRT export time only.

        for s in segments:
            s.setdefault("text_original", s.get("text", ""))
        job["full_transcript"] = " ".join(s["text"] for s in segments)

        # Transcription is done with the resident TTS model still offloaded;
        # release the accelerator cache the offload freed, on whichever
        # backend this host synthesizes with.
        release_device_cache()

        return segments

    try:
        loop = asyncio.get_running_loop()
        try:
            # Bound the whole-file transcribe (#730): a wedged whisperx/CTranslate2
            # call would otherwise hold its GPU-pool worker forever and starve
            # every other request into a "can't reach backend". run_transcribe_guarded
            # leaves an unkillable native worker accounted for on timeout so a
            # retry cannot overlap it (#1669).
            segments_result = await run_transcribe_guarded(_gpu_pool, _transcribe, what="Dub")
        except asyncio.CancelledError:
            job["aborted"] = True
            raise
        if job.get("aborted"):
            raise HTTPException(status_code=499, detail="Transcription aborted")
        from services.segmentation import deduplicate_chunk_segments
        segments_result = deduplicate_chunk_segments(segments_result)
        job["segments"] = segments_result
        source_lang = job.get("source_lang")
        _save_job(job_id, job)
        return {
            "job_id": job_id,
            "segments": segments_result,
            "full_transcript": job.get("full_transcript", ""),
            "source_lang": source_lang,
        }
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
