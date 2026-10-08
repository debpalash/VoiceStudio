"""
Voice Design Marketplace — v1.0.x voice profile sharing.

Export, import, and share custom voice profiles as portable `.omnivoice`
bundles. A bundle is a ZIP file containing:
  • metadata.json  — profile name, settings, engine, tags, creator info
  • ref_audio.wav  — reference audio clip
  • locked_audio.wav — locked/optimized audio (if locked)
  • thumbnail.jpg  — optional preview image

This enables:
  • Backup/restore of voice profiles across machines
  • Sharing voices via file transfer, Discord, forums
  • Future: P2P marketplace discovery (local network / IPFS)

Endpoints:
    POST /marketplace/export/{profile_id}  → download .omnivoice bundle
    POST /marketplace/import               → upload .omnivoice bundle → new profile
    GET  /marketplace/browse               → list importable bundles in local store
    POST /marketplace/publish/{profile_id} → save to local marketplace directory
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from core.config import OUTPUTS_DIR, VOICES_DIR
from core.db import db_conn
from core import event_bus
from core.version import APP_VERSION
from core.http_headers import content_disposition
from core.logging_utils import log_safe
from core.path_security import UnsafePath, resolve_within, safe_filename
from core.safe_archive import ArchiveError, copy_member, open_bounded_zip, read_member

logger = logging.getLogger("omnivoice.marketplace")

router = APIRouter(prefix="/marketplace", tags=["Voice Marketplace"])

# Local marketplace directory for published voices
MARKETPLACE_DIR = Path(OUTPUTS_DIR) / "marketplace"
MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)

# Bundle format version — increment if the schema changes
BUNDLE_VERSION = 1

# Maximum bundle upload size (100 MB) to prevent memory exhaustion
MAX_BUNDLE_BYTES = 100 * 1024 * 1024


_MEMBER_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}")


def _open_bundle(content: bytes) -> "tuple[zipfile.ZipFile, dict]":
    """Validate a ``.omnivoice`` bundle and return it with its metadata.

    Entry count, sizes and member names are checked before anything is read,
    so a crafted bundle cannot exhaust memory or disk."""
    if len(content) > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Bundle too large. Max is {MAX_BUNDLE_BYTES} bytes.",
        )
    try:
        zf = open_bounded_zip(content)
        if "metadata.json" not in zf.namelist():
            raise HTTPException(
                status_code=400,
                detail="Invalid .omnivoice bundle: missing metadata.json",
            )
        metadata = json.loads(read_member(zf, "metadata.json"))
    except ArchiveError as exc:
        raise HTTPException(status_code=exc.status, detail=f"Invalid .omnivoice bundle: {exc.detail}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid .omnivoice bundle: metadata.json is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="Invalid .omnivoice bundle: metadata.json is not valid JSON")
    return zf, metadata


def _extract_bundle_audio(zf: zipfile.ZipFile, profile_id: str) -> "tuple[str | None, str | None]":
    """Copy the bundle's reference and locked audio into VOICES_DIR.

    Output names come from ``profile_id``; the member name only contributes an
    allowlisted extension. Returns ``(ref_filename, locked_filename)``."""
    found: dict[str, str] = {}
    for name in zf.namelist():
        for prefix in ("ref_audio", "locked_audio"):
            if name.startswith(prefix) and not name.endswith("/"):
                found[prefix] = name
    written: dict[str, str] = {}
    try:
        for prefix, suffix in (("ref_audio", ""), ("locked_audio", "_locked")):
            name = found.get(prefix)
            if not name:
                continue
            ext = os.path.splitext(name)[1]
            if not _MEMBER_EXT_RE.fullmatch(ext):
                ext = ".wav"
            filename = f"{profile_id}{suffix}{ext}"
            copy_member(zf, name, os.path.join(VOICES_DIR, filename))
            written[prefix] = filename
    except ArchiveError as exc:
        for filename in written.values():
            try:
                os.remove(os.path.join(VOICES_DIR, filename))
            except OSError:
                # Best effort cleanup; the bundle is already being refused.
                pass
        raise HTTPException(status_code=exc.status, detail=f"Invalid .omnivoice bundle: {exc.detail}") from exc
    return written.get("ref_audio"), written.get("locked_audio")


def _contained_path(root, value, *, detail="Invalid file path") -> Path:
    try:
        return resolve_within(root, value)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _voice_asset(value) -> Path | None:
    """Resolve a DB-stored voice asset without trusting the database value."""
    if not value:
        return None
    try:
        resolved = resolve_within(VOICES_DIR, value)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Voice profile contains an invalid asset path") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Voice profile reference audio is missing")
    return resolved


# ── Export ──────────────────────────────────────────────────────────────────


def _bundle_metadata(profile: dict, **extra) -> dict:
    """Common .omnivoice metadata for export + publish.

    Captures ``kind`` and ``vd_states`` so a *designed* persona survives the
    bundle round-trip as a design (not silently demoted to a clone) — required
    for the synthetic-only gating of the persona gallery (§R3). Old bundles
    without these keys import as ``kind='clone'`` (backward-compatible).
    """
    meta = {
        "bundle_version": BUNDLE_VERSION,
        "profile_name": profile.get("name", "Unnamed"),
        "ref_text": profile.get("ref_text", ""),
        "instruct": profile.get("instruct", ""),
        "language": profile.get("language", "Auto"),
        "personality": profile.get("personality", ""),
        "seed": profile.get("seed"),
        "kind": profile.get("kind") or "clone",
        "vd_states": profile.get("vd_states"),
        "is_locked": bool(profile.get("is_locked")),
        "omnivoice_version": APP_VERSION,
    }
    meta.update(extra)
    return meta


@router.post("/export/{profile_id}")
def export_profile(profile_id: str):
    """Export a voice profile as a downloadable .omnivoice bundle (ZIP)."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Voice profile not found")

    profile = dict(row)

    # Build the ZIP bundle in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Metadata
        metadata = _bundle_metadata(
            profile, created_at=profile.get("created_at"), exported_at=time.time(),
        )
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        # Reference audio
        ref_path = profile.get("ref_audio_path")
        if ref_path:
            full_ref = _voice_asset(ref_path)
            if full_ref and full_ref.is_file():
                ext = os.path.splitext(ref_path)[1] or ".wav"
                zf.write(str(full_ref), f"ref_audio{ext}")

        # Locked audio (if profile is locked)
        locked_path = profile.get("locked_audio_path")
        if locked_path:
            full_locked = _voice_asset(locked_path)
            if full_locked and full_locked.is_file():
                ext = os.path.splitext(locked_path)[1] or ".wav"
                zf.write(str(full_locked), f"locked_audio{ext}")

    buf.seek(0)
    safe_name = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in profile.get("name", "voice")
    ).strip().replace(" ", "_")[:40]
    filename = f"{safe_name}.omnivoice"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(buf.getbuffer().nbytes),
        },
    )


