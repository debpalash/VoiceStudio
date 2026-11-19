import os
import re
import uuid
import time
import shutil
import subprocess
import platform
from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import require_native_access, require_loopback, require_consumer
from core.db import db_conn
from core.config import DATA_DIR, OUTPUTS_DIR
from core import event_bus
from core.path_authorization import PathAuthorizationError, consume
from core.path_security import UnsafePath, resolve_within, safe_filename
from schemas.requests import ExportRequest, ExportRecordRequest, RevealRequest

router = APIRouter()


def _authorized_destination(token: str) -> str:
    """Consume a native save-dialog capability and validate its destination."""
    try:
        raw = consume(token, "dub_export")
    except PathAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not raw or not raw.strip() or not os.path.isabs(os.path.expanduser(raw)):
        raise HTTPException(status_code=400, detail="The selected destination is invalid.")
    dest = os.path.realpath(os.path.expanduser(raw))
    parent = os.path.dirname(dest)
    if not parent or not os.path.isdir(parent):
        raise HTTPException(
            status_code=400,
            detail="That destination folder doesn't exist yet. Create it first, or pick an existing one.",
        )
    return dest


def _safe_source(filename: str) -> str:
    """Resolve a source filename against OUTPUTS_DIR / dub outputs, blocking traversal."""
    try:
        base = safe_filename(filename)
    except UnsafePath as exc:
        raise HTTPException(
            status_code=400,
            detail="The file to export has an unexpected name. Try re-generating the audio and exporting again.",
        ) from exc
    for root in (OUTPUTS_DIR, os.path.join("dub", "outputs")):
        try:
            candidate = resolve_within(root, base)
        except UnsafePath:
            continue
        if candidate.is_file():
            return str(candidate)
    raise HTTPException(
        status_code=404,
        detail="That file isn't on disk anymore — it may have been cleaned up. Regenerate and try again.",
    )


@router.post("/export", dependencies=[Depends(require_native_access)])
def export_file(req: ExportRequest):
    src = _safe_source(req.source_filename)
    dest = _authorized_destination(req.authorization)
    try:
        # Video exports: overlay VoiceStudio logo if visible watermark is enabled
        if src.lower().endswith(".mp4"):
            from services.watermark import is_visible_video_enabled, get_ffmpeg_overlay_args
            from services.ffmpeg_utils import find_ffmpeg, local_inputs_only
            logo_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "logo.png")
            logo_path = os.path.realpath(logo_path)
            if is_visible_video_enabled() and os.path.exists(logo_path):
                overlay_args = get_ffmpeg_overlay_args(logo_path)
                # Resolve ffmpeg the way every other call site does. The bare
                # name only works when a system ffmpeg is on PATH: the binary
                # imageio-ffmpeg ships — the app's default source — is named
                # `ffmpeg-<platform>-v<version>`, so spawning "ffmpeg" raised
                # FileNotFoundError and the watermark the user asked for was
                # silently dropped from the exported video.
                ffmpeg = find_ffmpeg()
                if overlay_args and ffmpeg:
                    try:
                        subprocess.run(
                            local_inputs_only(
                                [ffmpeg, "-y", "-i", src, "-i", logo_path]
                                + overlay_args
                                + ["-codec:a", "copy", dest]
                            ),
                            check=True,
                            capture_output=True,
                            timeout=120,
                        )
                    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                        # Fallback: plain copy if ffmpeg overlay fails
                        shutil.copy2(src, dest)
                else:
                    shutil.copy2(src, dest)
            else:
                shutil.copy2(src, dest)
        else:
            shutil.copy2(src, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    export_id = str(uuid.uuid4())[:8]
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO export_history (id, filename, destination_path, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (export_id, req.source_filename, dest, req.mode, time.time()),
        )
    event_bus.emit("export_history", {"action": "exported", "id": export_id})
    return {"success": True, "id": export_id}


_RECORD_PATH_MAX = 4096
_RECORD_MODES = {"audio", "video", "file"}
# Every C0 control character plus DEL: a path with one cannot have come from a
# native save dialog, and it corrupts whatever renders the history.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_SEPARATORS = re.compile(r"[\\/]")


def _recorded_destination(raw: str) -> str:
    """The destination a completed export reports: bounded, control-free, display-only.

    The path is returned exactly as the desktop saved it — a POSIX name may end in
    a space — so the history shows the file the user actually got. Nothing opens
    it (`/export/reveal` resolves against the data directory instead), but it must
    not carry kilobytes of text, control characters, or an upward traversal.
    """
    path = raw or ""
    if not path.strip() or len(path) > _RECORD_PATH_MAX:
        raise HTTPException(
            status_code=400, detail="The destination path is missing or too long."
        )
    if _CONTROL_CHARACTER.search(path):
        raise HTTPException(
            status_code=400, detail="The destination path contains control characters."
        )
    if ".." in _SEPARATORS.split(path):
        raise HTTPException(
            status_code=400, detail="The destination path may not traverse upwards."
        )
    return path


def _recorded_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode not in _RECORD_MODES:
        raise HTTPException(status_code=400, detail="Unknown export mode.")
    return mode


@router.post("/export/record", dependencies=[Depends(require_consumer)])
def record_export(req: ExportRecordRequest):
    export_id = str(uuid.uuid4())[:8]
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO export_history (id, filename, destination_path, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                export_id,
                req.filename,
                _recorded_destination(req.destination_path),
                _recorded_mode(req.mode),
                time.time(),
            ),
        )
    event_bus.emit("export_history", {"action": "recorded", "id": export_id})
    return {"success": True, "id": export_id}


