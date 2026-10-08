"""Speech-to-speech voice changer — Studio's Convert method (POST /convert).

The user drops (or records) a source clip, picks an existing voice profile,
and gets the same words back in that profile's voice: the active ASR backend
transcribes the clip (no word timestamps — the text is all we need), the
active TTS engine re-synthesizes it conditioned on the profile's reference
audio, and — by default — the take is pitch-preservingly time-stretched
(ffmpeg atempo, clamped to one well-behaved 0.5–2.0 stage) so it lands near
the source clip's duration.

Deliberately reuses the /generate choke points instead of re-deriving them:

* profile row → conditioning via ``generation._resolve_profile_conditioning``
  (lock wins, ``kind`` authoritative, #533 language fill),
* engine resolution via ``services.tts_backend.resolve_generation_backend``
  (never a silent OmniVoice fallback; ``require_cloning=True`` refuses
  clone-less engines with the actionable switch-engine message),
* synthesis via ``generation._run_backend_inference`` on the guarded GPU
  pool (#730 bound + reset; busy/timeout → retryable 503),
* provenance + persistence via ``services.watermark.mark_synthetic_async``
  and ``generation._finalize_generation`` (watermark → WAV in OUTPUTS_DIR →
  history row → retention prune), marked AFTER the stretch so the take users
  keep carries exactly one whole-take mark.

Local-first: no network calls; ASR-model-less installs get the same typed
409 download CTA as /transcribe; a backend mid-shutdown surfaces the global
503 ``[shutting_down]`` (ModelLoadInterruptedByShutdown → main.py handler).
Reachability matches /generate: loopback bind by default, with the shared
network-share PIN / API-key middleware gating any non-loopback exposure.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import tempfile
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from core.path_security import upload_suffix

router = APIRouter()
logger = logging.getLogger("omnivoice.convert")

#: ffmpeg's atempo filter is well-behaved in [0.5, 2.0] per stage. Convert
#: clamps to ONE stage by design: needing more than 2× either way means the
#: synthesized speech differs so much from the source that "matching" it
#: would produce chipmunk/slow-motion artifacts worse than the mismatch.
ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0

#: Within this relative tolerance the durations already match — stretching
#: would resample the whole take for an inaudible gain.
_MATCH_TOLERANCE = 0.02

#: Convert clips are short conversational inputs, not long-form media. Stream
#: them to disk in bounded chunks so a network-share client cannot make the
#: backend materialize an arbitrarily large multipart upload in memory.
_MAX_SOURCE_AUDIO_BYTES = 64 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _copy_source_upload(audio: UploadFile, destination) -> int:
    """Stream ``audio`` into ``destination`` with the Convert upload cap."""
    total = 0
    while True:
        chunk = await audio.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            return total
        total += len(chunk)
        if total > _MAX_SOURCE_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Source audio is too large (maximum 64 MB).",
            )
        destination.write(chunk)


def _clamped_tempo_ratio(tts_duration_s: float, source_duration_s: float) -> "float | None":
    """The atempo ratio that fits the take into the source duration, or None.

    ratio > 1 speeds the take up (it came out longer than the source),
    ratio < 1 slows it down. Clamped to a single atempo stage's [0.5, 2.0];
    None when either duration is unusable or they already match.
    """
    if not source_duration_s or source_duration_s <= 0:
        return None
    if not tts_duration_s or tts_duration_s <= 0:
        return None
    ratio = tts_duration_s / source_duration_s
    if abs(ratio - 1.0) <= _MATCH_TOLERANCE:
        return None
    return min(ATEMPO_MAX, max(ATEMPO_MIN, ratio))


async def _match_source_duration(audio_tensor, sample_rate: int, source_duration_s: float):
    """Best-effort pitch-preserving stretch of the take toward the source
    clip's duration. Returns the input unchanged when no stretch is needed
    or ffmpeg fails — a duration mismatch is better than a failed convert."""
    n_samples = int(audio_tensor.shape[-1])
    ratio = _clamped_tempo_ratio(n_samples / sample_rate, source_duration_s)
    if ratio is None:
        return audio_tensor
    target_samples = max(1, int(round(n_samples / ratio)))
    from services.ffmpeg_utils import _pitch_preserving_stretch
    try:
        return await _pitch_preserving_stretch(audio_tensor, target_samples, sample_rate)
    except Exception as e:  # noqa: BLE001 — stretch is opt-in polish, never fatal
        logger.warning("duration match skipped — atempo stretch failed: %s", e)
        return audio_tensor


async def _transcribe_source(tmp_path: str, *, source_lease=None) -> dict:
    """Active-ASR transcription of the uploaded clip (no word timestamps).

    Mirrors POST /transcribe: typed 409 + download CTA before any backend
    is constructed (never a silent multi-GB auto-download), the guarded GPU
    pool dispatch (#730), 504 on timeout, and the same 409 when the loader
    degrades onto an engine with no weights on disk (#1185).
    """
    from services.asr_backend import (
        ASRModelMissingError,
        ASRTimeoutError,
        asr_model_missing_detail,
        asr_model_missing_error,
        run_transcribe_guarded,
    )

    missing = await asyncio.to_thread(asr_model_missing_error, purpose="transcribe")
    if missing is not None:
        raise HTTPException(
            status_code=409,
            detail={**missing, "message": asr_model_missing_detail(missing)},
        )

    def _run():
        # `load_*`, not `get_*`: the loader runs ensure_loaded() and degrades
        # past an engine whose deep import chain is broken (#1185).
        from services.asr_backend import load_active_asr_backend
        backend = load_active_asr_backend()
        return backend.transcribe(tmp_path, word_timestamps=False)

    from services.model_manager import _gpu_pool
    release = source_lease.acquire() if source_lease is not None else None
    abandoned = False
    try:
        return await run_transcribe_guarded(
            _gpu_pool,
            _run,
            what="Voice convert",
            on_abandon=release,
        )
    except asyncio.CancelledError:
        # The guard now owns the lease token until the native worker drains.
        abandoned = True
        raise
    except ASRTimeoutError as e:
        abandoned = True
        logger.warning("Convert transcription timed out: %s", e)
        raise HTTPException(status_code=504, detail=str(e))
    except ASRModelMissingError as e:
        raise HTTPException(
            status_code=409,
            detail={**e.payload, "message": asr_model_missing_detail(e.payload)},
        )
    finally:
        if release is not None and not abandoned:
            release()


@router.post("/convert")
async def convert_speech(
    audio: UploadFile = File(...),
    profile_id: str = Form(...),
    match_duration: bool = Form(True),
):
    """Convert a spoken clip into an existing voice profile's voice.

    Multipart form: ``audio`` (the source clip), ``profile_id`` (an existing
    voice profile), optional ``match_duration`` (default on — atempo the take
    toward the source clip's length, clamped to 0.5–2.0×).

    Returns JSON ``{audio_url, text, duration_s, id}`` — the take is saved to
    OUTPUTS_DIR and served from the ``/audio`` mount like every other take.
    """
    from core.db import db_conn
    from api.routers.generation import _resolve_profile_conditioning, _TempReferenceLease

    # ── Profile first: strict 404, unlike /generate's silent skip — Convert
    # has no meaning without a target voice.
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (profile_id,)
        ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="That voice profile doesn't exist. It may have been deleted from another tab.",
        )
    cond = _resolve_profile_conditioning(row)

    # ── Save the upload before loading an engine. Every ASR backend (and
    # ffprobe) needs a file path; the bounded streaming copy rejects oversized
    # network-share requests without materializing them in process memory or
    # starting heavyweight model work.
    ext = upload_suffix(audio.filename, ".wav") or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    source_lease = None
    try:
        try:
            await _copy_source_upload(audio, tmp)
        finally:
            tmp.close()
        source_lease = _TempReferenceLease(tmp.name)

        # ── Engine gate before ASR/TTS work: the shared resolver refuses a
        # clone-less engine with the actionable switch-engine message (→ 400),
        # and a backend mid-shutdown raises ModelLoadInterruptedByShutdown out
        # of the model load → the global 503 [shutting_down] handler.
        from services.tts_backend import active_backend_id, resolve_generation_backend
        try:
            backend = await resolve_generation_backend(
                require_cloning=True, cloning_purpose="voice conversion",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        result = await _transcribe_source(tmp.name, source_lease=source_lease)

        segments = result.get("segments", [])
        text = result.get("text", "")
        if not text and segments:
            text = " ".join(s.get("text", "") for s in segments).strip()
        # Same final-text hygiene as /transcribe: strip Whisper hallucination
        # loops, then deterministic polish (leading capital + terminal
        # punctuation) so the TTS input reads as typed text.
        from services.refinement import collapse_repetitive_artifacts
        from services.text_polish import polish_text
        text = polish_text(collapse_repetitive_artifacts(text))
        if not text or not text.strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    "No speech was recognized in the source clip, so there is "
                    "nothing to convert. Record or drop a clip with clear, "
                    "audible speech and try again."
                ),
            )

        # #308/#1032 parity with /generate: a clone profile saved without a
        # transcript conditions better when its reference clip is transcribed,
        # and that transcript is cached onto the row so it happens ONCE, not
        # per convert. Best-effort exactly like /generate — a timeout/failure
        # degrades to ref_text=None and the engine's own fallback. The ASR
        # model is already warm here (the source transcribe above just used it).
        if cond["ref_audio_path"] and not cond["ref_text"]:
            from api.routers.generation import (
                _generate_timeout_s,
                _persist_profile_ref_text,
            )
            from services.asr_backend import transcribe_reference
            from services.model_manager import run_on_gpu_pool_guarded
            try:
                cond["ref_text"] = await run_on_gpu_pool_guarded(
                    functools.partial(transcribe_reference, cond["ref_audio_path"]),
                    what="Reference transcribe",
                    timeout=_generate_timeout_s(""),
                )
            except TimeoutError as e:
                logger.warning(
                    "reference transcribe hung (%s); using engine ASR fallback", e,
                )
                cond["ref_text"] = None
            if cond["ref_text"] and cond["persist_ref_text"]:
                _persist_profile_ref_text(profile_id, cond["ref_text"])

        # Source duration for the optional match: the container's own length
        # (ffprobe), falling back to the last ASR segment end. Best-effort —
        # None just skips the stretch.
        source_duration_s = None
        if match_duration:
            from services.ffmpeg_utils import probe_duration
            source_duration_s = await probe_duration(
                tmp.name, allowed_root=os.path.dirname(tmp.name),
            )
            if not source_duration_s and segments:
                source_duration_s = max((s.get("end", 0) or 0) for s in segments) or None

        # ── Same text choke point as /generate: engine-agnostic normalization
        # (numbers→words, junk strip) on the fully resolved language.
        from services.text_normalization import normalize_for_tts
        language = cond["language"]
        text = normalize_for_tts(text, language)

        used_seed = cond["seed"]
        if used_seed is None:
            import random
            used_seed = random.randint(0, 2**31 - 1)

        from api.routers.generation import (
            _finalize_generation,
            _generate_timeout_s,
            _run_backend_inference,
        )
        from services.model_manager import (
            GpuJobTimeoutError,
            GpuPoolBusyError,
            run_on_gpu_pool_guarded,
        )
        from core.device_caps import detect_host_caps
        from services.engine_routing import runtime_compute_profile_async
        compute_profile = await runtime_compute_profile_async(
            backend, detect_host_caps()
        )
        if compute_profile["routing_status"] == "unavailable":
            raise HTTPException(
                status_code=400,
                detail=compute_profile["routing_reason"],
            )

        start_time = time.time()
        from services.performance_profiles import tts_defaults
        _profile_defaults = tts_defaults(active_backend_id())
        _render = functools.partial(
            _run_backend_inference,
            backend, text, language, cond["ref_audio_path"], cond["ref_text"],
            cond["instruct"],
            None,        # duration — the model picks; match_duration owns pacing
            _profile_defaults.get("num_step", 16), 2.0,
            1.0,         # speed
            True, _profile_defaults.get("postprocess_output", True),
            used_seed,
        )
        try:
            audio_tensor = await run_on_gpu_pool_guarded(
                _render,
                what="Voice convert",
                timeout=_generate_timeout_s(
                    text,
                    engine=backend,
                    execution_device=compute_profile["effective_device"],
                    min_vram_gb=compute_profile["min_vram_gb"],
                    hardware_family=compute_profile.get("runtime_hardware_family"),
                    vram_gb=compute_profile.get("runtime_vram_gb"),
                ),
                min_vram_gb=compute_profile["min_vram_gb"],
            )
        except GpuPoolBusyError as e:
            raise HTTPException(
                status_code=503, detail=str(e),
                headers={"Retry-After": str(e.retry_after),
                         "X-OmniVoice-Retryable": "true"},
            ) from e
        except GpuJobTimeoutError as e:
            raise HTTPException(
                status_code=503, detail=str(e),
                headers={"Retry-After": "30", "X-OmniVoice-Retryable": "true"},
            ) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        sample_rate = backend.sample_rate

        if match_duration and source_duration_s:
            audio_tensor = await _match_source_duration(
                audio_tensor, sample_rate, source_duration_s,
            )

        # Provenance mark AFTER the stretch (one whole-take mark on the audio
        # the user actually keeps), then the shared finalize tail — WAV in
        # OUTPUTS_DIR, self-healing history row, retention prune, event emit.
        from services.watermark import mark_synthetic_async
        audio_tensor = await mark_synthetic_async(
            audio_tensor, sample_rate, context="convert.finalize",
        )
        _, meta = await _finalize_generation(
            audio_tensor, sample_rate, text=text, history_mode="convert",
            ref_audio_path=cond["ref_audio_path"], language=language,
            instruct=cond["instruct"], resolved_profile_id=profile_id,
            used_seed=used_seed, start_time=start_time,
            already_marked=True,
        )

        return {
            "id": meta["id"],
            "audio_url": f"/audio/{meta['filename']}",
            "text": text,
            "duration_s": meta["duration"],
            "gen_time_s": meta["gen_time"],
        }
    finally:
        if source_lease is not None:
            source_lease.finish_request()
        else:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