# ── Import ──────────────────────────────────────────────────────────────────


@router.post("/import")
async def import_profile(
    file: UploadFile = File(..., description="A .omnivoice bundle file"),
):
    """Import a voice profile from a .omnivoice bundle."""
    if not file.filename or not file.filename.endswith(".omnivoice"):
        raise HTTPException(
            status_code=400,
            detail="File must be a .omnivoice bundle (ZIP format).",
        )

    # Enforce upload size limit before reading
    content = await file.read(MAX_BUNDLE_BYTES + 1)  # bounded: anything larger is refused below
    zf, metadata = _open_bundle(content)
    profile_id = str(uuid.uuid4())[:8]
    ref_audio_filename, locked_audio_filename = _extract_bundle_audio(zf, profile_id)

    if not ref_audio_filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid .omnivoice bundle: no reference audio found.",
        )

    # Create the profile in the database
    is_locked = bool(metadata.get("is_locked") and locked_audio_filename)
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_profiles
               (id, name, ref_audio_path, ref_text, instruct, language,
                seed, personality, is_locked, locked_audio_path, created_at,
                kind, vd_states)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                metadata.get("profile_name", "Imported Voice"),
                ref_audio_filename,
                metadata.get("ref_text", ""),
                metadata.get("instruct", ""),
                metadata.get("language", "Auto"),
                metadata.get("seed"),
                metadata.get("personality", ""),
                1 if is_locked else 0,
                locked_audio_filename or "",
                time.time(),
                # Preserve the design/clone distinction across the round-trip;
                # old bundles without these keys import as a clone.
                metadata.get("kind") or "clone",
                metadata.get("vd_states"),
            ),
        )

    event_bus.emit("profiles", {"action": "created", "id": profile_id})
    logger.info(
        "Imported voice profile %r as %s from .omnivoice bundle",
        metadata.get("profile_name"), profile_id,
    )

    return {
        "success": True,
        "profile_id": profile_id,
        "name": metadata.get("profile_name", "Imported Voice"),
        "is_locked": is_locked,
        "source_bundle": file.filename,
    }


# ── Local Marketplace (Publish & Browse) ───────────────────────────────────


