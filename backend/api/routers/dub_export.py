import asyncio
import io
import json
import logging
import ntpath
import os
import re
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Optional

from core.config import DUB_DIR
from core.http_headers import content_disposition
from core.logging_utils import log_safe
from core.path_security import UnsafePath, resolve_within
from core.tasks import task_manager
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from services.ffmpeg_utils import (
    bed_mix_filter,
    explain_ffmpeg_failure,
    find_ffmpeg,
    run_ffmpeg,
)
from services.karaoke_ass import build_ass, scale_words
from services.video_retime import (
    DRIFT_TOLERANCE_S,
    RetimeError,
    build_chunk_filter_graph,
    expand_retime_chunks,
    prepare_smart_fit_video,
)

from api.routers.dub_core import _get_job

router = APIRouter()
logger = logging.getLogger("omnivoice.api")


async def _preserved_background(job: dict, job_id: str, lang: str, *, prepare: bool = True) -> str:
    """All mixed preview/download paths share the same dialogue-only bed."""
    from services.dub_background import surgical_background

    bed = _optional_dub_artifact(job.get("no_vocals_path"), job_id)
    source = _optional_dub_artifact(job.get("video_path"), job_id) or _optional_dub_artifact(job.get("audio_path"), job_id)
    if not bed or not source:
        raise HTTPException(status_code=409, detail={"code": "dub_background_unavailable", "message": "Original audio and background separation are required"})
    track = (job.get("dubbed_tracks") or {}).get(lang) or {}
    segments = track.get("source_segments") or job.get("segments") or []
    if not segments:
        raise HTTPException(status_code=409, detail={"code": "dub_background_unavailable", "message": "Dialogue timing is required"})
    if not prepare:
        return bed
    strategy = track.get("timing_strategy") or job.get("timing_strategy")
    plans = job.get("fit_plans" if strategy == "smart_fit" else "video_stretch_plans") or {}
    entry = (plans.get(lang) or {}) if strategy in {"smart_fit", "stretch_video"} else {}
    directory = os.path.join(_existing_job_dir_or_404(job_id), "exports")
    os.makedirs(directory, exist_ok=True)
    try:
        return await surgical_background(source, bed, directory, segments, entry.get("plan") or [], float(entry.get("orig_duration") or job.get("duration") or 0))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail={"code": "dub_background_unavailable", "message": str(exc)}) from exc


def _unique_stamp() -> str:
    """Return a short unique suffix like '20260415T142301-ab12cd34' for export files."""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


_SAFE_LANG = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: Seconds of silence on a `/tasks/stream` before a keepalive comment goes out.
#: A task that is busy but quiet — ffmpeg on a long video, a slow TTS segment,
#: a job queued behind another — leaves the stream byte-silent, and byte-silent
#: SSE gets severed by the desktop webview, Chrome's ~5 min cap or a proxy's
#: idle timeout (#1196, #2108). Comments are invisible to every consumer.
TASK_STREAM_KEEPALIVE_S = 15.0


def _job_dir_or_400(job_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="Invalid job id")
    try:
        return str(resolve_within(DUB_DIR, job_id))
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc


def _existing_job_dir_or_404(job_id: str) -> str:
    """Discover a real job directory without passing request data to a path sink."""
    _job_dir_or_400(job_id)
    try:
        for entry in os.scandir(DUB_DIR):
            if entry.name == job_id and not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                return entry.path
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Job directory not found") from exc
    raise HTTPException(status_code=404, detail="Job directory not found")


def _resolve_dub_artifact(value: object, job_id: str) -> Path:
    """Resolve current or safely rebased pre-relocation dub artifact paths."""
    raw = str(value or "")
    try:
        resolved = resolve_within(DUB_DIR, raw)
        relative = resolved.relative_to(Path(DUB_DIR).resolve())
        if not relative.parts or relative.parts[0] != job_id:
            raise UnsafePath("Artifact does not belong to the requested job")
        return resolved
    except UnsafePath:
        # Older job rows store absolute paths. After the user relocates the
        # data directory, preserve only the suffix rooted at the exact
        # ``dub_jobs`` boundary; never touch the old host path itself.
        if ntpath.isabs(raw):
            parts = PureWindowsPath(raw).parts
        elif os.path.isabs(raw):
            parts = Path(raw).parts
        else:
            raise
        anchor = Path(DUB_DIR).name
        positions = [index for index, part in enumerate(parts) if part == anchor]
        if not positions:
            raise
        relative_parts = parts[positions[-1] + 1:]
        if (
            not relative_parts
            or relative_parts[0] != job_id
            or any(
                part in {"", ".", ".."}
                or "/" in part
                or "\\" in part
                or ":" in part
                for part in relative_parts
            )
        ):
            raise
        return resolve_within(DUB_DIR, Path(*relative_parts))


def _discover_job_artifact(path: Path, job_id: str) -> Path | None:
    """Return an existing artifact by walking the validated job directory.

    Persisted paths select names but never reach a filesystem sink. Each
    returned path comes from ``os.scandir`` beneath the validated job root,
    and symlinks are rejected so a post-validation swap cannot escape.
    """
    job_root = Path(_existing_job_dir_or_404(job_id)).resolve()
    try:
        parts = path.relative_to(job_root).parts
    except ValueError:
        return None
    if not parts:
        return None
    current = job_root
    for index, requested in enumerate(parts):
        if os.path.basename(requested) != requested or requested in {"", ".", ".."}:
            return None
        try:
            entry = next(
                (
                    item
                    for item in os.scandir(current)
                    if item.name == requested and not item.is_symlink()
                ),
                None,
            )
        except OSError:
            return None
        if entry is None:
            return None
        if index < len(parts) - 1 and not entry.is_dir(follow_symlinks=False):
            return None
        current = Path(entry.path)
    return current if current.is_file() else None


def _dub_artifact(value: object, job_id: str, *, missing_detail: str = "File not found") -> str:
    """Resolve a persisted job artifact inside the global dub-data boundary."""
    try:
        resolved = _resolve_dub_artifact(value, job_id)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid job artifact path") from exc
    path = _discover_job_artifact(resolved, job_id)
    if path is None:
        raise HTTPException(status_code=404, detail=missing_detail)
    return str(path)


def _optional_dub_artifact(value: object, job_id: str) -> str | None:
    if not value:
        return None
    try:
        resolved = _resolve_dub_artifact(value, job_id)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid job artifact path") from exc
    path = _discover_job_artifact(resolved, job_id)
    return str(path) if path is not None else None


def _safe_lang_or_400(lang: str | None) -> str | None:
    if lang is not None and not _SAFE_LANG.fullmatch(lang):
        raise HTTPException(status_code=400, detail="Invalid language code")
    return lang


def _consume_native_save(authorization: str) -> str | None:
    if not authorization:
        return None
    from core.path_authorization import PathAuthorizationError, consume

    try:
        return consume(authorization, "dub_export")
    except PathAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _native_save(source: str, destination: str, display_name: str, media_type: str):
    """Copy a generated export file to a user-chosen destination and return JSON."""
    import shutil
    dest = os.path.expanduser(destination)
    # Reject traversal against the user's home dir — Tauri save dialog returns abs path.
    if not os.path.isabs(dest):
        raise HTTPException(status_code=400, detail="save_path must be absolute")
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copy2(source, dest)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"Permission denied: {e}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Copy failed: {e}")
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise HTTPException(status_code=500, detail="Copy produced empty file at destination")
    logger.info("Native save completed (%d bytes)", os.path.getsize(dest))
    return {
        "saved": True,
        "path": dest,
        "size": os.path.getsize(dest),
        "media_type": media_type,
        "display_name": display_name,
    }

