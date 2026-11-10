"""Sound-effect generation — desktop/web only (issue #3577).

``GET  /api/sfx/status``    installed? usable on this host? why not?
``POST /api/sfx/generate``  prompt + duration (+ seed) -> a WAV in OUTPUTS_DIR

Backed by the opt-in MOSS-SoundEffect v2.0 sidecar (``engines.moss_sfx``). It
is not a speech engine and is not in the TTS registry, so no voice or engine
picker lists it. Nothing downloads until the user clicks Install, which goes
through the shared ``/engines/sidecar/moss-soundeffect/install`` job routes.

Not part of the mobile contract: phones do not generate sound effects, and the
mobile apps ignore these routes.

Conventions shared with /generate and /convert: the take is watermark-marked
(``services.watermark``), saved to OUTPUTS_DIR, recorded in history and served
from ``/audio``; synthesis runs on the guarded GPU pool (busy/timeout -> a
retryable 503); a client that disconnects cancels the request.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import consumer_generation, require_consumer_reads_admin_writes

router = APIRouter(dependencies=[Depends(require_consumer_reads_admin_writes)])
logger = logging.getLogger("omnivoice.sfx")

ENGINE_ID = "moss-soundeffect"
MODEL_REPO = "OpenMOSS-Team/MOSS-SoundEffect-v2.0"
LICENSE = "Apache-2.0"


class SfxRequest(BaseModel):
    prompt: str = Field(..., description="What the sound is, in English or Chinese.")
    duration: float = Field(10.0, description="Seconds of audio, 1 to 30.")
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


def _limits() -> dict:
    from engines.moss_sfx import DEFAULT_SECONDS, MAX_PROMPT_CHARS, MAX_SECONDS, MIN_SECONDS

    return {
        "min_seconds": MIN_SECONDS,
        "max_seconds": MAX_SECONDS,
        "default_seconds": DEFAULT_SECONDS,
        "max_prompt_chars": MAX_PROMPT_CHARS,
    }


def _status() -> dict:
    """Install state plus a plain-language reason when generation is blocked."""
    from engines.moss_sfx import SFX_WEIGHTS_BYTES, MossSfxBackend, host_support

    host_ok, host_reason = host_support()
    reason_code, reason = MossSfxBackend.availability()
    available = reason_code == "ok"
    installed = False
    if host_ok:
        try:
            from engines.moss_sfx.bootstrap import is_sfx_installed

            installed = bool(is_sfx_installed())
        except Exception:  # noqa: BLE001 - a probe failure reads as "not installed"
            installed = False
    return {
        "engine_id": ENGINE_ID,
        "installed": installed,
        "available": bool(available),
        "supported": bool(host_ok),
        # Why Generate is disabled, or null when it can run.
        "reason": None if available else reason,
        # Stable code for the same fact (host_unsupported, not_installed, weights_missing,
        # sidecar_missing); the app translates it and never shows ``reason``.
        "reason_code": None if available else reason_code,
        "install_allowed": bool(host_ok),
        "download_bytes": SFX_WEIGHTS_BYTES,
        "license": LICENSE,
        "model_repo": MODEL_REPO,
        **_limits(),
    }


@router.get("/api/sfx/status")
def sfx_status():
    return _status()


def _fail(status: int, code: str, message: str) -> HTTPException:
    """Error with a stable ``code`` (the app translates it) and English ``message`` (logs, API clients)."""
    return HTTPException(status_code=status, detail={"code": code, "error": code, "message": message})


def _clean_prompt(prompt: str) -> str:
    text = " ".join(str(prompt or "").split())
    limits = _limits()
    if not text:
        raise _fail(422, "sfx_prompt_required", "Describe the sound you want first.")
    if len(text) > limits["max_prompt_chars"]:
        raise _fail(
            422, "sfx_prompt_too_long",
            f"The description is too long (maximum {limits['max_prompt_chars']} characters).",
        )
    return text


def _checked_duration(duration: float) -> float:
    limits = _limits()
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        seconds = float("nan")
    if not seconds == seconds or seconds < limits["min_seconds"] or seconds > limits["max_seconds"]:
        raise _fail(
            422, "sfx_bad_duration",
            f"Duration must be between {limits['min_seconds']:g} and "
            f"{limits['max_seconds']:g} seconds.",
        )
    return round(seconds, 1)


@router.post("/api/sfx/generate")
@consumer_generation
async def sfx_generate(req: SfxRequest):
    """Generate one sound effect. Returns ``{id, audio_url, duration_s, gen_time_s, seed, engine}``."""
    from api.routers.generation import _finalize_generation, _generate_timeout_s
    from engines.moss_sfx import get_backend
    from services.model_manager import GpuJobTimeoutError, GpuPoolBusyError, run_on_gpu_pool_guarded

    prompt = _clean_prompt(req.prompt)
    seconds = _checked_duration(req.duration)

    status = await asyncio.to_thread(_status)
    if not status["supported"]:
        raise _fail(409, "sfx_unsupported_host", status["reason"])
    if not status["available"]:
        raise _fail(
            409, "sfx_not_installed",
            "Sound effects are not installed. Install them from Tools > Sound effects.",
        )

    seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
    backend = get_backend()
    start_time = time.time()
    try:
        audio_tensor = await run_on_gpu_pool_guarded(
            functools.partial(backend.generate, prompt, seconds=seconds, seed=seed),
            what="Sound effect",
            # Budgeted like a long synthesis: cold load of ~11 GB plus the diffusion steps.
            timeout=max(_generate_timeout_s("x" * 2000, engine=backend), 900.0),
        )
    except GpuPoolBusyError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "sfx_busy", "error": "sfx_busy", "message": str(e)},
            headers={"Retry-After": str(e.retry_after), "X-OmniVoice-Retryable": "true"},
        ) from e
    except GpuJobTimeoutError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "sfx_timeout", "error": "sfx_timeout", "message": str(e)},
            headers={"Retry-After": "30", "X-OmniVoice-Retryable": "true"},
        ) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - OOM or a sidecar crash must not take the app down
        logger.warning("sound-effect generation failed: %s", e)
        detail = str(e)
        if "out of memory" in detail.lower():
            code = "sfx_out_of_memory"
            message = (
                "The GPU ran out of memory generating this sound effect. Close other "
                "GPU-heavy apps or try a shorter duration."
            )
        else:
            code = "sfx_failed"
            message = "Sound-effect generation failed. Your other engines are unaffected; try again."
        raise HTTPException(status_code=500, detail={"code": code, "error": code, "message": message}) from e

    from services.watermark import mark_synthetic_async

    sample_rate = backend.sample_rate
    audio_tensor = await mark_synthetic_async(audio_tensor, sample_rate, context="sfx.finalize")
    _, meta = await _finalize_generation(
        audio_tensor, sample_rate, text=prompt, history_mode="sfx",
        ref_audio_path=None, language=None, instruct=None, resolved_profile_id=None,
        used_seed=seed, start_time=start_time, already_marked=True, engine_id=ENGINE_ID,
    )
    return {
        "id": meta["id"],
        "audio_url": f"/audio/{meta['filename']}",
        "duration_s": meta["duration"],
        "gen_time_s": meta["gen_time"],
        "seed": seed,
        "engine": ENGINE_ID,
    }
