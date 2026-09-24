import asyncio
import contextlib
import logging
import os
import re
import uuid
import weakref
import time
import shutil
from typing import Optional
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core.db import db_conn
from core.config import VOICES_DIR, OUTPUTS_DIR
from core import event_bus
from core.personalities import get_personalities
from omnivoice.utils.voice_design import heal_design_instruct, sanitize_instruct
from core.path_security import UnsafePath, resolve_within
from core.profile_images import MAX_IMAGE_BYTES, normalize_portrait
from starlette.datastructures import UploadFile as StarletteUploadFile

router = APIRouter()
logger = logging.getLogger("omnivoice.profiles")


def _profile_record(row):
    result = dict(row)
    image_path = _voices_path(f"{result['id']}.portrait.jpg")
    result["image_url"] = (
        f"/profiles/{result['id']}/image?v={os.stat(image_path).st_mtime_ns}"
        if image_path and os.path.isfile(image_path) else None
    )
    # Cache-busting playback URL for the clip GET /profiles/{id}/audio serves.
    # The stable /audio route is unchanged; the version token changes whenever
    # the reference is replaced (#2282), so players and HTTP caches never keep
    # the previous take. None when no sample exists yet (pending design voice).
    audio_file = result.get("locked_audio_path") or result.get("ref_audio_path")
    audio_path = _voices_path(str(audio_file)) if audio_file else None
    result["audio_url"] = (
        f"/profiles/{result['id']}/audio?v={os.stat(audio_path).st_mtime_ns}"
        if audio_path and os.path.isfile(audio_path) else None
    )
    return result


async def _auto_transcribe_reference(audio_path: str) -> str:
    """Best-effort local transcript for a new reference clip, or "".

    A matching transcript defines the boundary between the reference and the
    requested line. Saving a blank transcript and waiting until the first
    generation made that first take depend on the TTS model's internal ASR
    fallback; short lines could then start with stray words from the
    reference. Resolve it while the reference is saved so every synthesis,
    including the first, uses stable conditioning. Local-only:
    transcribe_reference considers only already-installed ASR/dictation models.
    """
    try:
        from services.asr_backend import transcribe_reference

        return (await asyncio.to_thread(transcribe_reference, audio_path) or "").strip()
    except Exception as exc:  # noqa: BLE001 — profile save remains usable
        logger.warning("reference transcription during profile save failed: %s", exc)
        return ""


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    ref_text: Optional[str] = None
    instruct: Optional[str] = None
    language: Optional[str] = None
    personality: Optional[str] = None


@router.get("/personalities")
def list_personalities():
    """Return built-in voice personality presets."""
    return get_personalities()

@router.get("/profiles")
def list_profiles():
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM voice_profiles ORDER BY created_at DESC").fetchall()
    return [_profile_record(r) for r in rows]

_DESIGN_SEED = 42  # deterministic sample render, same as archetype previews


