"""SoniTranslate engine endpoints.

Provides status, install, start/stop, and dub operations for the
SoniTranslate sidecar integration.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import require_admin, require_desktop, require_native_access
from services import sonitranslate as soni

router = APIRouter(prefix="/engines/sonitranslate", tags=["SoniTranslate"])
logger = logging.getLogger("omnivoice.api")


# ── Status ──────────────────────────────────────────────────────────────


@router.get("/status")
def sonitranslate_status():
    """Check SoniTranslate availability."""
    return soni.status()


# ── Install ─────────────────────────────────────────────────────────────

# install/start/stop are managed-sidecar operations and carry the same
# admin + desktop gate as POST /engines/sidecar/{engine_id}/install: install
# clones mutable upstream source and pip-installs it, and start() spawns that
# checkout with HF_TOKEN in the child env.  An API key does not make that
# supply-chain path safe to trigger remotely (docs/api-auth.md).


@router.post(
    "/install",
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)
async def sonitranslate_install():
    """Clone and set up SoniTranslate (heavy — ~15GB with models)."""
    try:
        result = await soni.install()
        return result
    except Exception as e:
        logger.exception("SoniTranslate install failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Start / Stop ────────────────────────────────────────────────────────


@router.post(
    "/start",
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)
async def sonitranslate_start():
    """Start the SoniTranslate Gradio server."""
    try:
        result = await soni.start()
        return result
    except Exception as e:
        logger.exception("SoniTranslate start failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/stop",
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)
async def sonitranslate_stop():
    """Stop the SoniTranslate Gradio server."""
    result = await soni.stop()
    return result


# ── Dub ─────────────────────────────────────────────────────────────────


class DubRequest(BaseModel):
    video_authorization: str
    target_language: str = "Spanish (es)"
    source_language: str = "Automatic detection"
    tts_voice: str = "es-ES-AlvaroNeural-Male"
    max_speakers: int = 1
    output_authorization: str | None = None


@router.post("/dub", dependencies=[Depends(require_native_access)])
async def sonitranslate_dub(body: DubRequest):
    """Run full dubbing pipeline via SoniTranslate.

    Transcribes, translates, generates TTS, and mixes audio.
    Returns the path to the dubbed output video.

    KNOWN PROVENANCE GAP (#1169, documented — not silently ignored): the
    dubbed audio is synthesized and muxed entirely inside the external
    SoniTranslate sidecar (its own venv + gradio pipeline, Edge-TTS voices),
    which hands back a finished video file. VoiceStudio's tensor-stage
    mark_synthetic chokepoint never sees that audio; marking it would require
    a demux → embed → re-mux post-pass on the sidecar's output, which is a
    lossy re-encode of a pipeline we don't control. This opt-in engine
    (explicit install + start) is therefore NOT covered by the invisible
    AudioSeal provenance mark that every built-in synthesis path carries.
    """
    try:
        from core.path_authorization import PathAuthorizationError, consume

        try:
            video_path = consume(body.video_authorization, "soni_input")
            output_dir = (
                consume(body.output_authorization, "soni_output_dir")
                if body.output_authorization
                else None
            )
        except PathAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        result = await soni.dub_video(
            video_path=video_path,
            target_language=body.target_language,
            source_language=body.source_language,
            tts_voice=body.tts_voice,
            max_speakers=body.max_speakers,
            output_dir=output_dir,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("SoniTranslate dub failed")
        raise HTTPException(status_code=500, detail=str(e))
