import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.db import db_conn
from core.config import VOICES_DIR, OUTPUTS_DIR
from core import event_bus
from core.audio_validation import resolve_regular_file
from core.file_cleanup import FileCleanupError, unlink_if_present
from services.ffmpeg_utils import spawn_subprocess

logger = logging.getLogger("omnivoice.gallery")

router = APIRouter()

VOICE_GALLERY_DIR = Path(os.path.join(OUTPUTS_DIR, "voice_gallery"))
VOICE_GALLERY_DIR.mkdir(parents=True, exist_ok=True)

# Voice imports carry no project-authored taxonomy. The gallery deliberately
# ships no curated directory of named real people (celebrities, politicians,
# franchise characters): shipping such a directory would turn a neutral
# user-driven import tool into an editorial invitation to clone identifiable
# individuals (the inducement-liability line). Users paste their own URLs/files
# into a flat "My Imports" list and own the licensing call. Designed,
# real-person-free voices live in the archetype gallery (core.archetypes).
CATEGORIES: list[dict] = []


class VoiceEntry(BaseModel):
    id: str
    name: str
    character: str
    category: str
    source_type: str  # "youtube", "upload", "preset"
    source_url: Optional[str] = None
    audio_path: str
    duration: float
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    tags: List[str] = []
    created_at: float