@router.post("/profiles")
async def create_profile(
    name: str = Form(...),
    ref_audio: Optional[UploadFile] = File(None),
    ref_text: str = Form(""),
    instruct: str = Form(""),
    language: str = Form("Auto"),
    seed: Optional[int] = Form(None),
    personality: str = Form(""),
    kind: str = Form("clone"),
    vd_states: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    """Create a voice profile (spec: docs/specs/voice-studio-unification.md §5).

    kind='clone'  — requires `ref_audio` (the user's reference recording).
    kind='design' — requires `vd_states` (JSON of category picks); the server
                    renders a deterministic sample WAV (seed 42, same path as
                    archetype materialization) and stores it as the profile's
                    reference so the voice identity is stable across runs.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A voice profile needs a name.")
    if kind not in ("clone", "design"):
        raise HTTPException(status_code=422, detail="kind must be 'clone' or 'design'")
    if kind == "clone" and ref_audio is None:
        raise HTTPException(status_code=422, detail="clone profiles require ref_audio")
    if kind == "design":
        if not (vd_states or "").strip():
            raise HTTPException(status_code=422, detail="design profiles require vd_states")
        import json as _json
        try:
            parsed = _json.loads(vd_states)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
        except ValueError:
            raise HTTPException(status_code=422, detail="vd_states must be a JSON object")
        # Root-cause close for #983: a design profile must never be PERSISTED
        # with a partial vd_states shape, regardless of which client (older
        # frontend build, hand-edited payload, third-party API caller) created
        # it — a missing category key crashes DesignMethodPanel's render on
        # every future client that selects this profile. CATEGORY_ORDER is the
        # same single source of truth the frontend's CATEGORIES keys mirror
        # (core/describe_voice.py), so this can't drift from the picker.
        from core.describe_voice import CATEGORY_ORDER
        for _cat in CATEGORY_ORDER:
            parsed.setdefault(_cat, "Auto")
        vd_states = _json.dumps(parsed)
        # An all-Auto design (every category left on "Auto") yields an empty
        # instruct — that's still a valid, saveable voice: synthesis falls back
        # to neutral instruct-only conditioning (see generation.py design path).
        # Don't gate save on a non-empty instruct.
        #
        # Defence-in-depth against the "[object Object]" / freeform-prose poison
        # (#550 #571 #594 #596): never persist an instruct the engine validator
        # would reject. Sanitize the submitted instruct and, if it's unusable,
        # rebuild the tags from vd_states — so the row is always generation-safe
        # regardless of which frontend build saved it.
        instruct = heal_design_instruct(instruct, parsed)
    else:
        # Clone-kind saves get the same server-side choke point (audit finding:
        # this class — "Unsupported instruct items" 400s on every later use —
        # recurred THREE times via clients that bypassed the frontend filter,
        # and the save-time heal above was gated to design-kind). A clone
        # profile has no vd_states to rebuild from, so this is sanitize-only:
        # valid tags survive, prose/"[object Object]" is dropped.
        instruct = sanitize_instruct(instruct)

    profile_id = str(uuid.uuid4())[:8]
    portrait = None
    if isinstance(image, StarletteUploadFile):
        portrait = normalize_portrait(await image.read(MAX_IMAGE_BYTES + 1))
    portrait_path = os.path.join(VOICES_DIR, f"{profile_id}.portrait.jpg")

    if kind == "clone":
        ext = os.path.splitext(ref_audio.filename or ".wav")[1]
        audio_filename = f"{profile_id}{ext}"
        audio_path = os.path.join(VOICES_DIR, audio_filename)
        # Storage can be removed after startup; recover before persisting uploads.
        os.makedirs(VOICES_DIR, exist_ok=True)
        with open(audio_path, "wb") as f:
            f.write(await ref_audio.read())
        # A clone needs speech to copy: refuse a reference with no audio
        # stream (a silent screen recording, a video-only WebM) at save time
        # instead of failing every later generation with it.
        from services.ffmpeg_utils import require_audio_stream
        try:
            await asyncio.to_thread(require_audio_stream, audio_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(audio_path)
            raise
        # Resolve the transcript at save time (see _auto_transcribe_reference).
        if not ref_text.strip():
            ref_text = await _auto_transcribe_reference(audio_path)
        used_seed = seed
    else:
        # Saving a design profile is a pure persistence operation — it must not
        # depend on a loaded TTS model (issue #476: on a fresh model-less Docker
        # image the render forced a full model load + inference that 503'd, so
        # the save failed). We try the deterministic identity sample opportunist-
        # ically through the one shared TTS path (archetypes' renderer, never a
        # second inference code path); if the engine isn't ready it's rendered
        # lazily on first preview/use. The row carries vd_states + instruct, so
        # the voice is fully usable without the sample (synthesis falls back to
        # instruct-only conditioning — see generation.py's design path).
        from pathlib import Path
        from api.routers.archetypes import _render_archetype_wav
        audio_filename = f"{profile_id}.wav"
        audio_path = os.path.join(VOICES_DIR, audio_filename)
        try:
            await _render_archetype_wav(
                {
                    "language": language,
                    "sample_script": ref_text,  # optional custom sample line
                    "instruct": instruct,
                },
                Path(audio_path),
            )
        except Exception:
            # Engine unavailable / OOM / inference failure — defer the sample.
            # Store the row with no ref_audio_path; the identity sample is
            # rendered on first preview or use. Never let this block the save.
            import logging
            logging.getLogger("omnivoice.profiles").info(
                "Design profile %s saved with sample pending — "
                "voice engine not ready; will render on first use", profile_id,
            )
            if os.path.exists(audio_path):  # partial/blank render: don't keep it
                with __import__("contextlib").suppress(OSError):
                    os.remove(audio_path)
            audio_filename = None
        used_seed = seed if seed is not None else _DESIGN_SEED

    try:
        if portrait:
            os.makedirs(VOICES_DIR, exist_ok=True)
            with open(portrait_path, "wb") as out:
                out.write(portrait)
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO voice_profiles (id, name, ref_audio_path, ref_text, instruct, "
                "language, seed, personality, kind, vd_states, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, name, audio_filename, ref_text, instruct, language,
                 used_seed, personality, kind, vd_states, time.time())
            )
    except Exception:
        if os.path.exists(portrait_path):
            os.remove(portrait_path)
        # Clean up orphaned audio file if DB insert fails
        if os.path.exists(audio_path):
            os.remove(audio_path)
        raise
    event_bus.emit("profiles", {"action": "created", "id": profile_id})
    return get_profile(profile_id)

@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str):
    """Full profile record for the voice profile page."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,),
        ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="That voice profile doesn't exist. It may have been deleted from another tab.",
        )
    return _profile_record(row)


