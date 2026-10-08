"""HTTP layer for the `.ovsvoice` portable persona format (#29 / parity §R3 G1).

Thin router over `services.persona_bundle`:

    POST /personas/export/{profile_id}   → stream a downloadable .ovsvoice
    POST /personas/import                → create a profile from a bundle
    POST /personas/inspect               → read a bundle's manifest, no writes

Mirrors the legacy `.omnivoice` endpoints (`marketplace.py`) and reuses the
same path-confinement (`_voices_path`) + consent floor. `.ovsvoice` is additive;
`.omnivoice` import stays a compatible legacy reader.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os

from utils.fsops import safe_replace
import time
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from core import event_bus
from core.config import VOICES_DIR  # noqa: F401 — re-exported for tests/monkeypatch
from core.db import db_conn
from core.version import APP_VERSION
from core.logging_utils import log_safe
from core.http_headers import content_disposition
from services import persona_bundle as pb

router = APIRouter()
logger = logging.getLogger("omnivoice.personas")


def _safe_name(name: str, profile_id: str) -> str:
    """Sanitised download filename stem (marketplace idiom); empty → persona_<id>."""
    cleaned = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in (name or "")
    ).strip().replace(" ", "_")[:40]
    return cleaned or f"persona_{profile_id}"


# ── Export ────────────────────────────────────────────────────────────────


@router.post("/personas/export/{profile_id}")
async def export_persona(
    profile_id: str,
    license_spdx: str = Query(pb.DEFAULT_LICENSE),
    tags: str = Query(""),
    include_reference: bool = Query(True),
):
    """Build + stream a `.ovsvoice` bundle for a profile."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    profile = dict(row)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    # #693: if OMNIVOICE_MODEL is set, record the *resolved* checkpoint in the
    # exported bundle so a leaked engine id (e.g. "omnivoice") can't be baked in;
    # keep "" when unset (the bundle's "engine unspecified" marker).
    from services.model_manager import resolve_omnivoice_checkpoint
    engine_id = resolve_omnivoice_checkpoint() if os.environ.get("OMNIVOICE_MODEL", "").strip() else ""
    try:
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(
            None,
            functools.partial(
                pb.build_persona_bundle,
                profile,
                license_spdx=license_spdx,
                tags=tag_list,
                include_reference=include_reference,
                engine_id=engine_id,
                omnivoice_version=APP_VERSION,
            ),
        )
    except pb.NoPreviewSource:
        raise HTTPException(
            status_code=503,
            detail="This profile has no readable reference or locked audio to "
                   "build a preview from — re-create or re-import it.",
        )
    except Exception as exc:
        logger.error("persona export failed for %s: %s", log_safe(profile_id), log_safe(exc))
        raise HTTPException(
            status_code=503,
            detail="Could not build the persona bundle — see Settings → Logs.",
        )

    filename = f"{_safe_name(profile.get('name'), profile_id)}.ovsvoice"
    from io import BytesIO
    return StreamingResponse(
        BytesIO(content),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(len(content)),
        },
    )


# ── Import ────────────────────────────────────────────────────────────────


def _voices_dest(filename: str) -> str:
    """Resolve an output filename inside VOICES_DIR; 400 on escape (belt+braces —
    the name is always server-generated `{profile_id}…`)."""
    from api.routers.profiles import _voices_path
    path = _voices_path(filename)
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid profile id")
    return path


def _consent_verified(parsed: pb.ParsedPersona, consent_path: str | None) -> bool:
    """B12-B16: trust verified-own-voice ONLY with a real recording (≥ floor) AND
    non-empty consent_text AND a consent.json present. The manifest flag alone
    can't forge it."""
    if not parsed.consent or not consent_path:
        return False
    if os.path.getsize(consent_path) < pb._MIN_CONSENT_AUDIO_BYTES:
        return False
    return bool((parsed.consent.get("consent_text") or "").strip())