@router.get("/tasks/stream/{task_id}")
async def stream_task(task_id: str, after_seq: int = 0):
    """Universal Server-Sent Event stream for background tasks.

    `?after_seq=N` enables resumption: on reconnect, the client replays
    persisted events with seq > N, then (if the job is still live) attaches
    to the in-memory listener for live updates. After a server restart the
    in-memory task is gone but the persisted tail + final `jobs.status` are
    still readable, so a mid-stream reload still sees the final state.
    """
    from core import job_store
    job_row = job_store.get(task_id)
    live = task_manager.active_tasks.get(task_id)

    if not live and not job_row:
        raise HTTPException(
            status_code=404,
            detail="No such task. It may have been cleaned up or was never created.",
        )

    async def _reader():
        # 1) Replay any persisted events after the client's last-seen seq.
        try:
            persisted = job_store.events_since(task_id, after_seq=after_seq)
        except Exception:
            persisted = []
        for evt in persisted:
            yield evt["payload"]

        # 2) If the job has finished (whether in-memory or persisted-only), done.
        if not live:
            return
        if live["status"] in ("done", "failed", "cancelled"):
            return

        # 3) Attach to the in-memory listener for live updates.
        q = asyncio.Queue()
        await task_manager.add_listener(task_id, q)
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=TASK_STREAM_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if evt is None:
                    break
                yield evt
        finally:
            await task_manager.remove_listener(task_id, q)

    return StreamingResponse(
        _reader(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs")
async def list_jobs(status: str | None = None, project_id: str | None = None, limit: int = 100):
    """List persisted jobs, newest first.

    `status=active` → running + pending (what the batch-queue UI wants).
    `status=failed|done|cancelled|pending|running` → exact match.
    `project_id=...` → scope to one project.
    """
    from core import job_store
    limit = max(1, min(500, int(limit)))
    return job_store.list_jobs(status=status, project_id=project_id, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    from core import job_store
    row = job_store.get(job_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No such job. It may have been cleaned up or never created.",
        )
    return row


@router.get("/jobs/{job_id}/events")
async def list_job_events(job_id: str, after_seq: int = 0, limit: int = 500):
    """Persisted SSE tail. Strict ascending seq so the client can stitch
    it onto a live feed (which starts above the last returned seq).
    """
    from core import job_store
    row = job_store.get(job_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No job with that id. It may have expired, been deleted, or the server restarted before it was persisted — check the dub history in the sidebar.",
        )
    limit = max(1, min(2000, int(limit)))
    return {
        "job": row,
        "events": job_store.events_since(job_id, after_seq=after_seq, limit=limit),
    }


@router.post("/tasks/cancel/{task_id}")
async def cancel_task(task_id: str):
    """Cancel a running background task (e.g. dub generation)."""
    ok = task_manager.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"cancelled": True, "task_id": task_id}


@router.get("/dub/tracks/{job_id}")
async def dub_list_tracks(job_id: str):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"tracks": job.get("dubbed_tracks", {})}


@router.get("/dub/segments-text/{job_id}")
async def dub_segments_text(job_id: str, lang: str = Query(...)):
    """Per-segment texts for one generated track: ``{"texts": {segKey: text}}``.

    Backing store is ``job["segments_i18n"]`` (P1.2) — the authoritative
    per-language map every generate rebuilds. The Export preview tabs use it
    to hydrate segments whose in-browser ``translations[lang]`` entry is
    missing (tracks generated before per-language persistence, partial
    regens), so switching the preview language can't leave a mixed-language
    transcript. Empty map when the job predates segments_i18n or the track
    was never generated — the client keeps whatever it has.
    """
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    i18n = job.get("segments_i18n") or {}
    return {"texts": i18n.get(lang) or {}}


def _segments_for_lang(job: dict, lang: "str | None") -> list:
    """Job segments with `text` overlaid from ``job["segments_i18n"][lang]``.

    P1.2 — ``job["segments"]`` is single-slot: it holds whichever language was
    generated LAST, so exporting subtitles for track A after generating track B
    emitted B's text under A's language label (the "N identical subtitle
    files" class). ``segments_i18n`` ({lang: {segKey: text}}, written by
    ``dub_generate._sync_job_segments``) preserves each generated track's text;
    this overlays it non-destructively when present.

    Back-compat: no lang requested, no ``segments_i18n`` on the job (predates
    the field), no entry for this lang, or no text for a given segment — each
    falls back to the segment as-is, i.e. exactly today's behaviour.
    Segment keys are the stable id (str) with the list index (str) as the
    legacy fallback, mirroring how the map is written.
    """
    segments = job.get("segments", [])
    if not lang:
        return segments
    i18n = job.get("segments_i18n")
    lang_texts = i18n.get(lang) if isinstance(i18n, dict) else None
    if not isinstance(lang_texts, dict) or not lang_texts:
        return segments
    from services.srt_parser import vouch_cue_source
    # Jobs generated before #2295 have no vouch map and keep the legacy
    # equal-text reuse; newer ones reuse a cue only for its vouched track.
    cue_maps = job.get("segments_i18n_cue_sources")
    lang_cues = cue_maps.get(lang) if isinstance(cue_maps, dict) else None
    out = []
    for i, seg in enumerate(segments):
        key = str(seg.get("id")) if seg.get("id") is not None else str(i)
        txt = lang_texts.get(key)
        if txt is None:
            txt = lang_texts.get(str(i))
            key = str(i)
        if isinstance(txt, str) and txt.strip():
            # Imported cue markup belongs to this track only if this track's
            # generate carried the import's own text (#2295).
            row = dict(seg, text=txt)
            if isinstance(cue_maps, dict):
                cues = lang_cues if isinstance(lang_cues, dict) else {}
                vouch_cue_source(row, txt, cues.get(key))
            out.append(row)
        else:
            out.append(seg)
    return out


def _write_burn_srt(job: dict, exports_dir: str, stamp: str, dual: bool,
                    fitted_segments: "list[dict] | None" = None,
                    lang: "str | None" = None) -> str | None:
    """Build a temp SRT from job segments for use with ffmpeg's subtitles filter.

    Returned path is already ffmpeg-filter-safe (plain ASCII basename under exports_dir).
    Returns None if there are no segments to render.

    ``fitted_segments`` (Smart Fit): {id, start, end} cue records on the
    fitted timeline — when provided, cue times come from there instead of
    the original ``job["segments"]`` timings, so burned subs track the
    retimed video / fitted audio rather than the source timeline.

    ``lang`` (P1.2): burn the named track's text (see ``_segments_for_lang``)
    instead of whatever language generated last.
    """
    segments = _segments_for_lang(job, lang)
    if not segments:
        return None
    if fitted_segments:
        segments = _apply_fitted_times(segments, fitted_segments)
    lines = []
    for i, seg in enumerate(segments):
        lines.append(str(i + 1))
        lines.append(f"{_format_srt_time(seg['start'])} --> {_format_srt_time(seg['end'])}")
        lines.append(_pick_subtitle_text(seg, dual, escape=_srt_cue_text(seg)))
        lines.append("")
    sub_path = os.path.join(exports_dir, f"burn_subs_{stamp}.srt")
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return sub_path


def _write_burn_ass(job: dict, exports_dir: str, stamp: str,
                    fitted_segments: "list[dict] | None" = None,
                    lang: "str | None" = None) -> str | None:
    """Karaoke variant of ``_write_burn_srt``: word-timed ASS via ``build_ass``.

    Same text/timing resolution (``_segments_for_lang`` + fitted-cue overlay,
    which also scales per-word times onto the fitted timeline); the basename
    is plain ASCII under exports_dir so it is ffmpeg-filter-safe. Returns
    None if there are no segments to render.
    """
    segments = _segments_for_lang(job, lang)
    if not segments:
        return None
    if fitted_segments:
        segments = _apply_fitted_times(segments, fitted_segments)
    sub_path = os.path.join(exports_dir, f"burn_subs_{stamp}.ass")
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write(build_ass(segments))
    return sub_path


def _ffmpeg_filter_escape(path: str) -> str:
    """Escape a path for use inside a quoted ffmpeg filter value (subtitles=/ass=).

    ffmpeg's filter parser treats ``:`` as an option separator. The subtitles
    and ass filters also fail to open Windows backslash paths: doubling every
    ``\\`` and then escaping the drive colon produced ``C\\:\\Users\\...``
    inside the quotes, which is not a real file, so burned-in captions were
    missing on Windows. Use forward slashes for Windows paths only; a POSIX
    backslash is part of the filename and needs escaping at both parser levels.
    Apostrophes close/reopen quotes and likewise escape for both parsers.
    """
    normalized = str(path)
    if os.name == "nt" or re.match(r"^[A-Za-z]:[\\/]", normalized):
        normalized = normalized.replace("\\", "/")
    return normalized.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'" + "\\" * 3 + "''")


def _build_video_stretch_filter_graph(
    plan: list[dict], orig_dur: float, video_input_idx: int = 0,
    in_label: str | None = None,
) -> tuple[str, str]:
    """Build an ffmpeg filter_complex graph that stretches the source video
    per-segment so each segment's visual duration matches a dub audio layout.

    `plan` is a list of {orig_start, orig_end, new_start, new_end, stretch_ratio}
    in original-time order (as persisted by dub_generate for stretch_video
    mode). Gaps between plan entries — and the pre-roll / tail — are passed
    through at 1.0× rate so silence and B-roll don't get squashed.

    `in_label` overrides the input stream reference; e.g. pass "[vsub]" when
    a subtitles filter has already written to that label. Defaults to
    `[{video_input_idx}:v]` for direct source-stream consumption.

    Returns (filter_graph, output_label). Output label is "[vstretched]" when
    chunks were emitted, or the original input label when the plan was empty
    (caller should fall back to stream-copy in that case).
    """
    # Empty plan = no stretch — caller should stream-copy the video. Return
    # early so we don't synthesise a degenerate "stretch whole video at 1.0×"
    # graph that would force a needless re-encode.
    if not plan:
        return "", in_label or f"[{video_input_idx}:v]"

    # Chunk expansion + graph emission live in services.video_retime now so
    # the Smart Fit batched pipeline shares the exact same boundary math.
    # With default options the emitted graph is byte-identical to the
    # original inline implementation.
    chunks = expand_retime_chunks(plan, orig_dur)
    if not chunks:
        return "", in_label or f"[{video_input_idx}:v]"
    return build_chunk_filter_graph(chunks, in_label or f"[{video_input_idx}:v]")


def _video_stretch_plan_for(job: dict, lang_code: str) -> dict | None:
    """Return the persisted stretch plan + total durations for `lang_code`,
    or None if this job didn't use stretch_video mode (or no plan exists).
    """
    if (job.get("timing_strategy") or "").lower() != "stretch_video":
        return None
    plans = job.get("video_stretch_plans") or {}
    entry = plans.get(lang_code)
    if not entry or not entry.get("plan"):
        return None
    return entry


def _video_retime_plan_for(job: dict, lang_code: str) -> "tuple[str, dict] | None":
    """Resolve the video retime plan for ``lang_code`` across both keyspaces.

    Returns ``(kind, entry)`` where kind is ``"stretch_video"`` (legacy
    Mode B plans — resolution byte-identical to ``_video_stretch_plan_for``)
    or ``"smart_fit"`` (Phase A ``job["fit_plans"]`` entries, gated on the
    track actually having been generated under smart_fit so a stale plan
    from an earlier run can't retime a track re-generated under another
    strategy). ``None`` when neither applies.
    """
    legacy = _video_stretch_plan_for(job, lang_code)
    if legacy is not None:
        return "stretch_video", legacy
    entry = (job.get("fit_plans") or {}).get(lang_code)
    track = (job.get("dubbed_tracks") or {}).get(lang_code) or {}
    if entry and entry.get("plan") and track.get("timing_strategy") == "smart_fit":
        return "smart_fit", entry
    return None


def _fitted_segments_for(job: dict, lang_code: "str | None") -> "list[dict] | None":
    """Fitted-timeline subtitle cues ({id, start, end}) for a Smart Fit
    track, or None. Same staleness gate as ``_video_retime_plan_for``."""
    if not lang_code:
        return None
    entry = (job.get("fit_plans") or {}).get(lang_code)
    track = (job.get("dubbed_tracks") or {}).get(lang_code) or {}
    if not entry or track.get("timing_strategy") != "smart_fit":
        return None
    fitted = entry.get("fitted_segments")
    return fitted or None


def _apply_fitted_times(segments: list[dict], fitted: list[dict]) -> list[dict]:
    """Overlay fitted cue times onto subtitle segments (copies; non-destructive).

    Matches by segment ``id``; when the fitted record carries no ids at all
    (defensive), falls back to positional pairing. Segments without a match
    keep their original timings.
    """
    by_id = {str(f["id"]): f for f in fitted if f.get("id") is not None}
    out: list[dict] = []
    for i, seg in enumerate(segments):
        cue = None
        if seg.get("id") is not None:
            cue = by_id.get(str(seg["id"]))
        if cue is None and not by_id and i < len(fitted):
            cue = fitted[i]
        if cue is None:
            out.append(seg)
            continue
        patched = dict(seg)
        patched["start"] = float(cue["start"])
        patched["end"] = float(cue["end"])
        # Karaoke burn-in: persisted word times live on the original timeline;
        # scale them linearly onto the fitted cue span so the highlight sweep
        # follows the retimed audio. Degenerate spans drop the words — export
        # then falls back to an even split over the fitted span. Inert for
        # SRT/VTT, which never read ``words``.
        if isinstance(seg.get("words"), list) and seg.get("words"):
            scaled = scale_words(
                seg["words"], seg.get("start", 0.0), seg.get("end", 0.0),
                patched["start"], patched["end"],
            )
            if scaled is not None:
                patched["words"] = scaled
            else:
                patched.pop("words", None)
        out.append(patched)
    return out


def _burn_subs_allowed(retime_kind: "str | None") -> bool:
    """Subtitle burn-in combined with video retime.

    Allowed for ``smart_fit`` (fitted cue records exist, and the burn pass
    runs AFTER the retime graph so cues land on the retimed timeline) and
    for plain exports. Still rejected for legacy ``stretch_video``, which
    has no fitted-cue record — cues would burn at original timestamps onto
    a re-timed video and drift.
    """
    return retime_kind != "stretch_video"


#: Audio export formats → ffmpeg codec args. Unknown formats fall back to
#: AAC/m4a so a bad request can never produce a broken command.
_AUDIO_FORMAT_CODECS: dict[str, list[str]] = {
    "wav": ["-c:a", "pcm_s16le"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "flac": ["-c:a", "flac"],
}


def _build_audio_export_cmd(
    ffmpeg: str,
    track_path: str,
    bg_path: Optional[str],
    out_path: str,
    fmt: str,
) -> list[str]:
    """Build the ffmpeg command for an audio-only dub export (#119).

    No video input, stream-map, or codec — just the dubbed track, optionally
    mixed with the separated background (``no_vocals``), written to ``out_path``
    in the requested ``fmt``. Unknown formats fall back to AAC.
    """
    codec = _AUDIO_FORMAT_CODECS.get((fmt or "").lower(), _AUDIO_FORMAT_CODECS["m4a"])
    cmd = [ffmpeg, "-y", "-i", track_path]
    if bg_path:
        # Mix the dubbed voice over the original background bed (same weights
        # as the video mux path) so ambience/music is preserved.
        cmd += ["-i", bg_path, "-filter_complex",
                bed_mix_filter("1:a", "0:a", bed_gain=1.0),
                "-map", "[aout]"]
    cmd += codec
    cmd.append(out_path)
    return cmd


@router.get("/dub/download/{job_id}")
@router.get("/dub/download/{job_id}/{filename}")
async def dub_download(
    job_id: str,
    preserve_bg: bool = Query(True, description="Mix background noise into dubbed tracks"),
    default_track: str = Query("", description="Default audio track; omitted selects the first dubbed track"),
    include_tracks: str = Query("", description="Comma-separated list of tracks to include (e.g. 'original,de,es'). Empty = include all."),
    save_authorization: str = Header("", alias="X-VoiceStudio-Path-Authorization"),
    burn_subs: bool = Query(False, description="Burn subtitles into the video stream (forces re-encode). Uses dual-subtitle layout when dual=1."),
    dual: bool = Query(False, description="When burn_subs=1, render translated on top of italicised original."),
    karaoke: bool = Query(False, description="When burn_subs=1, burn a word-timed karaoke highlight (ASS) instead of line subtitles. Ignored when dual=1 (dual karaoke is unsupported — the line burn renders instead)."),
    out_format: str = Query("m4a", description="Audio-only jobs (#119): output container — wav, m4a, mp3, or flac. Ignored for video jobs."),
):
    # Strict allowlist on the path param BEFORE it reaches any filesystem
    # path or ffmpeg argv (export dir, retime work path, slice paths). Real
    # job ids are short uuid slices — alnum/hyphen/underscore only.
    job_dir = _job_dir_or_400(job_id)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracks = job.get("dubbed_tracks", {})
    if not tracks:
        raise HTTPException(status_code=400, detail="No dubbed tracks generated yet")

    include_set = set(t.strip() for t in include_tracks.split(",") if t.strip()) if include_tracks else None
    include_original = include_set is None or "original" in include_set

    if include_set:
        filtered_tracks = {k: v for k, v in tracks.items() if k in include_set}
    else:
        filtered_tracks = dict(tracks)

    filtered_tracks = {
        key: {
            **value,
            "path": _dub_artifact(value.get("path"), job_id, missing_detail="Dubbed track not found"),
        }
        for key, value in filtered_tracks.items()
    }

    # A dub export should play the dub without requiring player-specific track
    # selection. Keep ``original`` as an explicit opt-in, but when callers omit
    # the preference choose the first generated dub consistently (#1575).
    if (
        filtered_tracks
        and not (default_track == "original" and include_original)
        and default_track not in filtered_tracks
    ):
        default_track = next(iter(filtered_tracks))
    elif not filtered_tracks and include_original:
        default_track = "original"

    if not filtered_tracks and not include_original:
        raise HTTPException(status_code=400, detail="No tracks selected for export")

    video_path = _dub_artifact(job["video_path"], job_id, missing_detail="Source video not found")
    stamp = _unique_stamp()
    exports_dir = os.path.join(job_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    output_path = os.path.join(exports_dir, f"dubbed_video_{stamp}.mp4")
    ffmpeg = find_ffmpeg()

    # ── Audio-only dubbing (#119) ─────────────────────────────────────────
    # No source video to mux into — export the dubbed track (optionally mixed
    # with the separated background) straight to an audio container.
    if (job.get("input_type") or "video").lower() == "audio":
        if default_track and default_track != "original" and default_track in filtered_tracks:
            lang_code, track_info = default_track, filtered_tracks[default_track]
        elif filtered_tracks:
            lang_code, track_info = next(iter(filtered_tracks.items()))
        else:
            raise HTTPException(status_code=400, detail="No dubbed track selected for audio export")

        fmt = (out_format or "m4a").lower()
        if fmt not in _AUDIO_FORMAT_CODECS:
            fmt = "m4a"
        # Keep route/job data out of the filesystem and logging trust boundary.
        # The selected format reaches the path only through literal branches.
        if fmt == "wav":
            output_name = f"dubbed_audio_{stamp}.wav"
        elif fmt == "mp3":
            output_name = f"dubbed_audio_{stamp}.mp3"
        elif fmt == "flac":
            output_name = f"dubbed_audio_{stamp}.flac"
        else:
            output_name = f"dubbed_audio_{stamp}.m4a"
        out_path = os.path.join(exports_dir, output_name)
        bg = await _preserved_background(job, job_id, lang_code) if preserve_bg else None
        cmd = _build_audio_export_cmd(ffmpeg, track_info["path"], bg, out_path, fmt)
        try:
            rc, _, stderr = await run_ffmpeg(cmd, timeout=1800.0)
            if rc != 0:
                raise Exception(stderr.decode(errors="replace") if stderr else "ffmpeg audio export non-zero")
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="ffmpeg audio export timed out")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=explain_ffmpeg_failure(e, "export dubbed audio", cmd=cmd),
            )
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise HTTPException(status_code=500, detail="ffmpeg audio export produced no output file")
        logger.info("Dub audio export completed (%d bytes)", os.path.getsize(out_path))

        # Response metadata must not become a second path-like sink for job or
        # request data. Keep the user-selected format through explicit literal
        # branches; source names and language keys never enter the label.
        if fmt == "wav":
            dl_name = f"dubbed_audio_{stamp}.wav"
        elif fmt == "mp3":
            dl_name = f"dubbed_audio_{stamp}.mp3"
        elif fmt == "flac":
            dl_name = f"dubbed_audio_{stamp}.flac"
        else:
            dl_name = f"dubbed_audio_{stamp}.m4a"
        media_type = _MEDIA_TYPES.get(f".{fmt}", "audio/mp4")
        save_path = _consume_native_save(save_authorization)
        if save_path:
            # Keep the request-derived download label out of the filesystem
            # trust boundary. It is response metadata, not a source or
            # destination path (CodeQL, #1575).
            result = _native_save(out_path, save_path, "dubbed_audio", media_type=media_type)
            result["display_name"] = dl_name
            return result
        return FileResponse(
            out_path, media_type=media_type,
            headers={"Content-Disposition": content_disposition(dl_name)},
        )

    # Determine whether this export should drive video through a per-segment
    # retime (legacy stretch_video Mode B, or Smart Fit). Retime is keyed off
    # the default_track's plan because the video can only physically follow
    # one timeline at a time. If multiple dub tracks are included, only the
    # default_track is visually in sync — other tracks share the same
    # (retimed) video. Single-track export is the supported common case.
    retime_kind: "str | None" = None
    retime_entry: "dict | None" = None
    if default_track and default_track != "original":
        _retime = _video_retime_plan_for(job, default_track)
        if _retime:
            retime_kind, retime_entry = _retime
    stretch_entry = retime_entry if retime_kind == "stretch_video" else None
    # Subtitle burn under legacy stretch_video would render cues at the
    # original timestamps onto a re-timed video — they'd drift (no fitted-cue
    # record exists for that mode). Skip the burn pass in that combo and log;
    # the user can still export the SRT/VTT separately. Smart Fit DOES carry
    # fitted cues, so burn+retime is allowed there (burn runs post-retime).
    if not _burn_subs_allowed(retime_kind) and burn_subs:
        logger.warning(
            "stretch_video + burn_subs is not supported in one pass; "
            "skipping subtitle burn for job %s. Export the SRT/VTT separately.",
            log_safe(job_id),
        )
        burn_subs = False

    # Smart Fit: cue times come from the fitted timeline — that's where the
    # dubbed audio actually sits, whether or not the video retime succeeds.
    fitted_segments = _fitted_segments_for(job, default_track) if default_track and default_track != "original" else None
    # Burn the DEFAULT track's text (P1.2) — it's the audio the viewer hears.
    _burn_lang = default_track if default_track and default_track != "original" else None
    # Karaoke (word-highlight) burn writes an ASS instead of the line SRT.
    # Dual layout keeps the line burn — dual karaoke is out of scope, matching
    # the disabled control in the Export drawer. The default (karaoke off)
    # takes exactly the legacy SRT path.
    sub_path = None
    sub_is_ass = False
    if burn_subs:
        if karaoke and not dual:
            sub_path = _write_burn_ass(job, exports_dir, stamp, fitted_segments=fitted_segments, lang=_burn_lang)
            sub_is_ass = sub_path is not None
        if sub_path is None:
            sub_path = _write_burn_srt(job, exports_dir, stamp, dual, fitted_segments=fitted_segments, lang=_burn_lang)

    # ── Smart Fit video retime (two-tier) ─────────────────────────────────
    # Tier 1 (≤48 chunks): single filter_complex graph inlined into the mux
    # command below. Tier 2: batched slice renders joined by the concat
    # demuxer into an intermediate file, muxed as an extra input. Failures
    # fall back to an un-retimed export with a structured warning rather
    # than failing the whole download.
    retime_decision = None
    retime_warning: "dict | None" = None
    smart_track_dur = 0.0
    if retime_kind == "smart_fit" and retime_entry:
        smart_orig_dur = float(retime_entry.get("orig_duration") or job.get("duration") or 0.0)
        smart_track_dur = float(
            retime_entry.get("total_duration")
            or (filtered_tracks.get(default_track) or {}).get("duration")
            or 0.0
        )
        # A fresh export is a fresh user intent — clear any sticky abort flag
        # from a previous /dub/abort so it can't kill this run's first batch.
        job.pop("aborted", None)
        # realpath-normalised + containment-checked inline at the sink (the
        # file's established pattern — CodeQL does not track the guard
        # through a helper's return value).
        _base = os.path.realpath(DUB_DIR)
        retime_work_path = os.path.realpath(
            os.path.join(exports_dir, f"retimed_{stamp}.mp4")
        )
        if retime_work_path != _base and not retime_work_path.startswith(_base + os.sep):
            raise HTTPException(status_code=400, detail="Invalid export path")
        try:
            retime_decision = await prepare_smart_fit_video(
                job_id=job_id,
                ffmpeg=ffmpeg,
                video_path=video_path,
                plan=retime_entry["plan"],
                orig_dur=smart_orig_dur,
                track_dur=smart_track_dur,
                work_path=retime_work_path,
                abort_check=lambda: bool(job.get("aborted")),
            )
        except Exception as e:
            if (isinstance(e, RetimeError) and e.stage == "aborted") or job.get("aborted"):
                raise HTTPException(status_code=409, detail="Export aborted")
            from core.failure import build_failure
            retime_warning = build_failure(e, stage="video-retime", include_diagnostic=False)
            job["last_export_warning"] = {"type": "video_retime_fallback", **retime_warning}
            logger.exception(
                "Smart Fit video retime failed for job %s — exporting "
                "without per-segment retime",
                log_safe(job_id),
            )

    cmd = [ffmpeg, "-i", video_path]
    input_idx = 1

    retimed_idx = None
    if retime_decision is not None and retime_decision.mode == "file":
        cmd += ["-i", retime_decision.file_path]
        retimed_idx = input_idx
        input_idx += 1

    bg_idx = None
    tracks_to_process = []
    for lang_code, track_info in filtered_tracks.items():
        if preserve_bg:
            bg_audio = await _preserved_background(job, job_id, lang_code)
            cmd += ["-i", bg_audio]
            bg_idx = input_idx
            input_idx += 1
        cmd += ["-i", track_info["path"]]
        tracks_to_process.append({"lang_code": lang_code, "idx": input_idx, "bg_idx": bg_idx, "info": track_info})
        input_idx += 1

    filter_parts: list[str] = []
    video_map = "0:v:0"
    video_reencode = False
    if retime_decision is not None:
        if retime_decision.mode == "filter":
            filter_parts.append(retime_decision.graph)
            video_map = retime_decision.label
            video_reencode = True
        else:
            video_map = f"{retimed_idx}:v:0"
            # Residual drift after the batched render (fps rounding): video
            # shorter than the fitted track → freeze the last frame out to
            # the track length. Rare — the predicted tail pad inside the
            # render usually lands within tolerance.
            residual = smart_track_dur - retime_decision.video_dur
            if smart_track_dur and residual > DRIFT_TOLERANCE_S:
                filter_parts.append(
                    f"[{retimed_idx}:v]tpad=stop_mode=clone:stop_duration={residual:.4f}[vtpad]"
                )
                video_map = "[vtpad]"
                video_reencode = True
    if sub_path:
        esc = _ffmpeg_filter_escape(sub_path)
        # Burn AFTER any retime so cues (already on the fitted timeline for
        # Smart Fit) land on the retimed video. Without retime this reduces
        # to the legacy `[0:v]subtitles=…[vsub]` graph. Karaoke burns the
        # word-timed ASS through the ass filter at the same graph position.
        if video_map.startswith("["):
            sub_src = video_map
        elif retimed_idx is not None:
            sub_src = f"[{retimed_idx}:v]"
        else:
            sub_src = "[0:v]"
        _sub_filter = "ass" if sub_is_ass else "subtitles"
        filter_parts.append(f"{sub_src}{_sub_filter}='{esc}'[vsub]")
        video_map = "[vsub]"
    if stretch_entry:
        orig_dur = float(stretch_entry.get("orig_duration") or job.get("duration") or 0.0)
        graph, vlabel = _build_video_stretch_filter_graph(
            stretch_entry["plan"], orig_dur, video_input_idx=0,
            in_label=video_map if video_map != "0:v:0" else None,
        )
        if graph:
            filter_parts.append(graph)
            video_map = vlabel

    cmd += ["-map", video_map]
    if include_original:
        cmd += ["-map", "0:a:0"]

    # Smart Fit drift absorption, audio side: when the retimed video runs
    # longer than the fitted track (its tail passes through at 1.0× beyond
    # the last cue, or encoder rounding), pad the dub-track chain with
    # silence out to the video length so players don't end audio early.
    apad_dur = 0.0
    if (
        retime_decision is not None
        and smart_track_dur
        and retime_decision.video_dur - smart_track_dur > DRIFT_TOLERANCE_S
    ):
        apad_dur = retime_decision.video_dur

    if bg_idx is not None:
        for i, t in enumerate(tracks_to_process):
            tail = f",apad=whole_dur={apad_dur:.4f}" if apad_dur else ""
            filter_parts.append(bed_mix_filter(
                f"{t['bg_idx']}:a", f"{t['idx']}:a", out=f"aout{i}", tail=tail, uniq=str(i), bed_gain=1.0,
            ))
            t["out_label"] = f"[aout{i}]"
        for t in tracks_to_process:
            cmd += ["-map", t["out_label"]]
    elif apad_dur:
        for i, t in enumerate(tracks_to_process):
            out_label = f"[aout{i}]"
            filter_parts.append(f"[{t['idx']}:a]apad=whole_dur={apad_dur:.4f}{out_label}")
            t["out_label"] = out_label
        for t in tracks_to_process:
            cmd += ["-map", t["out_label"]]
    else:
        for t in tracks_to_process:
            cmd += ["-map", f"{t['idx']}:a:0"]

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts)]

    # Burning subs or per-segment video retime both force a real video
    # re-encode; stream-copy is viable when nothing touches the video
    # filter chain — including the batched Smart Fit path, whose retimed
    # intermediate is already encoded with these exact settings.
    if sub_path or stretch_entry or video_reencode:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]

    audio_stream_idx = 0
    if include_original:
        cmd += [f"-metadata:s:a:{audio_stream_idx}", "language=und", f"-metadata:s:a:{audio_stream_idx}", "title=Original"]
        audio_stream_idx += 1

    for t in tracks_to_process:
        cmd += [
            f"-metadata:s:a:{audio_stream_idx}", f"language={t['lang_code']}",
            f"-metadata:s:a:{audio_stream_idx}", f"title={t['info']['language']}"
        ]
        t["stream_idx"] = audio_stream_idx
        audio_stream_idx += 1

    total_audio = (1 if include_original else 0) + len(tracks_to_process)
    for i in range(total_audio):
        cmd += [f"-disposition:a:{i}", "0"]

    if default_track == "original" and include_original:
        cmd += ["-disposition:a:0", "default"]
    else:
        # A stale/missing language preference still means "play a dub", not
        # "silently fall back to the source". The first processed dub is the
        # deterministic fallback; ``original`` above remains explicit.
        target_idx = tracks_to_process[0]["stream_idx"] if tracks_to_process else 0
        for t in tracks_to_process:
            if t['lang_code'] == default_track:
                target_idx = t["stream_idx"]
                break
        cmd += [f"-disposition:a:{target_idx}", "default"]

    # When retiming (legacy stretch_video or Smart Fit) the video and audio
    # durations should match within sub-frame precision, but `-shortest`
    # can still cut off the trailing frame; let ffmpeg keep both streams.
    # Otherwise keep the legacy `-shortest` so a slightly-overrunning track
    # doesn't extend the mux past the video.
    if not stretch_entry and retime_decision is None:
        cmd += ["-shortest"]
    cmd += [output_path, "-y"]

    try:
        rc, _, stderr = await run_ffmpeg(cmd, timeout=1800.0, job_id=job_id)
        if rc != 0:
            raise Exception(stderr.decode(errors="replace") if stderr else "ffmpeg mux non-zero")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ffmpeg mux timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=explain_ffmpeg_failure(e, "combine video + dubbed audio", cmd=cmd),
        )
    finally:
        # The batched retime intermediate is a full re-encoded video — never
        # leave it behind (success or failure; it's stamp-unique, no reuse).
        if retime_decision is not None and retime_decision.mode == "file":
            try:
                os.remove(retime_decision.file_path)
            except OSError as e:
                logger.debug("cleanup remove failed: %s", e)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise HTTPException(status_code=500, detail="ffmpeg mux produced no output file")
    logger.info("Dub mux completed (%d bytes)", os.path.getsize(output_path))

    base_name = os.path.splitext(job.get('filename', 'output'))[0]
    safe_name = ''.join(c for c in base_name if c.isalnum() or c in '-_ ').strip() or 'output'
    dl_name = f"dubbed_{safe_name}_{stamp}.mp4"

    # Structured warning surface for the Smart Fit fallback ladder: header is
    # a fixed ASCII token (FileResponse headers must be latin-1 safe); the
    # full build_failure payload is persisted on the job for the UI to read.
    extra_headers = {}
    if retime_warning is not None:
        extra_headers["X-Dub-Export-Warning"] = "video-retime-fallback"

    save_path = _consume_native_save(save_authorization)
    if save_path:
        result = _native_save(output_path, save_path, dl_name, media_type="video/mp4")
        if retime_warning is not None:
            result["warning"] = {"type": "video_retime_fallback", **retime_warning}
        return result

    return FileResponse(
        output_path, media_type="video/mp4",
        headers={"Content-Disposition": content_disposition(dl_name), **extra_headers},
    )