@router.get("/profiles/{profile_id}/image")
def get_profile_image(profile_id: str):
    get_profile(profile_id)
    path = _voices_path(f"{profile_id}.portrait.jpg")
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Profile image not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.put("/profiles/{profile_id}/image")
async def update_profile_image(profile_id: str, image: UploadFile = File(...)):
    get_profile(profile_id)
    path = _voices_path(f"{profile_id}.portrait.jpg")
    if path is None:
        raise HTTPException(404, "Profile not found")
    portrait = normalize_portrait(await image.read(MAX_IMAGE_BYTES + 1))
    os.makedirs(VOICES_DIR, exist_ok=True)
    with open(path, "wb") as out:
        out.write(portrait)
    event_bus.emit("profiles", {"action": "updated", "id": profile_id})
    return get_profile(profile_id)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, patch: ProfileUpdate):
    """Partial update — only fields set on the payload are changed."""
    with db_conn() as conn:
        existing = conn.execute(
            "SELECT kind FROM voice_profiles WHERE id = ?", (profile_id,),
        ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404,
            detail="That voice profile doesn't exist. It may have been deleted from another tab.",
        )
    fields = []
    params = []
    edited_instruct = None
    for col in ("name", "ref_text", "instruct", "language", "personality"):
        val = getattr(patch, col)
        if val is None:
            continue
        if col == "name" and not val.strip():
            raise HTTPException(status_code=400, detail="A voice profile needs a name.")
        if col == "instruct":
            # Never let an edit persist a validator-rejecting instruct (prose /
            # "[object Object]"); keep only whitelist tags (#550 #571 #594 #596).
            val = sanitize_instruct(val)
            edited_instruct = val
        fields.append(f"{col} = ?")
        params.append(val.strip() if col in ("name", "language") else val)
    if edited_instruct is not None and existing["kind"] == "design":
        # Keep the complete recipe synchronized with the editable instruct.
        # Otherwise clients restore a stale vd_states snapshot and a successful
        # style edit has no effect on the next generation.
        import json
        from core.describe_voice import instruct_to_vd_states

        fields.append("vd_states = ?")
        params.append(json.dumps(instruct_to_vd_states(edited_instruct)))
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="PUT /profiles/{id} body contained no editable fields. Include at least one of: name, language, ref_text, instruct, personality.",
        )
    params.append(profile_id)
    with db_conn() as conn:
        cur = conn.execute(
            f"UPDATE voice_profiles SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="That voice profile doesn't exist. It may have been deleted from another tab.",
            )
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,),
        ).fetchone()
    event_bus.emit("profiles", {"action": "updated", "id": profile_id})
    return _profile_record(row)