@router.post("/publish/{profile_id}")
def publish_to_marketplace(
    profile_id: str,
    tags: str = Query("", description="Comma-separated tags"),
):
    """Publish a voice profile to the local marketplace directory.

    This saves a .omnivoice bundle to the marketplace folder so other
    VoiceStudio instances on the same machine (or shared network drive)
    can discover and import it.
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Voice profile not found")

    profile = dict(row)
    safe_name = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in profile.get("name", "voice")
    ).strip().replace(" ", "_")[:40]
    bundle_path = _contained_path(
        MARKETPLACE_DIR,
        f"{safe_name}_{profile_id}.omnivoice",
        detail="Invalid profile id",
    )

    # Build the bundle
    with zipfile.ZipFile(str(bundle_path), "w", zipfile.ZIP_DEFLATED) as zf:
        metadata = _bundle_metadata(
            profile,
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            published_at=time.time(),
        )
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        ref_path = profile.get("ref_audio_path")
        if ref_path:
            full_ref = _voice_asset(ref_path)
            if full_ref and full_ref.is_file():
                ext = os.path.splitext(ref_path)[1] or ".wav"
                zf.write(str(full_ref), f"ref_audio{ext}")

        locked_path = profile.get("locked_audio_path")
        if locked_path:
            full_locked = _voice_asset(locked_path)
            if full_locked and full_locked.is_file():
                ext = os.path.splitext(locked_path)[1] or ".wav"
                zf.write(str(full_locked), f"locked_audio{ext}")

    logger.info("Voice published to marketplace")
    return {
        "success": True,
        "profile_id": profile_id,
        "bundle_path": str(bundle_path),
        "bundle_size": os.path.getsize(bundle_path),
    }


@router.get("/browse")
def browse_marketplace(
    search: Optional[str] = Query(None, description="Search by name or tags"),
):
    """List available .omnivoice bundles in the local marketplace directory."""
    bundles = []

    for path in sorted(MARKETPLACE_DIR.glob("*.omnivoice"), key=os.path.getmtime, reverse=True):
        try:
            with zipfile.ZipFile(str(path)) as zf:
                if "metadata.json" not in zf.namelist():
                    continue
                metadata = json.loads(read_member(zf, "metadata.json"))

                # Apply search filter
                if search:
                    searchable = " ".join([
                        metadata.get("profile_name", ""),
                        " ".join(metadata.get("tags", [])),
                        metadata.get("personality", ""),
                    ]).lower()
                    if search.lower() not in searchable:
                        continue

                bundles.append({
                    "filename": path.name,
                    "name": metadata.get("profile_name", path.stem),
                    "language": metadata.get("language", "unknown"),
                    "tags": metadata.get("tags", []),
                    "is_locked": metadata.get("is_locked", False),
                    "personality": metadata.get("personality", ""),
                    "published_at": metadata.get("published_at"),
                    "size_bytes": os.path.getsize(path),
                    "has_ref_audio": any(
                        n.startswith("ref_audio") for n in zf.namelist()
                    ),
                    "has_locked_audio": any(
                        n.startswith("locked_audio") for n in zf.namelist()
                    ),
                })
        except Exception as e:
            logger.warning("Skipping invalid bundle %s: %s", log_safe(path.name), log_safe(e))

    return {"bundles": bundles, "total": len(bundles), "directory": str(MARKETPLACE_DIR)}


@router.post("/install/{filename}")
async def install_from_marketplace(filename: str):
    """Import a voice profile from a bundle in the local marketplace directory."""
    try:
        filename = safe_filename(filename)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid bundle filename") from exc
    if not filename.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="Invalid bundle filename")
    bundle_path = _contained_path(MARKETPLACE_DIR, filename, detail="Invalid bundle filename")
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")

    # Read and delegate to the import logic
    with open(bundle_path, "rb") as f:
        content = f.read(MAX_BUNDLE_BYTES + 1)

    zf, metadata = _open_bundle(content)
    profile_id = str(uuid.uuid4())[:8]
    ref_audio_filename, locked_audio_filename = _extract_bundle_audio(zf, profile_id)

    if not ref_audio_filename:
        raise HTTPException(status_code=400, detail="No reference audio in bundle.")

    is_locked = bool(metadata.get("is_locked") and locked_audio_filename)
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_profiles
               (id, name, ref_audio_path, ref_text, instruct, language,
                seed, personality, is_locked, locked_audio_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                metadata.get("profile_name", "Marketplace Voice"),
                ref_audio_filename,
                metadata.get("ref_text", ""),
                metadata.get("instruct", ""),
                metadata.get("language", "Auto"),
                metadata.get("seed"),
                metadata.get("personality", ""),
                1 if is_locked else 0,
                locked_audio_filename or "",
                time.time(),
            ),
        )

    event_bus.emit("profiles", {"action": "created", "id": profile_id})

    return {
        "success": True,
        "profile_id": profile_id,
        "name": metadata.get("profile_name", "Marketplace Voice"),
        "source": filename,
    }


@router.delete("/{filename}")
def remove_from_marketplace(filename: str):
    """Remove a bundle from the local marketplace directory."""
    try:
        filename = safe_filename(filename)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid bundle filename") from exc
    if not filename.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="Invalid bundle filename")
    bundle_path = _contained_path(MARKETPLACE_DIR, filename, detail="Invalid bundle filename")
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")
    try:
        os.unlink(bundle_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")
    return {"success": True, "deleted": filename}