_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


@router.api_route("/dub/media/{job_id}", methods=["GET", "HEAD"])
async def dub_get_media(job_id: str, request: Request):
    _job_dir_or_400(job_id)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    video_path = _dub_artifact(job["video_path"], job_id, missing_detail="Media file not found")
    # Pass an explicit media_type. Without this Starlette falls back to
    # mimetypes.guess_type, which on some platforms returns the wrong
    # MIME (e.g. "application/octet-stream" for .mkv), and the Tauri
    # WebView then refuses to render the <video> element — leaving a
    # silent black box. Default to video/mp4 because the ingest pipeline
    # remuxes URL downloads to mp4 (dub_pipeline.yt_download_sync).
    ext = os.path.splitext(video_path)[1].lower()
    media_type = _MEDIA_TYPES.get(ext, "video/mp4")
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
    }
    if request.method == "HEAD":
        headers["Content-Length"] = str(os.path.getsize(video_path))
        return Response(media_type=media_type, headers=headers)
    return FileResponse(video_path, media_type=media_type, headers=headers)

# One mux at a time per preview file. Without this, two overlapping requests
# (e.g. the <video> element remounting right after a re-dub) both ran ffmpeg
# against the same output path, and the mtime check below saw the half-written
# file as a valid cache — serving a truncated MP4 that left the player stuck
# loading forever (#281).
_preview_mux_locks: dict[str, asyncio.Lock] = {}