# Reference-clip uploads (#2282). Same formats the desktop picker offers plus
# Opus; anything else is refused so a crafted filename can never choose the
# on-disk extension (py/path-injection) or store a non-audio payload.
_REF_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}
_REF_AUDIO_EXTS = frozenset(_REF_AUDIO_TYPES)
_MIN_REF_AUDIO_BYTES = 1000  # same floor as consent recordings
# A reference is at most a minute or so of speech; even uncompressed 192 kHz
# stereo float stays far below this. Bounds memory and disk per request.
_MAX_REF_AUDIO_BYTES = 128 * 1024 * 1024
_UPLOAD_CHUNK = 1024 * 1024
_DECODE_TIMEOUT_S = 30.0
_DECODE_CONCURRENCY = 2
_DECODE_SEMAPHORE: Optional[asyncio.Semaphore] = None
# One replacement at a time per profile: overlapping uploads would otherwise
# read the same previous paths and leave the losing clip unreferenced.
_replace_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _audio_media_type(filename: str) -> str:
    """Content type for a stored clip, from its (allowlisted) extension."""
    return _REF_AUDIO_TYPES.get(os.path.splitext(filename)[1].lower(), "audio/wav")


def _sndfile_decodes(path: str) -> bool:
    """True when libsndfile decodes real sample frames, not just a header."""
    try:
        import soundfile as sf

        with sf.SoundFile(path) as clip:
            return clip.samplerate > 0 and len(clip.read(4096, dtype="float32")) > 0
    except Exception as exc:  # noqa: BLE001 — not a libsndfile format; try ffmpeg
        logger.debug("libsndfile could not decode a replacement reference: %s", exc)
        return False