def _init_gallery_db():
    """Initialize the voice gallery table."""
    with db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_gallery (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                character TEXT NOT NULL,
                category TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_url TEXT,
                audio_path TEXT NOT NULL,
                duration REAL NOT NULL,
                description TEXT,
                thumbnail TEXT,
                tags TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        # Migration: add is_favorite column if missing (existing DBs)
        try:
            conn.execute("SELECT is_favorite FROM voice_gallery LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE voice_gallery ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")


@router.get("/gallery/categories")
def list_categories():
    """List all voice gallery categories."""
    return CATEGORIES


@router.get("/gallery/voices")
def list_voices(
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search by name or character"),
    limit: int = Query(50, ge=1, le=200),
):
    """List voices in the gallery, optionally filtered by category or search."""
    query = "SELECT * FROM voice_gallery"
    params = []
    conditions = []

    if category:
        conditions.append("category = ?")
        params.append(category)
    if search:
        conditions.append("(name LIKE ? OR character LIKE ? OR description LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with db_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        r["tags"] = json.loads(r.get("tags", "[]") or "[]")
        results.append(r)
    return results


@router.get("/gallery/voices/{voice_id}")
def get_voice(voice_id: str):
    """Get a specific voice from the gallery."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_gallery WHERE id = ?", (voice_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Voice not found")
    r = dict(row)
    r["tags"] = json.loads(r.get("tags", "[]") or "[]")
    return r


@router.delete("/gallery/voices/{voice_id}")
def delete_voice(voice_id: str):
    """Delete a voice from the gallery."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT audio_path FROM voice_gallery WHERE id = ?", (voice_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Voice not found")

        audio_path = row["audio_path"]
        if audio_path:
            try:
                unlink_if_present(audio_path)
            except FileCleanupError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Could not delete the voice audio file. Close any app using it and retry.",
                ) from exc

        conn.execute("DELETE FROM voice_gallery WHERE id = ?", (voice_id,))
    return {"success": True}


@router.post("/gallery/search/youtube")
async def search_youtube(
    query: str = Query(..., description="User-supplied search terms or video title"),
    category: str = Query("import", description="Free-form tag stored with results"),
    max_results: int = Query(5, ge=1, le=20),
):
    """Search a source site (via yt-dlp) for clips matching the user's query.

    The query is user-supplied; the project ships no celebrity/character seed
    list. Users are responsible for the licensing of whatever they import.
    """
    try:
        # yt-dlp is an importable module, never a PATH requirement — run it
        # via the interpreter (honors the Settings → Audio tools overlay).
        from services.media_tools import ytdlp_invocation
        ytdlp_argv, ytdlp_env = ytdlp_invocation()
        result = await spawn_subprocess(
            *ytdlp_argv,
            "--dump-json",
            "--remote-components", "ejs:github",
            f"ytsearch{max_results}:{query}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ytdlp_env,
        )
        stdout, stderr = await result.communicate()

        if result.returncode != 0:
            logger.error(f"yt-dlp search failed: {stderr.decode()}")
            raise HTTPException(
                status_code=500, detail=f"YouTube search failed: {stderr.decode()}"
            )

        lines = stdout.decode().strip().split("\n")
        results = []
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                results.append(
                    {
                        "title": data.get("title", ""),
                        "video_id": data.get("id", ""),
                        "duration": str(data.get("duration")) if data.get("duration") is not None else None,
                        "thumbnail": data.get("thumbnail", None),
                    }
                )
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse yt-dlp JSON line: {line}")

        return {"results": results, "query": query, "category": category}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="yt-dlp not installed")
    except Exception as e:
        logger.exception("YouTube search error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gallery/download")
async def download_youtube_clip(
    video_url: str = Query(..., description="YouTube video URL"),
    start_time: float = Query(0, ge=0, description="Start time in seconds"),
    duration: float = Query(10, ge=1, le=30, description="Clip duration in seconds"),
    character_name: str = Query(..., description="Name to label this clip"),
    category: str = Query("import", description="Free-form tag stored with the clip"),
    description: str = Query("", description="Optional description"),
):
    """Download a clip from YouTube for voice cloning."""
    voice_id = str(uuid.uuid4())[:8]
    output_path = str(VOICE_GALLERY_DIR / f"{voice_id}.wav")
    temp_path = str(VOICE_GALLERY_DIR / f"{voice_id}.%(ext)s")

    try:
        from services.media_tools import ytdlp_invocation
        ytdlp_argv, ytdlp_env = ytdlp_invocation()
        cmd = [
            *ytdlp_argv,
            "--remote-components", "ejs:github",
            "-f",
            "bestaudio",
            "--download-sections",
            f"*{start_time:.1f}-{start_time + duration:.1f}",
            "-x",
            "--audio-format",
            "wav",
            "--audio-quality",
            "0",
            "-o",
            temp_path,
            video_url,
        ]

        result = await spawn_subprocess(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ytdlp_env,
        )
        stdout, stderr = await result.communicate()

        if result.returncode != 0:
            logger.error(f"yt-dlp download failed: {stderr.decode()}")
            raise HTTPException(
                status_code=500, detail=f"Download failed: {stderr.decode()}"
            )

        # Find the downloaded file (yt-dlp replaces %s with actual extension)
        downloaded_files = list(VOICE_GALLERY_DIR.glob(f"{voice_id}.*"))
        if not downloaded_files:
            raise HTTPException(status_code=500, detail="Downloaded file not found")

        actual_path = downloaded_files[0]
        # Rename to output_path
        final_path = Path(output_path)
        actual_path.rename(final_path)

        conn = db_conn()
        with conn as c:
            c.execute(
                """
                INSERT INTO voice_gallery 
                (id, name, character, category, source_type, source_url, audio_path, duration, description, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    voice_id,
                    character_name,
                    character_name,
                    category,
                    "youtube",
                    video_url,
                    output_path,
                    duration,
                    description,
                    json.dumps([character_name.lower(), category]),
                    time.time(),
                ),
            )

        return {
            "success": True,
            "voice_id": voice_id,
            "audio_path": output_path,
            "duration": duration,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="yt-dlp not installed")
    except Exception as e:
        logger.exception("Download error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gallery/upload")
async def upload_voice_clip(
    name: str = Form(...),
    character: str = Form(""),
    category: str = Form("import"),
    description: str = Form(""),
    audio: UploadFile = File(...),
):
    """Upload a voice clip directly to the gallery."""
    voice_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(audio.filename or ".wav")[1]
    audio_path = str(VOICE_GALLERY_DIR / f"{voice_id}{ext}")

    with open(audio_path, "wb") as f:
        f.write(await audio.read())

    # The picker accepts video; one without an audio stream is no voice clip.
    from services.ffmpeg_utils import require_audio_stream
    try:
        await asyncio.to_thread(require_audio_stream, audio_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(audio_path)
        raise

    try:
        import soundfile as sf

        info = sf.info(audio_path)
        duration = info.frames / info.samplerate
    except Exception:
        duration = 10.0

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO voice_gallery 
            (id, name, character, category, source_type, source_url, audio_path, duration, description, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                voice_id,
                name,
                character,
                category,
                "upload",
                None,
                audio_path,
                duration,
                description,
                json.dumps([character.lower(), category]),
                time.time(),
            ),
        )

    return {
        "id": voice_id,
        "name": name,
        "audio_path": audio_path,
        "duration": duration,
    }


def _stage_profile_audio(source: Path, directory: Path) -> Path:
    """Copy an imported clip to a hidden temp file inside ``directory``.

    The temp lives in the destination directory itself so a later
    ``os.replace`` to the final name is an atomic same-filesystem rename —
    cheap enough to run while holding a DB write lock, unlike the copy.
    Callers own cleanup of the returned path if they never publish it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=".gallery-import-", suffix=".part",
    )
    os.close(fd)
    try:
        shutil.copy2(source, tmp_name)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return Path(tmp_name)


def _copy_profile_audio(source: Path, destination: Path) -> None:
    """Copy an imported clip without exposing a partial profile audio file."""
    staged = _stage_profile_audio(source, destination.parent)
    try:
        os.replace(staged, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(staged)
        raise


def _gallery_profile_audio_filename(profile_id: str, source: Path) -> str:
    """Return the canonical, portable filename for a My Imports profile."""
    safe_id = (
        profile_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id or "")
        else uuid.uuid5(uuid.NAMESPACE_URL, str(profile_id)).hex[:16]
    )
    suffix = source.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".wav"
    return f"{safe_id}_gallery{suffix}"


def _is_materialized_gallery_profile(row, voice: dict, audio_filename: str) -> bool:
    """Recognize only rows created by this materializer, not identity collisions."""
    return bool(
        row["personality"] == f"gallery:{voice['id']}"
        and row["ref_audio_path"] == audio_filename
        and row["ref_text"] == ""
        and row["instruct"] == ""
        and row["language"] == "Auto"
        and row["seed"] is None
        and row["kind"] == "clone"
        and not row["vd_states"]
        and row["description"] == (voice.get("description") or "")
        and not row["is_locked"]
        and not row["verified_own_voice"]
        and not row["locked_audio_path"]
    )


def _existing_gallery_profile(conn, voice: dict, source: Path):
    personality = f"gallery:{voice['id']}"
    rows = conn.execute(
        "SELECT * FROM voice_profiles WHERE personality=? ORDER BY created_at, id",
        (personality,),
    ).fetchall()
    for row in rows:
        expected = _gallery_profile_audio_filename(row["id"], source)
        if _is_materialized_gallery_profile(row, voice, expected):
            return row
    return None


def _gallery_profile_audio_is_current(row, source: Path) -> bool:
    """Detect missing/replaced copies without re-hashing unchanged imports."""
    destination = resolve_regular_file(VOICES_DIR, row["ref_audio_path"])
    if destination is None:
        return False
    try:
        source_stat = source.stat()
        destination_stat = destination.stat()
        # copy2 preserves mtime; size + nanosecond mtime catches ordinary edits
        # and partial writes while keeping repeated Use clicks inexpensive.
        return (
            source_stat.st_size == destination_stat.st_size
            and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
        )
    except OSError:
        return False


def _materialize_gallery_profile(
    voice_id: str, requested_name: Optional[str] = None,
) -> dict:
    """Idempotently materialize/heal one My Imports clip as a clone profile."""
    personality = f"gallery:{voice_id}"
    copied_path: Optional[Path] = None
    created = False
    staged_path: Optional[Path] = None
    staged_source: Optional[Path] = None
    try:
        # Stage the (potentially large) audio copy BEFORE taking SQLite's
        # write lock: copying inside BEGIN IMMEDIATE would stall every other
        # backend writer for the whole copy. The staged temp lives in
        # VOICES_DIR itself, so publishing it inside the transaction is an
        # atomic same-filesystem os.replace. This pre-read is advisory only —
        # the locked transaction below re-reads and re-decides everything.
        copy_needed = False
        with db_conn() as conn:
            pre_row = conn.execute(
                "SELECT * FROM voice_gallery WHERE id = ?", (voice_id,),
            ).fetchone()
            if pre_row is not None:
                pre_source = Path(pre_row["audio_path"])
                if pre_source.is_file():
                    pre_existing = _existing_gallery_profile(conn, dict(pre_row), pre_source)
                    copy_needed = pre_existing is None or not _gallery_profile_audio_is_current(
                        pre_existing, pre_source,
                    )
        if copy_needed:
            staged_path = _stage_profile_audio(pre_source, Path(VOICES_DIR))
            staged_source = pre_source

        with db_conn() as conn:
            # The identity is not globally UNIQUE because personality is shared
            # with other import mechanisms. Serialize this check+insert in
            # SQLite so simultaneous Use clicks cannot both create a row.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM voice_gallery WHERE id = ?", (voice_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Voice not found")

            voice = dict(row)
            source = Path(voice["audio_path"])
            if not source.is_file():
                raise HTTPException(status_code=404, detail="Audio file not found on disk")

            def _install_audio(destination: Path) -> None:
                """Publish the staged copy under the lock via atomic rename."""
                nonlocal staged_path
                if staged_path is not None and staged_source == source:
                    os.replace(staged_path, destination)
                    staged_path = None
                else:
                    # Rare race: the gallery row changed between the advisory
                    # pre-read and taking the lock, so any staged bytes may be
                    # from the wrong source. Fall back to the blocking copy
                    # rather than publish stale audio.
                    _copy_profile_audio(source, destination)

            existing = _existing_gallery_profile(conn, voice, source)
            if existing is not None:
                ref_filename = _gallery_profile_audio_filename(existing["id"], source)
                if not _gallery_profile_audio_is_current(existing, source):
                    ref_path = Path(VOICES_DIR) / ref_filename
                    _install_audio(ref_path)
                    copied_path = ref_path
                conn.execute(
                    "UPDATE voice_profiles SET ref_audio_path=?, ref_text='', instruct='', "
                    "language='Auto', seed=NULL, description=?, kind='clone', vd_states=NULL, "
                    "personality=? WHERE id=?",
                    (
                        ref_filename, voice["description"] or "", personality,
                        existing["id"],
                    ),
                )
                result = {"profile_id": existing["id"], "name": existing["name"]}
            else:
                profile_id = str(uuid.uuid4())[:8]
                profile_name = (requested_name or voice["name"]).strip() or voice["name"]
                ref_filename = _gallery_profile_audio_filename(profile_id, source)
                copied_path = Path(VOICES_DIR) / ref_filename
                _install_audio(copied_path)
                conn.execute(
                    """INSERT INTO voice_profiles
                       (id, name, ref_audio_path, ref_text, instruct, language, seed,
                        personality, is_locked, locked_audio_path, description, kind,
                        vd_states, created_at)
                       VALUES (?, ?, ?, '', '', 'Auto', NULL, ?, 0, '', ?, 'clone', NULL, ?)""",
                    (
                        profile_id, profile_name, ref_filename, personality,
                        voice["description"] or "", time.time(),
                    ),
                )
                created = True
                result = {"profile_id": profile_id, "name": profile_name}
    except BaseException:
        if copied_path is not None:
            with contextlib.suppress(OSError):
                copied_path.unlink()
        raise
    finally:
        # Staged but never published (failure, or a concurrent request healed
        # the profile first) — never leave .part droppings in VOICES_DIR.
        if staged_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(staged_path)

    event_bus.emit(
        "profiles", {"action": "created" if created else "updated", "id": result["profile_id"]},
    )
    return result


@router.post("/gallery/voices/{voice_id}/save-as-profile")
async def save_voice_as_profile(
    voice_id: str,
    profile_name: str = Query(..., description="Name for the voice profile"),
):
    """Save a gallery voice as a voice profile for cloning."""
    result = await asyncio.to_thread(_materialize_gallery_profile, voice_id, profile_name)
    return {"profile_id": result["profile_id"], "name": result["name"]}


@router.get("/gallery/voices/{voice_id}/preview")
def preview_voice(voice_id: str):
    """Get a voice clip for preview playback."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT audio_path FROM voice_gallery WHERE id = ?", (voice_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Voice not found")

    audio_path = row["audio_path"]

    if os.path.isabs(audio_path) and os.path.exists(audio_path):
        # Serve the file from this API route so deployments mounted below a
        # path prefix do not lose that prefix while following a redirect.
        return FileResponse(audio_path)

    raise HTTPException(
        status_code=404,
        detail="Audio file not found. It may have been deleted or moved.",
    )


# ── Library management endpoints ──────────────────────────────────────────

@router.patch("/gallery/voices/{voice_id}")
def update_voice(voice_id: str, body: dict):
    """Update voice metadata — name, tags, is_favorite."""
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM voice_gallery WHERE id = ?", (voice_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Voice not found")

        updates = []
        params = []
        if "name" in body:
            updates.append("name = ?")
            params.append(body["name"])
        if "tags" in body:
            updates.append("tags = ?")
            params.append(json.dumps(body["tags"]) if isinstance(body["tags"], list) else body["tags"])
        if "is_favorite" in body:
            updates.append("is_favorite = ?")
            params.append(1 if body["is_favorite"] else 0)
        if "description" in body:
            updates.append("description = ?")
            params.append(body["description"])

        if not updates:
            return {"success": True, "updated": []}

        params.append(voice_id)
        # `updates` holds only static, code-controlled column fragments
        # ("is_favorite = ?", "description = ?"); every user value is bound via
        # a `?` placeholder in `params`. No user input reaches the SQL string.
        conn.execute(f"UPDATE voice_gallery SET {', '.join(updates)} WHERE id = ?", params)  # nosec B608
    return {"success": True, "updated": list(body.keys())}


@router.post("/gallery/voices/batch-delete")
def batch_delete_voices(body: dict):
    """Delete multiple voices by ID list."""
    ids = body.get("ids", [])
    if not ids:
        return {"deleted": 0}

    deleted = 0
    failed = 0
    with db_conn() as conn:
        for vid in ids:
            row = conn.execute("SELECT audio_path FROM voice_gallery WHERE id = ?", (vid,)).fetchone()
            if row:
                audio_path = row["audio_path"]
                if audio_path:
                    try:
                        unlink_if_present(audio_path)
                    except FileCleanupError:
                        logger.warning("Voice audio cleanup failed for a gallery item")
                        failed += 1
                        continue
                conn.execute("DELETE FROM voice_gallery WHERE id = ?", (vid,))
                deleted += 1
    return {"deleted": deleted, "failed": failed}


@router.post("/gallery/voices/{voice_id}/to-profile")
def voice_to_profile(voice_id: str):
    """Create a voice profile from a gallery clip."""
    result = _materialize_gallery_profile(voice_id)
    return {"success": True, "profile_id": result["profile_id"], "name": result["name"]}
