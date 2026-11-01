"""Stories Editor backend — audio encoding.

The Stories Editor stitches its audiobook client-side (Web Audio → WAV). This
endpoint optionally transcodes that WAV to a compressed container (MP3/M4B/OGG)
via ffmpeg, so users can export a small shareable file. Encoding runs through
`spawn_subprocess` (the Windows-`--reload`-safe helper, #122/#175).
"""
import asyncio
import os
import re
import tempfile

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from api.dependencies import require_admin
from fastapi.responses import Response

from services.ffmpeg_utils import find_ffmpeg, spawn_subprocess
from core.http_headers import content_disposition

router = APIRouter(dependencies=[Depends(require_admin)])

# format -> (ffmpeg audio codec, mime type, file extension). Strict whitelist so
# the operator-uploaded `format` can never inject arbitrary ffmpeg arguments.
_FORMATS = {
    "mp3": ("libmp3lame", "audio/mpeg", "mp3"),
    "m4b": ("aac", "audio/mp4", "m4b"),
    "ogg": ("libvorbis", "audio/ogg", "ogg"),
}


@router.post("/stories/encode")
async def stories_encode(
    file: UploadFile = File(...),
    format: str = Form("mp3"),
    bitrate: str = Form("192k"),
):
    """Transcode an uploaded WAV to MP3/M4B/OGG and return the encoded bytes.

    Provenance note (#1169): this endpoint is a pure TRANSCODER, not a
    synthesis producer — it never calls a TTS engine, so it must not call
    mark_synthetic (the upload may be arbitrary user audio, and marking human
    speech as synthetic would be wrong). Audio the Stories Editor stitched
    from VoiceStudio generations is already marked at its producing route, and
    the AudioSeal mark survives the lossy encode here.
    """
    fmt = (format or "mp3").lower()
    if fmt not in _FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
    codec, mime, ext = _FORMATS[fmt]
    if not re.match(r"^\d{2,3}k$", bitrate or ""):
        bitrate = "192k"

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(
            status_code=501,
            detail="ffmpeg not available. Install system ffmpeg or re-run the setup.",
        )

    data = await file.read()
    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(data)
            in_path = f.name
        out_path = f"{in_path[:-4]}.{ext}"
        proc = await spawn_subprocess(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", in_path, "-c:a", codec, "-b:a", bitrate, out_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out_path):
            detail = (stderr.decode(errors="replace") if stderr else "") or "encode failed"
            raise HTTPException(status_code=500, detail=detail[:300])
        with open(out_path, "rb") as fh:
            encoded = fh.read()
        return Response(
            content=encoded,
            media_type=mime,
            headers={"Content-Disposition": content_disposition(f"story.{ext}")},
        )
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