async def _ffmpeg_decodes(path: str) -> bool:
    """True when ffmpeg decodes at least one audio sample from the file.

    Decodes (bounded to the first second) instead of probing the header: a
    truncated compressed clip can carry valid stream metadata and still have
    no decodable frames. Browser MediaRecorder WebM has no duration header but
    decodes fine. Missing ffmpeg or a failed run counts as not decodable.
    """
    from services.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg is unavailable; cannot verify a replacement reference")
        return False
    try:
        # One deadline covers waiting for a decode slot and the decode itself.
        return await asyncio.wait_for(_decode_one_second(ffmpeg, path), _DECODE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — cannot verify; refuse the clip
        logger.info("ffmpeg could not decode a replacement reference: %s", exc)
        return False


def _decode_slots() -> asyncio.Semaphore:
    """Reference checks' own FFmpeg slots, separate from the export slots.

    run_ffmpeg's shared slots can be held by long dub exports, so a check must
    not queue there; its own small pool still caps concurrent decodes.
    """
    global _DECODE_SEMAPHORE
    if _DECODE_SEMAPHORE is None:
        _DECODE_SEMAPHORE = asyncio.Semaphore(_DECODE_CONCURRENCY)
    return _DECODE_SEMAPHORE


async def _decode_one_second(ffmpeg: str, path: str) -> bool:
    """Decode the first second to PCM; the process never outlives this call."""
    from services.ffmpeg_utils import spawn_subprocess

    async with _decode_slots():
        proc = await spawn_subprocess(
            ffmpeg, "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0",
            "-t", "1", "-ac", "1", "-f", "s16le", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await proc.communicate()
        finally:
            # Runs on timeout and on request cancellation as well: kill the
            # decode and wait (boundedly) for it to exit before the slot frees.
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
    return proc.returncode == 0 and len(stdout or b"") > 0


async def _is_decodable_audio(path: str) -> bool:
    """Require a positive decode before a clip replaces a voice's reference.

    libsndfile covers WAV/FLAC/OGG/MP3 in-process; compressed browser formats
    (WebM/M4A/AAC) fall through to ffmpeg. The previous reference is deleted
    once the replacement commits, so an unverifiable clip is refused rather
    than risk swapping a working voice for one no engine can read.
    """
    return _sndfile_decodes(path) or await _ffmpeg_decodes(path)


def _remove_voice_file(filename: Optional[str], *, keep: str) -> None:
    """Delete a superseded voices/ file unless it is still referenced."""
    if not filename or filename == keep:
        return
    with db_conn() as conn:
        shared = conn.execute(
            "SELECT 1 FROM voice_profiles WHERE ref_audio_path=? OR locked_audio_path=? "
            "OR consent_audio_path=? LIMIT 1",
            (filename, filename, filename),
        ).fetchone()
    path = None if shared else _voices_path(filename)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("could not remove superseded voice file: %s", exc)


def _replacement_fields(
    name: Optional[str], instruct: Optional[str], language: Optional[str],
    personality: Optional[str],
) -> "tuple[Optional[str], Optional[str], Optional[str], Optional[str]]":
    """Normalized profile edits saved with the new clip; None keeps a column."""
    if name is not None and not name.strip():
        raise HTTPException(status_code=400, detail="A voice profile needs a name.")
    return (
        name.strip() if name is not None else None,
        sanitize_instruct(instruct) if instruct is not None else None,
        language.strip() if language is not None else None,
        personality,
    )


# Static statement: optional edits use COALESCE(?, column) so no SQL is built
# from strings. A legacy row with a NULL kind is a clone, as the read treats it.
_REPLACE_AUDIO_SQL = (
    "UPDATE voice_profiles SET ref_audio_path=?, ref_text=?, "
    "name=COALESCE(?, name), instruct=COALESCE(?, instruct), "
    "language=COALESCE(?, language), personality=COALESCE(?, personality), "
    "locked_audio_path='', is_locked=0, seed=NULL, "
    "verified_own_voice=0, consent_text='', consent_audio_path='', "
    "consent_recorded_at=NULL "
    "WHERE id=? AND COALESCE(kind, 'clone')='clone' AND ref_audio_path IS ?"
)


async def _save_upload(upload: UploadFile, dest: str) -> int:
    """Stream an upload to ``dest`` with a size cap; returns bytes written."""
    total = 0
    with open(dest, "wb") as out:
        while chunk := await upload.read(_UPLOAD_CHUNK):
            total += len(chunk)
            if total > _MAX_REF_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="That recording is too large for a voice reference. "
                    "Trim it to under a minute and try again.",
                )
            out.write(chunk)
    return total


@router.put("/profiles/{profile_id}/audio")
async def replace_profile_audio(
    profile_id: str,
    ref_audio: UploadFile = File(...),
    ref_text: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    instruct: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    personality: Optional[str] = Form(None),
):
    """Replace a saved clone's reference clip in place (#2282).

    The profile keeps its id, portrait and history. Optional ``name``,
    ``instruct``, ``language`` and ``personality`` are saved in the same
    UPDATE, so an editor save commits every change or none. The clip is
    written under a NEW versioned filename (``{id}-{token}{ext}``) rather
    than overwritten: engine prompt caches, prepared-reference caches and
    chapter render caches are keyed by the reference path, so a new name
    invalidates all of them at once. Because the new clip may be a different
    speaker, a locked take and own-voice consent no longer describe this voice
    and are cleared. The previous files are removed only after the database
    commits; any failure before that removes the new file instead.
    """
    not_found = HTTPException(
        status_code=404,
        detail="That voice profile doesn't exist. It may have been deleted from another tab.",
    )
    if not _PROFILE_ID_RE.fullmatch(profile_id or ""):
        raise not_found
    ext = os.path.splitext(ref_audio.filename or "")[1].lower()
    if ext not in _REF_AUDIO_EXTS:
        raise HTTPException(
            status_code=415,
            detail="Choose a supported audio file: WAV, MP3, M4A, FLAC, OGG, Opus, AAC, or WebM.",
        )
    edits = _replacement_fields(name, instruct, language, personality)
    lock = _replace_locks.get(profile_id)
    if lock is None:
        lock = _replace_locks[profile_id] = asyncio.Lock()
    async with lock:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT kind, ref_audio_path, locked_audio_path, consent_audio_path "
                "FROM voice_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
        if not row:
            raise not_found
        if (row["kind"] or "clone") != "clone":
            raise HTTPException(
                status_code=409,
                detail="Designed voices are defined by their recipe, not a recorded sample. "
                "Edit the voice's traits in Voice Design instead.",
            )
        new_filename = f"{profile_id}-{uuid.uuid4().hex[:8]}{ext}"
        new_path = _voices_path(new_filename)
        if new_path is None:  # profile_id is charset-checked above; belt and braces
            raise not_found
        os.makedirs(VOICES_DIR, exist_ok=True)
        tmp_path = f"{new_path}.part"
        try:
            if await _save_upload(ref_audio, tmp_path) < _MIN_REF_AUDIO_BYTES:
                raise HTTPException(
                    status_code=422, detail="The reference recording is empty or too short.",
                )
            os.replace(tmp_path, new_path)
            if not await _is_decodable_audio(new_path):
                # A video-only WebM decodes to nothing because it has no audio
                # stream at all; say that rather than "could not be read".
                from services.ffmpeg_utils import require_audio_stream
                await asyncio.to_thread(require_audio_stream, new_path)
                raise HTTPException(
                    status_code=422,
                    detail="That file could not be read as audio. Choose another recording.",
                )
            text = (ref_text or "").strip() or await _auto_transcribe_reference(new_path)
            with db_conn() as conn:
                # Compare-and-swap on the clip read above, so a writer outside
                # this process can never be overwritten with its file orphaned.
                cur = conn.execute(
                    _REPLACE_AUDIO_SQL,
                    (new_filename, text, *edits, profile_id, row["ref_audio_path"]),
                )
                if cur.rowcount == 0:
                    still_there = conn.execute(
                        "SELECT 1 FROM voice_profiles WHERE id=?", (profile_id,),
                    ).fetchone()
                    if not still_there:  # deleted while the upload ran
                        raise not_found
                    raise HTTPException(
                        status_code=409,
                        detail="This voice changed while the new sample was uploading. "
                        "Reopen it and try again.",
                    )
                updated = conn.execute(
                    "SELECT * FROM voice_profiles WHERE id=?", (profile_id,),
                ).fetchone()
        except BaseException:
            for leftover in (tmp_path, new_path):
                with contextlib.suppress(OSError):
                    os.remove(leftover)
            raise
        for column in ("ref_audio_path", "locked_audio_path", "consent_audio_path"):
            _remove_voice_file(row[column], keep=new_filename)
    event_bus.emit("profiles", {"action": "updated", "id": profile_id})
    return _profile_record(updated)


@router.get("/profiles/{profile_id}/usage")
def get_profile_usage(profile_id: str):
    """Where has this voice been used? Synth-history + segment counts per project."""
    with db_conn() as conn:
        synth_rows = conn.execute(
            "SELECT id, text, audio_path, created_at, generation_time "
            "FROM generation_history WHERE profile_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (profile_id,),
        ).fetchall()
        synth_total = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_history WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["n"]

    # Dub project usage is harder — profile_id lives inside state_json.segments[].profile_id.
    # We scan the persisted state blob; for tens of projects this is fine.
    import json
    project_hits: list[dict] = []
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, updated_at, state_json FROM studio_projects ORDER BY updated_at DESC"
        ).fetchall()
    for r in rows:
        try:
            state = json.loads(r["state_json"] or "{}")
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        # Current desktop snapshots use dubSegments. An explicit empty list
        # supersedes legacy segments retained in an older snapshot.
        segs = state.get("dubSegments", state.get("segments", []))
        if not isinstance(segs, list):
            continue
        n = sum(1 for s in segs if isinstance(s, dict) and s.get("profile_id") == profile_id)
        if n:
            project_hits.append({
                "project_id": r["id"],
                "project_name": r["name"],
                "segment_count": n,
                "updated_at": r["updated_at"],
            })

    return {
        "synth_recent": [dict(r) for r in synth_rows],
        "synth_total": synth_total,
        "projects": project_hits,
        "project_total_segments": sum(p["segment_count"] for p in project_hits),
    }


