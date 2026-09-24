"""
Standalone transcription endpoint for the Capture / Dictation feature.

Unlike /dub/transcribe/{job_id}, this endpoint is job-free — callers POST
raw audio bytes and get back transcribed text immediately.  Used by:

    • The frontend "Capture" (global hotkey dictation) mode
    • The MCP server's future `transcribe_audio` tool
    • CLI consumers that just want speech-to-text

The ASR engine is whatever `load_active_asr_backend()` returns — WhisperX
by default, or MLX Whisper on Apple Silicon when configured. The *loader*,
not the bare selector: it also runs `ensure_loaded()` and falls through to
the next healthy engine when the selected one has a broken deep import chain
(#1185), which the shallow `is_available()` probe cannot see.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from typing import Optional

router = APIRouter()
logger = logging.getLogger("omnivoice.capture")


def _timing(value):
    """A segment timing, or ``None`` when the engine could not determine one.

    ``dict.get(key, 0)`` hands back a stored ``None`` rather than the default,
    because the key is present — so rounding it raised and took a transcript
    that was otherwise fine down with it (#1904). Pass the null through instead:
    the segment list renders whichever half of the range is known.
    """
    return round(value, 2) if isinstance(value, (int, float)) else None


def _truthy(value: Optional[str]) -> bool:
    """Parse a multipart form flag. Treats '1'/'true'/'yes'/'on'/'auto'
    (any case) as on; everything else — including None — as off."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "auto"}


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    mode: Optional[str] = Form(None),
    refine: Optional[str] = Form(None),
):
    """Transcribe an audio file to text.

    Args:
        audio: The audio file to transcribe.
        language: Optional language hint (not currently used; auto-detected).
        model: Whisper model size (legacy; ignored in dual-mode architecture).
        mode: 'fast' (default) uses MLX Turbo for speed; 'accurate' uses
              the selected ASR engine with word-level timing. 'reference' uses
              the selected ASR engine without word-level timing.
        refine: Opt-in local-LLM cleanup of the final text (disfluencies,
              self-corrections, punctuation) — same pipeline the live
              dictation socket uses. Off by default so MCP/CLI callers don't
              pay LLM latency unless they ask; honours the user's
              Settings → Dictation-refinement config and silently passes
              through when no LLM backend is configured. The raw ``text``
              is always returned; ``refined_text`` is added only when the
              LLM actually changed something.

    Returns:
        {
            "text": "full transcription",
            "refined_text": "cleaned text",   # only when refine=true changed it
            "segments": [ {"start": 0.0, "end": 1.5, "text": "..."}, ... ],
            "language": "en",
            "duration_s": 4.2,
            "transcription_time_s": 0.8,
            "engine": "mlx-whisper"
        }
    """
    import asyncio

    # Save upload to a temp file (all backends need a file path)
    ext = os.path.splitext(audio.filename or "audio.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        content = await audio.read()
        tmp.write(content)
        tmp.close()

        requested_mode = (mode or "").strip().lower()
        use_accurate = requested_mode == "accurate"
        use_active_asr = requested_mode in {"accurate", "reference"}

        # TTS-only install: no ASR model on disk → typed 409 with a download
        # CTA, BEFORE any backend is constructed (the whisper backends
        # auto-download multi-GB weights from HF on first load).
        from services.asr_backend import asr_model_missing_detail, asr_model_missing_error
        missing = await asyncio.to_thread(
            asr_model_missing_error,
            purpose="transcribe" if use_active_asr else "dictation",
            require_installed=requested_mode == "reference",
        )
        if missing is not None and not use_active_asr:
            transcribe_missing = await asyncio.to_thread(
                asr_model_missing_error,
                purpose="transcribe",
                require_installed=True,
            )
            if transcribe_missing is None:
                missing = None
                use_active_asr = True
        if missing is not None:
            raise HTTPException(
                status_code=409,
                detail={**missing, "message": asr_model_missing_detail(missing)},
            )

        def _run():
            if use_active_asr:
                # Accurate mode: full WhisperX with forced alignment —
                # for when the user explicitly wants word-level timing.
                # `load_*`, not `get_*`: the selector alone hands back an
                # engine whose shallow probe passed but whose deep import
                # chain is broken, which then 500s at `.transcribe()`. The
                # loader degrades to the next healthy engine (#1185).
                from services.asr_backend import load_active_asr_backend
                backend = load_active_asr_backend(require_installed=True) if requested_mode == "reference" else load_active_asr_backend()
                result = backend.transcribe(tmp.name, word_timestamps=use_accurate)
            else:
                # Fast mode (default): use the fastest available engine
                # (MLX Turbo on Apple Silicon). Skip word_timestamps for
                # ~30% latency reduction — dictation doesn't need them.
                from services.asr_backend import get_capture_asr_backend
                backend = get_capture_asr_backend()
                result = backend.transcribe(tmp.name, word_timestamps=False)
            sherpa_model_id = getattr(getattr(backend, "spec", None), "id", None)
            return result, backend.id, sherpa_model_id

        from services.model_manager import _gpu_pool
        from services.asr_backend import (
            ASRModelMissingError,
            ASRTimeoutError,
            run_transcribe_guarded,
        )
        t0 = time.perf_counter()
        try:
            result, engine_id, sherpa_model_id = await run_transcribe_guarded(
                _gpu_pool, _run, what="Dictation",
            )
        except ASRTimeoutError as e:
            # Backend is alive — ASR couldn't finish. 504 with guidance, not a
            # silent hang the UI reads as "can't reach the local backend".
            logger.warning("Capture transcription timed out: %s", e)
            raise HTTPException(status_code=504, detail=str(e))
        except ASRModelMissingError as e:
            # Degraded past the broken engine onto one with no weights on
            # disk — same typed 409 (+ download CTA) as the preflight above,
            # never a 500 and never a silent multi-GB auto-download.
            raise HTTPException(
                status_code=409,
                detail={**e.payload, "message": asr_model_missing_detail(e.payload)},
            )
        except Exception as e:
            # Each ASR engine decodes the upload its own way (ffmpeg, PyAV,
            # libsndfile), so a video with no audio stream fails with a
            # different engine-specific error in each. Name the cause once
            # here; the global handler turns NoAudioTrackError into a 422.
            from services.ffmpeg_utils import raise_for_audio_extract_failure
            await asyncio.to_thread(raise_for_audio_extract_failure, str(e), tmp.name)
            raise

        # Some sherpa-onnx NeMo-TDT builds load successfully but decode an
        # entire spoken clip to no tokens. Live dictation already recovers
        # from that failure; the shared file endpoint must do the same because
        # it also powers uploaded transcription and automatic profile text.
        # Retry only through an already-installed fallback, and demote the
        # silent model only when the second recognizer actually heard words.
        initial_text = str(result.get("text") or "").strip()
        if not initial_text and result.get("segments"):
            initial_text = " ".join(
                str(segment.get("text") or "")
                for segment in result["segments"]
                if isinstance(segment, dict)
            ).strip()
        recovered_from = None
        if not use_active_asr and sherpa_model_id and not initial_text:
            fallback_missing = await asyncio.to_thread(
                asr_model_missing_error,
                purpose="dictation",
                skip_sherpa=True,
                require_installed=True,
            )
            if fallback_missing is None:
                def _run_fallback():
                    from services.asr_backend import get_capture_asr_backend

                    fallback = get_capture_asr_backend(skip_sherpa=True)
                    return (
                        fallback.transcribe(tmp.name, word_timestamps=False),
                        fallback.id,
                    )

                try:
                    fallback_result, fallback_engine_id = await run_transcribe_guarded(
                        _gpu_pool,
                        _run_fallback,
                        what="Dictation fallback",
                    )
                    fallback_text = str(fallback_result.get("text") or "").strip()
                    if not fallback_text and fallback_result.get("segments"):
                        fallback_text = " ".join(
                            str(segment.get("text") or "")
                            for segment in fallback_result["segments"]
                            if isinstance(segment, dict)
                        ).strip()
                    if fallback_text:
                        from services.sherpa_dictation import demote_model

                        await asyncio.to_thread(demote_model, sherpa_model_id)
                        result = fallback_result
                        engine_id = fallback_engine_id
                        recovered_from = sherpa_model_id
                        logger.warning(
                            "File transcription recovered from silent dictation model %s "
                            "through installed engine %s",
                            sherpa_model_id,
                            fallback_engine_id,
                        )
                except Exception:
                    logger.exception(
                        "Installed fallback failed after dictation model %s returned no text",
                        sherpa_model_id,
                    )
        elapsed = round(time.perf_counter() - t0, 2)

        # Normalize result shape
        segments = result.get("segments", [])
        full_text = result.get("text", "")
        if not full_text and segments:
            full_text = " ".join(s.get("text", "") for s in segments).strip()

        # Wave 1.1: strip Whisper hallucination loops from the final text.
        # Segments keep the raw recognition so their timings stay truthful.
        from services.refinement import collapse_repetitive_artifacts
        full_text = collapse_repetitive_artifacts(full_text)

        # Cross-transport parity: deterministically polish the final text
        # (leading capital + terminal punctuation) exactly like the live
        # dictation socket (capture_ws) does, so the widget's POST fallback and
        # MCP/CLI callers get the same typed-looking result the WS returns —
        # not the raw "...test" the REST path used to leak. Segments stay raw
        # (their timings/verbatim recognition are the contract).
        from services.text_polish import polish_text
        full_text = polish_text(full_text)

        # Calculate audio duration from segments if available. A segment whose
        # timing the engine could not determine carries end=None (sherpa's
        # _sherpa_result when the sample rate yields no duration, and every
        # plain-text OpenAI-compatible response), so measure only the ones that
        # have a number and keep 0.0 when none do.
        duration = 0.0
        if segments:
            ends = [e for e in (s.get("end") for s in segments) if isinstance(e, (int, float))]
            duration = max(ends) if ends else 0.0

        detected_lang = result.get("language", language or "unknown")

        # Opt-in Wave 2.1 refinement, mirroring the live-dictation socket
        # (capture_ws). Off-thread (it's a network call, not GPU); never
        # raises — maybe_refine swallows failures and a missing LLM into a
        # None pass-through, so the raw text always stands.
        refined_text = None
        if _truthy(refine) and full_text:
            from services.refinement import maybe_refine
            refined = await asyncio.to_thread(maybe_refine, full_text)
            if refined:
                # Polish the refined text too, so both surfaced strings read as
                # typed text (mirrors the raw-vs-refined contract of the WS).
                refined = polish_text(refined)
                if refined != full_text:
                    refined_text = refined

        logger.info(
            "Capture transcription done: engine=%s, elapsed=%.2fs, duration=%.1fs, mode=%s, refined=%s",
            engine_id, elapsed, duration, requested_mode if use_active_asr else "fast",
            refined_text is not None,
        )

        response = {
            "text": full_text,
            "segments": [
                {
                    "start": _timing(s.get("start", 0)),
                    "end": _timing(s.get("end", 0)),
                    "text": s.get("text", "").strip(),
                }
                for s in segments
            ],
            "language": detected_lang,
            "duration_s": round(duration, 2),
            "transcription_time_s": elapsed,
            "engine": engine_id,
        }
        if refined_text is not None:
            response["refined_text"] = refined_text
        if recovered_from is not None:
            response["model_silent"] = recovered_from
        return response
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