def _preview_lock(path: str) -> asyncio.Lock:
    lock = _preview_mux_locks.get(path)
    if lock is None:
        lock = _preview_mux_locks.setdefault(path, asyncio.Lock())
    return lock


@router.api_route("/dub/preview-video/{job_id}", methods=["GET", "HEAD"])
async def dub_preview_video(
    request: Request,
    job_id: str,
    lang: str = Query(..., description="Language code of the dubbed track to mux in"),
    preserve_bg: bool = Query(True),
):
    """Return an inline-playable MP4 with the chosen dubbed track as sole audio.

    Caches per lang+preserve_bg combination under exports/preview_{lang}_{bg}.mp4.
    Cache is invalidated when the underlying dubbed track mtime is newer than the cache.
    """
    # Strict allowlist on the path param BEFORE it reaches any filesystem
    # path or ffmpeg argv (exports dir, preview/retime work paths) — same
    # boundary check as dub_download.
    job_dir = _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracks = job.get("dubbed_tracks", {})
    track_info = tracks.get(lang)
    if not track_info:
        raise HTTPException(status_code=404, detail=f"No dubbed track for lang={lang}")

    track_path = _dub_artifact(track_info.get("path"), job_id, missing_detail="Dubbed track file missing")

    video_path = _dub_artifact(job.get("video_path"), job_id, missing_detail="Source video missing")

    bg_audio = await _preserved_background(job, job_id, lang, prepare=request.method != "HEAD") if preserve_bg else None
    has_bg = bool(bg_audio)

    # realpath-normalised + containment-checked inline BEFORE any filesystem
    # access so the guard dominates every sink (the file's established
    # pattern — see dub_preview_segment; CodeQL does not track the guard
    # through a helper's return value).
    _base = os.path.realpath(DUB_DIR)
    exports_dir = os.path.realpath(os.path.join(job_dir, "exports"))
    if not exports_dir.startswith(_base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid job id")
    os.makedirs(exports_dir, exist_ok=True)
    bg_suffix = "surgical_v2_" + Path(bg_audio).stem if (preserve_bg and has_bg) else "nobg"
    preview_path = os.path.realpath(
        os.path.join(exports_dir, f"preview_v2_{lang}_{bg_suffix}.mp4")
    )
    if not preview_path.startswith(_base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")

    track_mtime = os.path.getmtime(track_path)

    def _cache_ok() -> bool:
        return (
            os.path.exists(preview_path)
            and os.path.getsize(preview_path) > 0
            and os.path.getmtime(preview_path) >= track_mtime
        )

    # Vidstack probes extensionless routes with HEAD before choosing a native
    # provider. Confirm that this preview is valid without starting an ffmpeg
    # mux; the following GET builds it lazily when needed.
    if request.method == "HEAD":
        headers = {
            "Cache-Control": "private, max-age=31536000, immutable",
            "Accept-Ranges": "bytes",
        }
        if _cache_ok():
            headers["Content-Length"] = str(os.path.getsize(preview_path))
        return Response(media_type="video/mp4", headers=headers)

    async def _mux_preview():
        # Mux into a temp file and os.replace() into place so a concurrent
        # reader never sees a partially-written preview (#281: video stuck
        # loading forever after a re-dub).
        mux_path = preview_path + ".tmp.mp4"
        ffmpeg = find_ffmpeg()
        # Resolve the same retime plan the download path uses (legacy
        # stretch_video or Smart Fit) so the in-app preview matches export.
        retime = _video_retime_plan_for(job, lang)
        retime_kind, retime_entry = retime if retime else (None, None)
        stretch_entry = retime_entry if retime_kind == "stretch_video" else None

        retime_decision = None
        smart_track_dur = 0.0
        if retime_kind == "smart_fit" and retime_entry:
            smart_orig_dur = float(retime_entry.get("orig_duration") or job.get("duration") or 0.0)
            smart_track_dur = float(
                retime_entry.get("total_duration") or track_info.get("duration") or 0.0
            )
            job.pop("aborted", None)  # fresh user intent — clear sticky abort
            # realpath-normalised + containment-checked inline at the sink
            # (same pattern as preview_path above — _base is the realpath
            # of DUB_DIR from the top of this endpoint).
            retime_work_path = os.path.realpath(os.path.join(
                exports_dir, f"preview_retimed_{lang}_{bg_suffix}.tmp.mp4",
            ))
            if retime_work_path != _base and not retime_work_path.startswith(_base + os.sep):
                raise HTTPException(status_code=400, detail="Invalid export path")
            try:
                retime_decision = await prepare_smart_fit_video(
                    job_id=job_id,
                    ffmpeg=ffmpeg,
                    video_path=video_path,
                    plan=retime_entry["plan"],
                    orig_dur=smart_orig_dur,
                    track_dur=smart_track_dur,
                    work_path=retime_work_path,
                    abort_check=lambda: bool(job.get("aborted")),
                )
            except Exception as e:
                if (isinstance(e, RetimeError) and e.stage == "aborted") or job.get("aborted"):
                    raise HTTPException(status_code=409, detail="Preview aborted")
                # Preview is best-effort: fall back to the un-retimed video
                # rather than a black player. The export path surfaces the
                # structured warning; here we just log.
                retime_decision = None
                logger.exception(
                    "Smart Fit preview retime failed for job %s — previewing "
                    "without per-segment retime",
                    log_safe(job_id),
                )

        cmd = [ffmpeg, "-i", video_path]
        input_idx = 1
        retimed_idx = None
        if retime_decision is not None and retime_decision.mode == "file":
            cmd += ["-i", retime_decision.file_path]
            retimed_idx = input_idx
            input_idx += 1
        if preserve_bg and has_bg:
            cmd += ["-i", bg_audio]
            bg_idx = input_idx
            input_idx += 1
        else:
            bg_idx = None
        cmd += ["-i", track_path]
        track_idx = input_idx

        # Build filter graph. Under a retime plan we splice the source video
        # into per-segment chunks, setpts each to match the dub audio
        # layout, and concat them — so audio plays at natural rate and the
        # visuals follow. Otherwise we stream-copy video for speed.
        filter_parts: list[str] = []
        video_map = "0:v:0"
        video_reencode = False
        if stretch_entry:
            orig_dur = float(stretch_entry.get("orig_duration") or job.get("duration") or 0.0)
            graph, vlabel = _build_video_stretch_filter_graph(
                stretch_entry["plan"], orig_dur, video_input_idx=0,
            )
            if graph:
                filter_parts.append(graph)
                video_map = vlabel
        elif retime_decision is not None:
            if retime_decision.mode == "filter":
                filter_parts.append(retime_decision.graph)
                video_map = retime_decision.label
                video_reencode = True
            else:
                video_map = f"{retimed_idx}:v:0"
                residual = smart_track_dur - retime_decision.video_dur
                if smart_track_dur and residual > DRIFT_TOLERANCE_S:
                    filter_parts.append(
                        f"[{retimed_idx}:v]tpad=stop_mode=clone:stop_duration={residual:.4f}[vtpad]"
                    )
                    video_map = "[vtpad]"
                    video_reencode = True

        # Smart Fit drift absorption (audio): silence-pad the dub chain out
        # to the retimed video length so the preview doesn't end audio early.
        apad_dur = 0.0
        if (
            retime_decision is not None
            and smart_track_dur
            and retime_decision.video_dur - smart_track_dur > DRIFT_TOLERANCE_S
        ):
            apad_dur = retime_decision.video_dur

        audio_map = f"{track_idx}:a:0"
        if bg_idx is not None:
            tail = f",apad=whole_dur={apad_dur:.4f}" if apad_dur else ""
            filter_parts.append(bed_mix_filter(f"{bg_idx}:a", f"{track_idx}:a", tail=tail, bed_gain=1.0))
            audio_map = "[aout]"
        elif apad_dur:
            filter_parts.append(f"[{track_idx}:a]apad=whole_dur={apad_dur:.4f}[aout]")
            audio_map = "[aout]"

        cmd += ["-map", video_map]
        cmd += ["-map", audio_map]
        if filter_parts:
            cmd += ["-filter_complex", ";".join(filter_parts)]

        # Retime path needs a real encode; stream-copy otherwise (the batched
        # Smart Fit intermediate is already encoded — copy unless tpad'ed).
        if stretch_entry or video_reencode:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        # `-shortest` would cut the retimed video at the (slightly different)
        # audio length and lose the trailing frame; only use it on the copy path.
        if not stretch_entry and retime_decision is None:
            cmd += ["-shortest"]
        cmd += [mux_path, "-y"]

        def _discard_tmp():
            try:
                os.remove(mux_path)
            except OSError as e:
                logger.debug("cleanup remove failed: %s", e)

        try:
            rc, _, stderr = await run_ffmpeg(cmd, timeout=900.0, job_id=job_id)
            if rc != 0:
                raise Exception(stderr.decode(errors="replace") if stderr else "ffmpeg mux non-zero")
            if not os.path.exists(mux_path) or os.path.getsize(mux_path) == 0:
                raise Exception("preview mux produced empty file")
        except asyncio.TimeoutError:
            _discard_tmp()
            raise HTTPException(status_code=504, detail="preview mux timed out")
        except HTTPException:
            _discard_tmp()
            raise
        except Exception as e:
            _discard_tmp()
            raise HTTPException(
                status_code=500,
                detail=f"ffmpeg failed to build the preview stream: {str(e)[:300]}. This usually means the source video can't be re-encoded on the fly — try downloading the MP4 instead.",
            )
        finally:
            if retime_decision is not None and retime_decision.mode == "file":
                try:
                    os.remove(retime_decision.file_path)
                except OSError as e:
                    # Best-effort scratch cleanup — never fail the export.
                    logger.debug("retime intermediate cleanup failed: %s", e)

        os.replace(mux_path, preview_path)

    async with _preview_lock(preview_path):
        if not _cache_ok():
            await _mux_preview()

    # The renderer includes the segment-fingerprint revision in the URL, so a
    # regenerated track gets a fresh cache key. Keep each completed preview:
    # switching Original/Dub then reuses local ranges instead of re-reading a
    # multi-hundred-megabyte MP4 from the backend.
    return FileResponse(
        preview_path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=31536000, immutable", "Accept-Ranges": "bytes"},
    )


def _compute_timeline_sync(src_path: str) -> tuple[list[float], list[float]]:
    """Blocking part of onset analysis — runs in a worker thread."""
    import numpy as np
    import soundfile as sf
    from services.onset_align import detect_speech_onsets
    audio, sr = sf.read(src_path, dtype="float32")
    onsets = detect_speech_onsets(audio, sr)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    mono = mono.reshape(-1)
    if mono.size == 0:
        return onsets, []
    bucket_count = min(2048, int(mono.size))
    bucket_width = max(1, (int(mono.size) + bucket_count - 1) // bucket_count)
    padded_size = bucket_count * bucket_width
    if padded_size != mono.size:
        mono = np.pad(mono, (0, padded_size - int(mono.size)))
    peaks = np.max(np.abs(mono.reshape(bucket_count, bucket_width)), axis=1)
    return onsets, [round(float(value), 5) for value in peaks]


@router.get("/dub/onsets/{job_id}")
async def dub_get_onsets(job_id: str):
    """Speech-onset times for the timeline editor's snap-to-onset ticks (#280).

    Prefers the Demucs-isolated vocals track (clean speech energy); falls
    back to the mixed audio. Computed once per job and cached as
    ``onsets.json`` in the job directory; recomputed if the source audio is
    newer than the cache (e.g. re-ingest into the same job dir).
    """
    job_dir = _job_dir_or_400(job_id)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    vocals = _optional_dub_artifact(job.get("vocals_path"), job_id)
    mix = _optional_dub_artifact(job.get("audio_path"), job_id)
    if vocals:
        src_path, source = vocals, "vocals"
    elif mix:
        src_path, source = mix, "mix"
    else:
        raise HTTPException(status_code=404, detail="No audio track available for onset analysis")

    # Containment inlined (not via _safe_job_path): CodeQL can't track the
    # sanitizer through a helper's return — the file's established idiom.
    base = os.path.realpath(DUB_DIR)
    cache_path = os.path.realpath(os.path.join(job_dir, "onsets.json"))
    if not cache_path.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid job id")
    try:
        if (
            os.path.exists(cache_path)
            and os.path.getmtime(cache_path) >= os.path.getmtime(src_path)
        ):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if (
                isinstance(cached, dict)
                and isinstance(cached.get("onsets"), list)
                and isinstance(cached.get("peaks"), list)
            ):
                return cached
    except (OSError, ValueError):
        pass  # unreadable/corrupt cache → recompute below

    try:
        onsets, peaks = await asyncio.to_thread(_compute_timeline_sync, src_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Onset analysis failed: {str(e)[:200]}",
        )

    payload = {"onsets": onsets, "peaks": peaks, "source": source}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, cache_path)
    except OSError:
        logger.warning("onsets cache write failed")
    return payload


@router.get("/dub/thumb/{job_id}")
async def dub_get_thumb(job_id: str):
    """Serve the extracted dub video thumbnail (jpg). 404 if not generated."""
    job_dir = _job_dir_or_400(job_id)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Resolve under DUB_DIR to prevent traversal.
    thumb = os.path.join(job_dir, "thumb.jpg")
    if not os.path.exists(thumb):
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

@router.get("/dub/audio/{job_id}")
async def dub_get_audio(job_id: str):
    _job_dir_or_400(job_id)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    audio = _dub_artifact(job.get("audio_path"), job_id, missing_detail="Audio file not found")
    return FileResponse(audio, media_type="audio/wav")

def _seg_wav_candidates(job: dict, lang: "str | None", seg_keys: tuple) -> list:
    """Per-segment WAV name candidates, language-keyed first (P1.3).

    Generation writes ``seg_{lang}_{id}.wav`` now; ``lang`` defaults to the
    job's last-generated track. Legacy un-keyed names (``seg_{id}.wav`` /
    ``seg_{index}.wav``) stay as fallbacks so jobs rendered by previous
    builds keep serving their audio — these read-only endpoints keep the
    permissive fallback that matches their historic behaviour (the strict
    single-track gate lives on the generate splice path, where a wrong-
    language read would be baked into a track).
    """
    lang = lang or job.get("language_code")
    keys = []
    if lang:
        keys.extend(f"{lang}_{k}" for k in seg_keys)
    keys.extend(seg_keys)
    return keys


def _existing_segment_artifact(job_id: str, candidate_ids: list) -> str | None:
    """Discover an existing, non-symlink segment WAV inside one job root."""
    job_root = Path(_existing_job_dir_or_404(job_id))
    wanted: list[str] = []
    for value in candidate_ids:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(value))
        if safe:
            wanted.append(f"seg_{safe}.wav")
    try:
        entries = {
            entry.name: entry
            for entry in os.scandir(job_root)
            if not entry.is_symlink() and entry.is_file(follow_symlinks=False)
        }
    except OSError:
        return None
    for name in wanted:
        entry = entries.get(name)
        if entry is not None:
            return entry.path
    return None


@router.get("/dub/preview/{job_id}/{segment_index}")
async def dub_preview_segment(job_id: str, segment_index: int, lang: str = Query(None)):
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Resolve the stable-id-named WAV via the render manifest — language-keyed
    # name first (P1.3), then the legacy id/index names for jobs rendered
    # before per-language (and before id-based, #185) naming. Each candidate
    # is realpath-normalised and containment-checked BEFORE any filesystem
    # access, and discovery returns only a non-symlink entry from that root.
    order = job.get("seg_order") or []
    seg_id = order[segment_index] if 0 <= segment_index < len(order) else segment_index
    seg_path = _existing_segment_artifact(
        job_id, _seg_wav_candidates(job, lang, (seg_id, segment_index))
    )
    if not seg_path:
        raise HTTPException(status_code=404, detail="Segment not generated yet")
    return FileResponse(seg_path, media_type="audio/wav")


# ── Second-pass ASR QC (Wave 3.3 / Spec 5) ───────────────────────────────────


@router.post("/dub/qc/{job_id}")
async def dub_qc_pass(job_id: str, lang: str = Query(None), drift_threshold: float = Query(0.5)):
    """Re-recognize the dubbed audio and flag lines whose recognized text
    drifts from the target text. Opt-in, never fatal: the dub is untouched —
    this only annotates segments with a per-line drift score and a measured
    start/end, surfaced as "verify this line" markers feeding incremental
    re-dub. The generated text stays authoritative (design delta from
    pyvideotrans, which overwrites subtitles)."""
    from services import dub_qc
    from services.dub_pipeline import put_job, save_job

    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tracks = job.get("dubbed_tracks", {})
    if lang and lang in tracks:
        wav_path = _dub_artifact(tracks[lang].get("path"), job_id, missing_detail="Dubbed audio file not found")
    elif tracks:
        wav_path = _dub_artifact(list(tracks.values())[0].get("path"), job_id, missing_detail="Dubbed audio file not found")
    else:
        raise HTTPException(status_code=400, detail="No dubbed audio track generated yet")
    segments = job.get("segments") or []
    if not segments:
        raise HTTPException(status_code=400, detail="Job has no segments")

    # TTS-only install: no ASR model on disk → typed 409 with a download CTA,
    # BEFORE any backend load could silently auto-download whisper weights.
    from services.asr_backend import asr_model_missing_detail, asr_model_missing_error
    missing = await asyncio.to_thread(asr_model_missing_error)
    if missing is not None:
        raise HTTPException(
            status_code=409,
            detail={**missing, "message": asr_model_missing_detail(missing)},
        )

    def _recognize():
        # `load_*`, not `get_*`: the plain selector returns engines whose
        # shallow probe passed but whose deep import chain is broken, which
        # then 500s at `.transcribe()`. The loader degrades (#1185).
        from services.asr_backend import load_active_asr_backend
        backend = load_active_asr_backend()
        result = backend.transcribe(wav_path, word_timestamps=False)
        return result.get("segments", []), backend.id

    try:
        from services.asr_backend import (
            ASRModelMissingError,
            ASRTimeoutError,
            run_transcribe_guarded,
        )
        from services.model_manager import _get_gpu_pool
        recognized, engine_id = await run_transcribe_guarded(
            _get_gpu_pool(), _recognize, what="QC",
        )
    except ASRTimeoutError as e:
        # Backend is alive; ASR just couldn't finish in time. 504, not 500/connection.
        logger.warning("dub QC ASR pass timed out")
        raise HTTPException(status_code=504, detail=str(e))
    except ASRModelMissingError as e:
        # Degraded onto an engine with no weights on disk — typed 409 with the
        # download CTA, matching the preflight above.
        raise HTTPException(
            status_code=409,
            detail={**e.payload, "message": asr_model_missing_detail(e.payload)},
        )
    except Exception as e:
        logger.exception("dub QC ASR pass failed")
        raise HTTPException(status_code=500, detail=f"QC transcription failed: {e}")

    seg_ids = job.get("seg_order") or [s.get("id", i) for i, s in enumerate(segments)]
    scored = dub_qc.score_dub(segments, recognized, drift_threshold=drift_threshold, seg_ids=seg_ids)

    # Annotate each segment (non-destructive — content text untouched).
    by_id = {q.seg_id: q for q in scored}
    for i, s in enumerate(segments):
        sid = str(seg_ids[i]) if i < len(seg_ids) else str(s.get("id", i))
        q = by_id.get(sid)
        if q is None:
            continue
        s["qc_drift"] = q.drift
        s["qc_flagged"] = q.flagged
        s["qc_recognized"] = q.recognized_text
        if q.new_start is not None:
            s["qc_measured_start"] = q.new_start
            s["qc_measured_end"] = q.new_end
    put_job(job_id, job)
    save_job(job_id, job)

    flagged = [q for q in scored if q.flagged]
    payload = json.dumps({"event": "qc_done", "engine": engine_id,
                          "flagged": len(flagged), "total": len(scored)})
    try:
        from core import job_store
        job_store.append_event(job_id, f"data: {payload}\n\n")
    except Exception:
        # QC event fan-out is best-effort; the scores are already in the response.
        logger.debug("QC event append failed")

    return {
        "engine": engine_id,
        "total": len(scored),
        "flagged_count": len(flagged),
        "drift_threshold": drift_threshold,
        "segments": [
            {"seg_id": q.seg_id, "drift": q.drift, "flagged": q.flagged,
             "recognized_text": q.recognized_text,
             "measured_start": q.new_start, "measured_end": q.new_end}
            for q in scored
        ],
    }


@router.get("/dub/download-audio/{job_id}")
@router.get("/dub/download-audio/{job_id}/{filename}")
async def dub_download_audio(
    job_id: str,
    lang: str = Query(None),
    preserve_bg: bool = Query(True),
    save_authorization: str = Header("", alias="X-VoiceStudio-Path-Authorization"),
):
    job_dir = _existing_job_dir_or_404(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracks = job.get("dubbed_tracks", {})
    if lang and lang in tracks:
        wav_path = _dub_artifact(tracks[lang].get("path"), job_id, missing_detail="Audio file not found")
    elif tracks:
        wav_path = _dub_artifact(list(tracks.values())[0].get("path"), job_id, missing_detail="Audio file not found")
    else:
        raise HTTPException(status_code=400, detail="No dubbed audio track generated yet")

    lang_label = lang or list(tracks.keys())[0]
    _safe_lang_or_400(lang_label)
    stamp = _unique_stamp()
    exports_dir = os.path.join(job_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)

    bg_audio = await _preserved_background(job, job_id, lang_label) if preserve_bg else None
    if bg_audio:
        ffmpeg = find_ffmpeg()
        final_audio_path = os.path.join(exports_dir, f"mixed_dub_{stamp}.wav")
        cmd = [
            ffmpeg, "-i", bg_audio, "-i", wav_path,
            "-filter_complex", bed_mix_filter("0:a", "1:a", bed_gain=1.0),
            "-map", "[aout]", "-c:a", "pcm_s16le", "-y", final_audio_path
        ]
        try:
            rc, _, stderr = await run_ffmpeg(cmd, timeout=900.0)
            if rc != 0:
                raise Exception(stderr.decode(errors="replace") if stderr else "ffmpeg mix non-zero")
            if not os.path.exists(final_audio_path) or os.path.getsize(final_audio_path) == 0:
                raise Exception("ffmpeg mix produced no output file")
            wav_path = final_audio_path
            logger.info("Dub audio mix completed")
        except Exception as exc:
            logger.exception("Failed to mix audio")
            raise HTTPException(status_code=500, detail={"code": "dub_background_unavailable", "message": "Could not preserve background audio"}) from exc

    base_name = os.path.splitext(job.get('filename', 'audio'))[0]
    safe_name = ''.join(c for c in base_name if c.isalnum() or c in '-_ ').strip() or 'audio'
    dl_name = f"dubbed_audio_{lang_label}_{safe_name}_{stamp}.wav"
    save_path = _consume_native_save(save_authorization)
    if save_path:
        return _native_save(wav_path, save_path, dl_name, media_type="audio/wav")
    return FileResponse(
        wav_path, media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": content_disposition(dl_name),
        },
    )


def _format_srt_time(seconds):
    from services.srt_parser import format_cue_timestamp
    return format_cue_timestamp(seconds, ",")

def _srt_cue_text(seg: dict):
    """SRT writer for ``seg``: an unchanged imported cue keeps its markup."""
    from services.srt_parser import source_cue_or
    return lambda text: source_cue_or(seg, text, "srt_source")


def _pick_subtitle_text(seg: dict, dual: bool, escape=lambda text: text) -> str:
    """One line per subtitle cue, unless dual=true and an original exists.

    Dual layout stacks translated text on top of the (italicised) original, the
    way Netflix / language-learning apps present them:

        Das Spiel wirklich zu verändern.
        <i>Actually change the game.</i>

    ``escape`` applies to the text only, so the ``<i>`` stays markup.
    """
    translated = (seg.get("text") or "").strip()
    original = (seg.get("text_original") or "").strip()
    if not dual or not original or original == translated:
        return escape(translated or original)
    return f"{escape(translated)}\n<i>{escape(original)}</i>"


# Subtitles deliberately have no ?save_path= variant: they're small text
# bodies, so the Tauri side fetches them raw and writes the file itself via
# the save_text_file command — the OS save dialog is the write authorization
# (#309). The frontend's JSON-envelope save flow stays for binary exports.


def _fitted_cue_times(job: dict, lang: str | None) -> list | None:
    """Per-segment (start, end) on the fitted timeline when this job used
    stretch_video; None to use the original segment times. (Wave 3.1.)"""
    tracks = job.get("dubbed_tracks", {})
    lc = lang if (lang and lang in tracks) else (next(iter(tracks), None))
    entry = _video_stretch_plan_for(job, lc) if lc else None
    if not entry:
        return None
    from services.fitted_subtitles import fitted_cues
    return fitted_cues(job.get("segments", []), entry["plan"])


@router.get("/dub/srt/{job_id}")
@router.get("/dub/srt/{job_id}/{filename}")
async def dub_export_srt(
    job_id: str,
    dual: bool = False,
    lang: str = Query(None, description="Track language code. Emits that track's text (segments_i18n) when the job carries it; when that track was generated under Smart Fit or stretch_video, cue times come from the fitted timeline."),
):
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # P1.2 — text follows the REQUESTED track, not whichever language was
    # generated last (job["segments"] is single-slot). Legacy jobs without
    # segments_i18n fall back to today's behaviour.
    segments = _segments_for_lang(job, lang)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments available")

    # Subtitles must follow the audio the viewer hears, per timing strategy:
    # Smart Fit (phase B) overlays the fitted segment times directly;
    # stretch_video (Wave 3.1) regenerates cue times from the stretch plan.
    # Neither applies → original times.
    fitted = _fitted_segments_for(job, lang)
    if fitted:
        segments = _apply_fitted_times(segments, fitted)
    cues = None if fitted else _fitted_cue_times(job, lang)

    srt_lines = []
    for i, seg in enumerate(segments):
        s, e = cues[i] if cues else (seg["start"], seg["end"])
        srt_lines.append(f"{i + 1}")
        srt_lines.append(f"{_format_srt_time(s)} --> {_format_srt_time(e)}")
        srt_lines.append(_pick_subtitle_text(seg, dual, escape=_srt_cue_text(seg)))
        srt_lines.append("")

    srt_content = "\n".join(srt_lines)
    base_name = os.path.splitext(job.get('filename', 'video'))[0]
    suffix = "_dual" if dual else ""
    dl_name = f"subtitles_{base_name}{suffix}.srt"
    return Response(
        content=srt_content,
        media_type="text/plain",
        headers={"Content-Disposition": content_disposition(dl_name)},
    )

def _format_vtt_time(seconds):
    from services.srt_parser import format_cue_timestamp
    return format_cue_timestamp(seconds, ".")

@router.get("/dub/vtt/{job_id}")
@router.get("/dub/vtt/{job_id}/{filename}")
async def dub_export_vtt(
    job_id: str,
    dual: bool = False,
    lang: str = Query(None, description="Track language code. Emits that track's text (segments_i18n) when the job carries it; when that track was generated under Smart Fit or stretch_video, cue times come from the fitted timeline."),
):
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Same per-track text resolution as /dub/srt (see comment there, P1.2).
    segments = _segments_for_lang(job, lang)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments available")

    # Same strategy-aware cue timing as /dub/srt (see comment there).
    fitted = _fitted_segments_for(job, lang)
    if fitted:
        segments = _apply_fitted_times(segments, fitted)
    cues = None if fitted else _fitted_cue_times(job, lang)

    from services.srt_parser import escape_webvtt_text, source_cue_or, srt_cue_to_webvtt

    vtt_lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments):
        s, e = cues[i] if cues else (seg["start"], seg["end"])
        vtt_lines.append(str(i + 1))
        vtt_lines.append(f"{_format_vtt_time(s)} --> {_format_vtt_time(e)}")
        # Reuse imported cue syntax only while the corresponding text is unchanged.
        # This distinguishes literal &lt;i&gt; from genuine <i> markup and survives
        # persistence; older projects retain the legacy markup interpretation.
        def escape_text(text, seg=seg):
            if "webvtt_source" in seg:
                return escape_webvtt_text(source_cue_or(seg, text, "webvtt_source"))
            if "srt_source" in seg:
                # Imported SubRip text follows SubRip rules: its `<i>` is
                # markup, `a<b and c>d` is dialogue.
                return srt_cue_to_webvtt(source_cue_or(seg, text, "srt_source"))
            return escape_webvtt_text(text)
        vtt_lines.append(_pick_subtitle_text(seg, dual, escape=escape_text))
        vtt_lines.append("")

    vtt_content = "\n".join(vtt_lines)
    base_name = os.path.splitext(job.get('filename', 'video'))[0]
    suffix = "_dual" if dual else ""
    dl_name = f"subtitles_{base_name}{suffix}.vtt"
    return Response(
        content=vtt_content,
        media_type="text/vtt",
        headers={"Content-Disposition": content_disposition(dl_name)},
    )