# profile_id is a request path param and the audio filename derives from it, so
# constrain it to the generated-id charset (no separators / `..` possible) before
# any path use, and read only a *direct child* of VOICES_DIR — os.path.basename()
# strips any directory component (a path-injection / CWE-22 barrier).
_PROFILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@router.get("/profiles/{profile_id}/audio")
async def get_profile_audio(profile_id: str):
    if not _PROFILE_ID_RE.fullmatch(profile_id or ""):
        return Response("Profile not found", status_code=404)
    with db_conn() as conn:
        row = conn.execute(
            "SELECT ref_audio_path, locked_audio_path, kind, instruct, language, ref_text "
            "FROM voice_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
    if not row:
        return Response("Profile not found", status_code=404)
    audio_file = row["locked_audio_path"] or row["ref_audio_path"]
    if not audio_file:
        # A design profile saved before the engine was ready (issue #476) has no
        # identity sample yet. Render it lazily now — the deterministic seed-42
        # sample is reproducible, so a deferred render matches a save-time one.
        rendered = await _materialize_design_sample(profile_id, row)
        if rendered is None:
            return Response("No audio available", status_code=404)
        audio_file = rendered
    # CWE-22: resolve the DB-stored filename strictly inside VOICES_DIR via the
    # shared guard — _voices_path() applies the os.path.basename() barrier plus
    # symlink-resolved containment (same path the consent endpoint trusts).
    audio_path = _voices_path(str(audio_file))
    if audio_path is None or not os.path.exists(audio_path):
        return Response("Audio file missing", status_code=404)
    return FileResponse(audio_path, media_type=_audio_media_type(audio_path))


async def _materialize_design_sample(profile_id: str, row) -> Optional[str]:
    """Render a design profile's pending identity sample on first request.

    Returns the stored filename on success, or None if this isn't a renderable
    design row. Raises HTTPException(503) with a precise "model not ready"
    message if the engine is genuinely unavailable — saving never depends on
    this, but a user who explicitly asks for the sample gets a clear signal.
    """
    try:
        kind = row["kind"]
    except (KeyError, IndexError):
        kind = "clone"
    if kind != "design":
        return None

    from pathlib import Path
    from api.routers.archetypes import _render_archetype_wav

    audio_filename = f"{profile_id}.wav"
    # CWE-22: resolve under VOICES_DIR via the shared basename + containment
    # guard before rendering (rejects any escape).
    audio_path = _voices_path(audio_filename)
    if audio_path is None:
        raise HTTPException(status_code=400, detail="invalid profile identifier")
    try:
        await _render_archetype_wav(
            {
                "language": row["language"] or "Auto",
                "sample_script": row["ref_text"] or "",
                "instruct": row["instruct"] or "",
            },
            Path(audio_path),
        )
    except Exception as e:
        with __import__("contextlib").suppress(OSError):
            if os.path.exists(audio_path):
                os.remove(audio_path)
        raise HTTPException(
            status_code=503,
            detail=(
                "The voice engine isn't ready yet, so this designed voice's "
                "preview sample can't be rendered. Finish setup / download a "
                f"model, then try again. ({e})"
            ),
        )

    with db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET ref_audio_path=? WHERE id=?",
            (audio_filename, profile_id),
        )
    return audio_filename