@router.post("/personas/import")
async def import_persona(file: UploadFile = File(...)):
    """Create a new voice profile from a `.ovsvoice` (or legacy `.omnivoice`) bundle."""
    name = (file.filename or "").lower()
    if not name.endswith(".ovsvoice") and not name.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="File must be a .ovsvoice or .omnivoice bundle")

    content = await file.read(pb.MAX_BUNDLE_BYTES + 1)  # parse rejects anything larger
    try:
        parsed = pb.parse_persona_bundle(content)
    except pb.BundleError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    persona = parsed.manifest.get("persona") or {}
    written: list[str] = []

    def _gen_id() -> str:
        return str(uuid.uuid4())[:8]

    profile_id = _gen_id()
    try:
        # ── Audio members → server-named files (never the member name). ──
        ref_filename = None
        locked_filename = None
        if "ref_audio" in parsed.members:
            ref_filename = f"{profile_id}{parsed.member_ext('ref_audio')}"
            dest = _voices_dest(ref_filename)
            parsed.extract_member("ref_audio", dest); written.append(dest)
        if "locked_audio" in parsed.members:
            locked_filename = f"{profile_id}_locked{parsed.member_ext('locked_audio')}"
            dest = _voices_dest(locked_filename)
            parsed.extract_member("locked_audio", dest); written.append(dest)
        # Preview-only bundle (A12/B8): use the preview as the usable ref clip.
        if ref_filename is None and locked_filename is None and "preview" in parsed.members:
            ref_filename = f"{profile_id}{parsed.member_ext('preview')}"
            dest = _voices_dest(ref_filename)
            parsed.extract_member("preview", dest); written.append(dest)
        if ref_filename is None and locked_filename is None:
            raise HTTPException(status_code=400, detail="bundle has no usable audio")

        # ── Consent recording (optional) ──
        consent_filename = None
        consent_path = None
        if "consent_audio" in parsed.members:
            consent_filename = f"{profile_id}_consent{parsed.member_ext('consent_audio')}"
            consent_path = _voices_dest(consent_filename)
            parsed.extract_member("consent_audio", consent_path); written.append(consent_path)

        verified = _consent_verified(parsed, consent_path)
        consent_text = ((parsed.consent or {}).get("consent_text") or "").strip()
        recorded_at = None
        if verified:
            try:
                recorded_at = float(parsed.consent.get("recorded_at"))
            except (TypeError, ValueError):
                recorded_at = time.time()

        is_locked = bool(persona.get("is_locked") and locked_filename)
        ref_for_db = ref_filename or locked_filename  # at least one is set

        def _insert(pid: str):
            with db_conn() as conn:
                conn.execute(
                    """INSERT INTO voice_profiles
                       (id, name, ref_audio_path, ref_text, instruct, language,
                        seed, personality, is_locked, locked_audio_path, created_at,
                        kind, vd_states,
                        verified_own_voice, consent_text, consent_audio_path, consent_recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pid,
                        persona.get("name") or "Imported Voice",
                        ref_for_db,
                        persona.get("ref_text", ""),
                        persona.get("instruct", ""),
                        persona.get("language", "Auto"),
                        persona.get("seed"),
                        persona.get("personality", ""),
                        1 if is_locked else 0,
                        locked_filename or "",
                        time.time(),
                        persona.get("kind") or "clone",
                        persona.get("vd_states"),
                        1 if verified else 0,
                        # Keep the attestation text so the user can re-attest locally,
                        # even when imported unverified.
                        consent_text,
                        consent_filename if verified else "",
                        recorded_at if verified else None,
                    ),
                )

        import sqlite3
        try:
            _insert(profile_id)
        except sqlite3.IntegrityError:
            profile_id = _gen_id()  # one retry on id collision (B20)
            # rename the on-disk files to the new id so they still match the row
            written = _rename_for_new_id(written, profile_id)
            ref_for_db = _retarget(ref_for_db, profile_id)
            locked_filename = _retarget(locked_filename, profile_id)
            consent_filename = _retarget(consent_filename, profile_id)
            _insert(profile_id)

    except HTTPException:
        if not _cleanup(written):
            raise HTTPException(status_code=500, detail="Import failed, and temporary files could not be removed. Close any app using them and retry cleanup.")
        raise
    except Exception:
        cleaned = _cleanup(written)
        logger.warning("Persona import failed")
        detail = ("Import failed; no files were kept." if cleaned else
                  "Import failed, and temporary files could not be removed. Close any app using them and retry cleanup.")
        raise HTTPException(status_code=500, detail=detail)

    event_bus.emit("profiles", {"action": "created", "id": profile_id})
    logger.info("Imported persona %s as %s (verified=%s)", log_safe(persona.get("name")), log_safe(profile_id), verified)

    return {
        "success": True,
        "profile_id": profile_id,
        "name": persona.get("name") or "Imported Voice",
        "kind": persona.get("kind") or "clone",
        "verified_own_voice": verified,
        "preview_only": parsed.preview_only,
        "license_spdx": parsed.license_spdx,
        "watermarked_preview": parsed.watermarked_preview,
        "source_bundle": file.filename,
        "schema_version_ahead": parsed.schema_version_ahead,
    }


def _cleanup(paths: list[str]) -> bool:
    complete = True
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            complete = False
            logger.warning("Persona import temporary-file cleanup did not complete")
    return complete


def _rename_for_new_id(written: list[str], new_id: str) -> list[str]:
    """After an id-collision retry, rename each written file to carry the new id
    (filenames are `{old_id}…`; swap the leading 8-char stem)."""
    out = []
    for p in written:
        d, base = os.path.split(p)
        # base looks like {id}{ext} | {id}_locked{ext} | {id}_consent{ext}
        new_base = new_id + base[8:]
        new_path = os.path.join(d, new_base)
        try:
            safe_replace(p, new_path)
            out.append(new_path)
        except OSError:
            out.append(p)
    return out


def _retarget(filename: str | None, new_id: str) -> str | None:
    return new_id + filename[8:] if filename else filename


# ── Inspect (no-write preview) ──────────────────────────────────────────────


@router.post("/personas/inspect")
async def inspect_persona(file: UploadFile = File(...)):
    """Read a bundle's manifest + consent summary WITHOUT writing any file or row."""
    name = (file.filename or "").lower()
    if not name.endswith(".ovsvoice") and not name.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="File must be a .ovsvoice or .omnivoice bundle")
    content = await file.read(pb.MAX_BUNDLE_BYTES + 1)  # parse rejects anything larger
    try:
        parsed = pb.parse_persona_bundle(content)
    except pb.BundleError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    persona = parsed.manifest.get("persona") or {}
    consent_summary = None
    if parsed.consent:
        has_recording = "consent_audio" in parsed.members
        consent_summary = {
            "verified_claimed": bool(parsed.consent.get("verified_own_voice")),
            "method": parsed.consent.get("method", ""),
            "has_recording": has_recording,
            # would_verify mirrors import's gate, minus the byte-floor check
            # (inspect never extracts to measure size — advisory only).
            "would_verify": has_recording and bool((parsed.consent.get("consent_text") or "").strip()),
        }

    return {
        "format": "omnivoice-legacy" if parsed.is_legacy else pb.OVSVOICE_FORMAT,
        "schema_version": parsed.manifest.get("schema_version", pb.OVSVOICE_SCHEMA_VERSION),
        "name": persona.get("name") or "Imported Voice",
        "kind": persona.get("kind") or "clone",
        "language": persona.get("language", "Auto"),
        "personality": persona.get("personality", ""),
        "is_locked": bool(persona.get("is_locked")),
        "license_spdx": parsed.license_spdx,
        "tags": parsed.manifest.get("tags") or [],
        "preview_only": parsed.preview_only,
        "watermarked_preview": parsed.watermarked_preview,
        "consent": consent_summary,
        "schema_version_ahead": parsed.schema_version_ahead,
    }