@router.get("/dub/ass/{job_id}")
@router.get("/dub/ass/{job_id}/{filename}")
async def dub_export_ass(
    job_id: str,
    lang: str = Query(None, description="Track language code. Same text/timing resolution as /dub/srt, rendered as a karaoke (word-highlight) ASS sidecar."),
):
    """Karaoke ASS sidecar — the same script the karaoke burn-in renders.

    Raw text body like /dub/srt and /dub/vtt (the Tauri side writes the file
    itself; no ?save_path= variant — see the comment above /dub/srt).
    """
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    segments = _segments_for_lang(job, lang)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments available")

    # Same strategy-aware cue timing as /dub/srt. The fitted overlay also
    # scales word times; the stretch_video cue path has no per-word record,
    # so words are dropped and build_ass even-splits over the new spans.
    fitted = _fitted_segments_for(job, lang)
    if fitted:
        segments = _apply_fitted_times(segments, fitted)
    else:
        cues = _fitted_cue_times(job, lang)
        if cues:
            segments = [
                {**{k: v for k, v in seg.items() if k != "words"}, "start": s, "end": e}
                for seg, (s, e) in zip(segments, cues)
            ]

    base_name = os.path.splitext(job.get('filename', 'video'))[0]
    dl_name = f"subtitles_{base_name}_karaoke.ass"
    return Response(
        content=build_ass(segments),
        media_type="text/plain",
        headers={"Content-Disposition": content_disposition(dl_name)},
    )