@router.post("/profiles/{profile_id}/lock")
async def lock_profile(
    profile_id: str,
    history_id: str = Form(...),
    seed: Optional[int] = Form(None),
):
    with db_conn() as conn:
        profile = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Voice profile not found. It may have been deleted from another window — refresh the sidebar to see the current list.",
            )

        history = conn.execute("SELECT * FROM generation_history WHERE id=?", (history_id,)).fetchone()
        if not history or not history["audio_path"]:
            raise HTTPException(status_code=404, detail="History item not found or has no audio")

        try:
            src_path = resolve_within(OUTPUTS_DIR, history["audio_path"])
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail="Invalid history audio path") from exc
        if not src_path.is_file():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")

        locked_filename = f"{profile_id}_locked.wav"
        locked_path = _voices_path(locked_filename)
        if locked_path is None:
            raise HTTPException(status_code=400, detail="Invalid profile id")
        shutil.copy2(str(src_path), locked_path)

        ref_text = history["text"][:100] if history["text"] else ""

        conn.execute(
            "UPDATE voice_profiles SET locked_audio_path=?, seed=?, is_locked=1, ref_text=? WHERE id=?",
            (locked_filename, seed, ref_text, profile_id)
        )
    event_bus.emit("profiles", {"action": "locked", "id": profile_id})
    return {"locked": True, "profile_id": profile_id, "locked_audio_path": locked_filename}

@router.post("/profiles/{profile_id}/unlock")
async def unlock_profile(profile_id: str):
    with db_conn() as conn:
        profile = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Voice profile not found. It may have been deleted from another window — refresh the sidebar to see the current list.",
            )

        if profile["locked_audio_path"]:
            locked_path = _voices_path(profile["locked_audio_path"])
            if locked_path and os.path.exists(locked_path):
                os.remove(locked_path)

        conn.execute(
            "UPDATE voice_profiles SET locked_audio_path='', seed=NULL, is_locked=0 WHERE id=?",
            (profile_id,)
        )
    event_bus.emit("profiles", {"action": "unlocked", "id": profile_id})
    return {"unlocked": True, "profile_id": profile_id}

# ── Consent lock (parity program Wave 0.2) ─────────────────────────────────
#
# A profile becomes "verified own voice" when its owner records themselves
# reading a consent statement. The recording is provenance, not a voiceprint
# check — agentic features and gallery sharing gate on the flag; plain local
# synthesis never does. Spec: docs/competitive-analysis.md Action 22.