@router.delete("/export/history/{export_id}", dependencies=[Depends(require_loopback)])
def delete_export_history(export_id: str):
    """Forget an export record without touching its source or destination file."""
    with db_conn() as conn:
        conn.execute("DELETE FROM export_history WHERE id = ?", (export_id,))
    event_bus.emit("export_history", {"action": "deleted", "id": export_id})
    return {"deleted": export_id}


@router.get("/export/history", dependencies=[Depends(require_consumer)])
def get_export_history(request: Request):
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM export_history ORDER BY created_at DESC LIMIT 50").fetchall()
    from core.auth import PrincipalKind, principal_for

    records = [dict(r) for r in rows]
    # The destination is a path on this host. Only clients that are the machine
    # itself get it back: a browser on the trusted network or a PIN client is a
    # different device, and the anonymous kind was the only case redacted before.
    local_kinds = {
        PrincipalKind.LOOPBACK,
        PrincipalKind.API_KEY,
        PrincipalKind.ADMIN_SESSION,
    }
    if principal_for(request).kind not in local_kinds:
        for record in records:
            record["destination_path"] = ""
    return records


@router.post("/export/reveal", dependencies=[Depends(require_native_access)])
def reveal_in_folder(req: RevealRequest):
    # Desktop clients reveal arbitrary user-selected export destinations in
    # the native Tauri process. This HTTP fallback is deliberately limited to
    # server-owned data so a remote/browser caller cannot make the host open
    # an attacker-chosen path.
    if not req.path or not req.path.strip():
        raise HTTPException(
            status_code=400,
            detail="No path was provided — nothing to reveal.",
        )
    try:
        target_path = resolve_within(DATA_DIR, req.path)
    except UnsafePath as exc:
        raise HTTPException(status_code=403, detail="That path cannot be opened remotely.") from exc
    if not target_path.exists():
        raise HTTPException(
            status_code=404,
            detail="That file or folder is no longer on disk. It may have been moved or deleted.",
        )

    target = str(target_path)
    folder = target if target_path.is_dir() else str(target_path.parent)
    system = platform.system()
    try:
        if system == "Darwin":
            if target_path.is_file():
                subprocess.Popen(["open", "-R", target])
            else:
                subprocess.Popen(["open", folder])
        elif system == "Windows":
            if target_path.is_file():
                subprocess.Popen(["explorer", "/select,", target.replace("/", "\\")])
            else:
                subprocess.Popen(["explorer", folder.replace("/", "\\")])
        else:
            subprocess.Popen(["xdg-open", folder])
        return {"success": True}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