@router.get("/dub/export-segments/{job_id}")
async def dub_export_segments_zip(job_id: str, lang: str = Query(None)):
    import zipfile
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    segments = job.get("segments", [])
    if not segments:
        raise HTTPException(status_code=400, detail="No segments available")

    zip_buffer = io.BytesIO()
    order = job.get("seg_order") or []
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, seg in enumerate(segments):
            seg_id = order[i] if i < len(order) else i
            # Discovery returns only a non-symlink entry from the validated job root.
            seg_path = _existing_segment_artifact(
                job_id, _seg_wav_candidates(job, lang, (seg_id, i))
            )
            if seg_path:
                speaker = seg.get("speaker_id", "Speaker1").replace(" ", "")
                start_str = f"{seg['start']:.2f}"
                end_str = f"{seg['end']:.2f}"
                arc_name = f"{i+1:03d}_{start_str}-{end_str}_{speaker}.wav"
                zf.write(seg_path, arc_name)

    zip_buffer.seek(0)
    base_name = os.path.splitext(job.get('filename', 'video'))[0]
    safe_name = ''.join(c for c in base_name if c.isalnum() or c in '-_ ').strip() or 'segments'
    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"segments_{safe_name}.zip")},
    )

@router.get("/dub/download-mp3/{job_id}")
@router.get("/dub/download-mp3/{job_id}/{filename}")
async def dub_download_mp3(
    job_id: str,
    lang: str = Query(None),
    preserve_bg: bool = Query(True),
    save_authorization: str = Header("", alias="X-VoiceStudio-Path-Authorization"),
    bitrate: str = Query("192k"),
):
    job_dir = _existing_job_dir_or_404(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracks = job.get("dubbed_tracks", {})
    if lang and lang in tracks:
        wav_path = _dub_artifact(tracks[lang].get("path"), job_id, missing_detail="Audio file not found")
    elif tracks:
        wav_path = _dub_artifact(list(tracks.values())[0].get("path"), job_id, missing_detail="Audio file not found")
    else:
        raise HTTPException(status_code=400, detail="No dubbed audio track generated yet")

    lang_label = lang or list(tracks.keys())[0]
    _safe_lang_or_400(lang_label)
    ffmpeg = find_ffmpeg()
    stamp = _unique_stamp()
    exports_dir = os.path.join(job_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)

    source_path = wav_path
    bg_audio = await _preserved_background(job, job_id, lang_label) if preserve_bg else None
    if bg_audio:
        mixed_path = os.path.join(exports_dir, f"mixed_mp3_{stamp}.wav")
        cmd_mix = [
            ffmpeg, "-i", bg_audio, "-i", wav_path,
            "-filter_complex", bed_mix_filter("0:a", "1:a", bed_gain=1.0),
            "-map", "[aout]", "-c:a", "pcm_s16le", "-y", mixed_path
        ]
        try:
            rc, _, _ = await run_ffmpeg(cmd_mix, timeout=900.0)
            if rc == 0 and os.path.exists(mixed_path) and os.path.getsize(mixed_path) > 0:
                source_path = mixed_path
            else:
                raise RuntimeError("Background mixing failed")
        except Exception as exc:
            logger.exception("Failed to mix audio for MP3")
            raise HTTPException(status_code=500, detail={"code": "dub_background_unavailable", "message": "Could not preserve background audio"}) from exc

    mp3_path = os.path.join(exports_dir, f"dubbed_{stamp}.mp3")
    # Accept '128', '192k' etc. — normalize to ffmpeg's 'Nk' form and clamp
    # to a sensible range so a malformed value can't stall encoding.
    _br = str(bitrate or "192k").lower().rstrip("k") or "192"
    try:
        _br_int = max(64, min(int(_br), 320))
    except ValueError:
        _br_int = 192
    br_arg = f"{_br_int}k"
    cmd = [ffmpeg, "-i", source_path, "-codec:a", "libmp3lame", "-b:a", br_arg, "-y", mp3_path]
    try:
        rc, _, stderr = await run_ffmpeg(cmd, timeout=600.0)
        if rc != 0:
            raise Exception(stderr.decode(errors="replace") if stderr else "MP3 encode non-zero")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="MP3 encoding timed out")
    except HTTPException:
        raise
    except Exception as e:
        detail = explain_ffmpeg_failure(e, "encode MP3", cmd=cmd)
        if not isinstance(e, OSError):
            # ffmpeg ran and failed: for MP3 the classic cause is a build
            # without libmp3lame — keep that hint for the ran-and-failed case.
            detail += " If the error mentions libmp3lame, your ffmpeg build lacks the MP3 encoder (`ffmpeg -codecs | grep mp3`)."
        raise HTTPException(status_code=500, detail=detail)

    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
        raise HTTPException(status_code=500, detail="MP3 encoding produced no output file")
    logger.info("Dub MP3 encoding completed")

    base_name = os.path.splitext(job.get('filename', 'audio'))[0]
    safe_name = ''.join(c for c in base_name if c.isalnum() or c in '-_ ').strip() or 'audio'
    dl_name = f"dubbed_{lang_label}_{safe_name}_{stamp}.mp3"
    save_path = _consume_native_save(save_authorization)
    if save_path:
        return _native_save(mp3_path, save_path, dl_name, media_type="audio/mpeg")
    return FileResponse(
        mp3_path, media_type="audio/mpeg",
        headers={"Content-Disposition": content_disposition(dl_name)},
    )