_MIN_CONSENT_AUDIO_BYTES = 1000  # same floor as the frontend recorder

# Upload filename extension whitelist — anything else falls back to .wav so a
# crafted filename can never influence the on-disk path (py/path-injection).
_CONSENT_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def _voices_path(filename: str) -> Optional[str]:
    """Resolve a DB-stored audio filename strictly inside VOICES_DIR.

    Rejects anything that isn't a bare filename or that escapes the voices
    directory after symlink resolution. Returns None instead of raising so
    cleanup paths can simply skip bad values.
    """
    if not filename or os.path.basename(filename) != filename:
        return None
    root = os.path.realpath(VOICES_DIR)
    path = os.path.realpath(os.path.join(root, filename))
    if not path.startswith(root + os.sep):
        return None
    return path


@router.post("/profiles/{profile_id}/consent")
async def record_consent(
    profile_id: str,
    consent_audio: UploadFile = File(...),
    consent_text: str = Form(...),
):
    if not consent_text.strip():
        raise HTTPException(status_code=422, detail="consent_text must not be empty")
    data = await consent_audio.read()
    if len(data) < _MIN_CONSENT_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail="consent recording is too short")

    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, consent_audio_path FROM voice_profiles WHERE id=?", (profile_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    ext = os.path.splitext(consent_audio.filename or "")[1]
    if not _CONSENT_EXT_RE.match(ext):
        ext = ".wav"
    audio_filename = f"{profile_id}_consent{ext}"
    audio_path = _voices_path(audio_filename)
    if audio_path is None:  # profile_id is server-generated; this is belt+braces
        raise HTTPException(status_code=400, detail="Invalid profile id")
    with open(audio_path, "wb") as f:
        f.write(data)

    # A re-record may change the extension; drop the superseded file.
    old = row["consent_audio_path"]
    if old and old != audio_filename:
        old_path = _voices_path(old)
        if old_path and os.path.exists(old_path):
            os.remove(old_path)

    recorded_at = time.time()
    try:
        with db_conn() as conn:
            conn.execute(
                "UPDATE voice_profiles SET verified_own_voice=1, consent_text=?, "
                "consent_audio_path=?, consent_recorded_at=? WHERE id=?",
                (consent_text.strip(), audio_filename, recorded_at, profile_id),
            )
    except Exception:
        if os.path.exists(audio_path):
            os.remove(audio_path)
        raise
    event_bus.emit("profiles", {"action": "consent_recorded", "id": profile_id})
    return {
        "id": profile_id,
        "verified_own_voice": True,
        "consent_recorded_at": recorded_at,
    }


@router.delete("/profiles/{profile_id}/consent")
def revoke_consent(profile_id: str):
    with db_conn() as conn:
        row = conn.execute(
            "SELECT consent_audio_path FROM voice_profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        conn.execute(
            "UPDATE voice_profiles SET verified_own_voice=0, consent_text='', "
            "consent_audio_path='', consent_recorded_at=NULL WHERE id=?",
            (profile_id,),
        )
    if row["consent_audio_path"]:
        path = _voices_path(row["consent_audio_path"])
        if path and os.path.exists(path):
            os.remove(path)
    event_bus.emit("profiles", {"action": "consent_revoked", "id": profile_id})
    return {"id": profile_id, "verified_own_voice": False}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    with db_conn() as conn:
        row = conn.execute("SELECT ref_audio_path, locked_audio_path, consent_audio_path FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if row:
            for col in ["ref_audio_path", "locked_audio_path", "consent_audio_path"]:
                if row[col]:
                    path = _voices_path(row[col])
                    if path and os.path.exists(path):
                        os.remove(path)
        portrait_path = _voices_path(f"{profile_id}.portrait.jpg")
        if portrait_path and os.path.isfile(portrait_path):
            os.remove(portrait_path)
        # Prevent FOREIGN KEY constraint failure
        conn.execute("UPDATE generation_history SET profile_id = NULL WHERE profile_id=?", (profile_id,))
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (profile_id,))
    event_bus.emit("profiles", {"action": "deleted", "id": profile_id})
    return {"deleted": profile_id}