@router.get("/dub/export-stems/{job_id}")
async def dub_export_stems(job_id: str, lang: str = Query(None)):
    import zipfile
    _job_dir_or_400(job_id)
    lang = _safe_lang_or_400(lang)
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracks = job.get("dubbed_tracks", {})
    if not tracks:
        raise HTTPException(status_code=400, detail="No dubbed tracks generated yet")

    if lang and lang in tracks:
        vocals_path = _dub_artifact(tracks[lang].get("path"), job_id, missing_detail="Dubbed audio file not found")
        lang_label = lang
    elif tracks:
        first_key = list(tracks.keys())[0]
        _safe_lang_or_400(first_key)
        vocals_path = _dub_artifact(tracks[first_key].get("path"), job_id, missing_detail="Dubbed audio file not found")
        lang_label = first_key
    else:
        raise HTTPException(status_code=400, detail="No dubbed audio track")

    bg_path = _optional_dub_artifact(job.get("no_vocals_path"), job_id)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(vocals_path, f"vocals_dubbed_{lang_label}.wav")
        if bg_path:
            zf.write(bg_path, "background_original.wav")

    zip_buffer.seek(0)
    base_name = os.path.splitext(job.get('filename', 'video'))[0]
    safe_name = ''.join(c for c in base_name if c.isalnum() or c in '-_ ').strip() or 'stems'
    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"stems_{safe_name}.zip")},
    )
