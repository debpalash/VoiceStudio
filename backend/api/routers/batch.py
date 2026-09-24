"""Batch dubbing queue — POST videos with settings, process sequentially.

This is a lightweight batch orchestrator. Each job is a dub project that
runs through the same ingest→transcribe→translate→generate pipeline as
a manual dub, but driven by the queue instead of the UI.

The queue is in-memory (lives for the process lifetime). Jobs persist to
the SQLite `jobs` table for history, but the queue itself restarts empty
on backend restart — intentional, since GPU jobs can't be safely resumed.
"""
import os
import json
import shutil
import uuid
import time
import asyncio
import logging
from typing import Optional, List

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.config import DATA_DIR
from core import failure
from core.logging_utils import log_safe
from core.file_cleanup import FileCleanupError, unlink_if_present
from services.dub_batching import (
    BATCH_WIDTH_ENV,
    batch_timeout_s as _batch_timeout_s,
    native_batch_width as _native_batch_width,
)
from services import gpu_gateway
from services.segment_bundle import extract_segment_wavs, remove_segment_wavs
from services.tts_backend import active_backend_id, resolve_generation_backend

router = APIRouter()
logger = logging.getLogger("omnivoice.batch")

# Compatibility values emitted by the established Tauri Batch picker. They
# are taxonomy tokens, not arbitrary prose, and are resolved server-side so
# native watch-folder uploads and both desktop clients use the same voice.
_BATCH_PRESET_INSTRUCT = {
    "narrator": "male, middle-aged, low pitch, british accent",
    "excited_child": "child, high pitch",
    "anxious_whisper": "young adult, whisper",
    "surprised_woman": "female, young adult, high pitch",
    "elderly_story": "male, elderly, very low pitch",
    "sichuan": "female, young adult, moderate pitch, \u56db\u5ddd\u8bdd",
}

# ── In-memory queue ─────────────────────────────────────────────────────

_queue: asyncio.Queue = None       # Lazily initialised
_worker_task: asyncio.Task = None  # Background consumer
_processing_job_ids: set[str] = set()
_jobs: dict = {}                   # job_id → status dict


class BatchJobStatus(BaseModel):
    id: str
    status: str  # "queued" | "running" | "done" | "failed" | "cancelled"
    filename: str
    langs: List[str]
    voice_id: Optional[str] = None
    preserve_bg: bool = True
    translation_provider: Optional[str] = None
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    docs_topic: Optional[str] = None
    progress: Optional[dict] = None
    attempts: int = 1
    retry_ready: bool = True
    setup_required: Optional[dict] = None


def _ensure_queue():
    """Lazy-init the asyncio queue + worker on first use."""
    global _queue, _worker_task
    if _queue is None:
        _queue = asyncio.Queue()
        _worker_task = asyncio.ensure_future(_worker())


async def _worker():
    """Process jobs one at a time from the queue."""
    while True:
        job_id = await _queue.get()
        job = _jobs.get(job_id)
        if not job or job["status"] == "cancelled":
            _queue.task_done()
            continue

        job["status"] = "running"
        job["started_at"] = time.time()
        _processing_job_ids.add(job_id)
        logger.info("Batch job %s starting: %s", job_id, job["filename"])

        try:
            await _run_batch_pipeline(job_id, job)
            if job["status"] != "cancelled":
                job["status"] = "done"
                job["finished_at"] = time.time()
                logger.info(
                    "Batch job %s completed in %.1fs",
                    job_id, job["finished_at"] - job["started_at"],
                )
        except asyncio.CancelledError:
            # Task cancellation always means SHUTDOWN: the job-level cancel
            # endpoint only flips job["status"] — nothing ever cancels this
            # task to abort a single job. Swallowing the CancelledError here
            # made the worker unkillable (the while-loop re-entered
            # _queue.get() and event-loop teardown hung forever in
            # _cancel_all_tasks waiting on a task that never finishes). Mark
            # the in-flight job, then let the cancellation propagate.
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            raise
        except Exception as e:
            job["status"] = "failed"
            # plan-04 (#131): guaranteed non-empty, structured reason.
            failed = failure.build_failure(e, stage="batch", include_diagnostic=False)
            job["error"] = failed["reason"]
            # Lets the client show its localized message for a known class
            # (e.g. NO_AUDIO_TRACK) while `error` keeps the English reason.
            job["docs_topic"] = failed["docs_topic"] or None
            job["finished_at"] = time.time()
            logger.error("Batch job %s failed: %s", job_id, e, exc_info=True)
        finally:
            _processing_job_ids.discard(job_id)
            if job["status"] == "cancelled":
                job["retry_ready"] = True
            _queue.task_done()


def _set_progress(job, stage, percent=0, **extra):
    """Update a job's progress dict."""
    job["progress"] = {"stage": stage, "percent": percent, **extra}


#: Override for the native dub batch width. Set to 1 to disable batching.

#: Hard ceiling on the override — a batch this wide is already amortizing
#: almost all of the per-call setup, and beyond it the failure mode is an OOM
#: that costs more than the saving.

# Bound each allocation while persisting multipart uploads. Video inputs can
# be many gigabytes; `await UploadFile.read()` with no size used to mirror the
# entire file in process memory before writing it back out.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

_REMOTE_BATCH_OPERATION = "batch_segments"


async def _resolve_batch_execution(voice: dict):
    """Resolve Batch's TTS target without loading local weights remotely."""
    engine_id = active_backend_id()
    decision = gpu_gateway.decide("batch")
    if decision.remote:
        await gpu_gateway.preflight(
            engine_id,
            decision,
            operation=_REMOTE_BATCH_OPERATION,
        )
        return engine_id, decision, None
    backend = await resolve_generation_backend(
        require_cloning=voice["requires_cloning"],
        cloning_purpose="this batch job's pinned voice",
    )
    return engine_id, decision, backend


def _decode_remote_batch(
    result: gpu_gateway.RemoteResult,
    batch_dir: str,
    expected: set[int],
) -> tuple[dict[int, str], int]:
    """Validate and unpack one worker result before accepting remote success."""
    import soundfile as sf

    target = os.path.join(batch_dir, ".remote", result.task_id)
    paths = extract_segment_wavs(result.path or "", target)
    try:
        if set(paths) != expected:
            missing = sorted(expected - set(paths))
            extra = sorted(set(paths) - expected)
            raise ValueError(
                f"segment bundle mismatch (missing={missing}, extra={extra})"
            )
        rates = {int(sf.info(path).samplerate) for path in paths.values()}
        if len(rates) != 1 or next(iter(rates), 0) <= 0:
            raise ValueError("segment bundle has inconsistent sample rates")
        return paths, rates.pop()
    except BaseException:
        remove_segment_wavs(paths)
        raise


async def _save_upload(upload: UploadFile, destination: str) -> None:
    try:
        with open(destination, "wb") as output:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                output.write(chunk)
    except BaseException:
        try:
            unlink_if_present(destination)
        except FileCleanupError:
            logger.warning("Could not remove incomplete batch upload", exc_info=True)
        raise


def _batch_voice(voice_id: str | None) -> dict:
    """Resolve one queue-wide voice into concrete generation inputs.

    Clone profiles contribute their reference; designed profiles contribute
    their healed instruction and seed. Legacy ``preset:`` selections become
    the same instruction used by Dubbing instead of falling through to the
    engine default.
    """
    resolved = {
        "ref_audio": None,
        "ref_text": None,
        "instruct": "",
        "seed": None,
        "requires_cloning": False,
    }
    if not voice_id:
        return resolved
    if voice_id.startswith("preset:"):
        preset_id = voice_id.removeprefix("preset:")
        instruct = _BATCH_PRESET_INSTRUCT.get(preset_id)
        if instruct is None:
            raise ValueError("That built-in voice preset no longer exists")
        from omnivoice.utils.voice_design import sanitize_instruct

        resolved["instruct"] = sanitize_instruct(instruct)
        return resolved

    from core.config import VOICES_DIR
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?",
            (voice_id,),
        ).fetchone()
    if row is None:
        raise ValueError("That saved voice no longer exists")

    if row["kind"] == "design":
        from omnivoice.utils.voice_design import heal_design_instruct

        resolved["instruct"] = heal_design_instruct(row["instruct"], row["vd_states"])
        resolved["seed"] = int(row["seed"]) if row["seed"] is not None else None
        return resolved

    relative = row["locked_audio_path"] if row["is_locked"] else row["ref_audio_path"]
    if not relative:
        raise ValueError("That saved voice has no reference audio")
    ref_audio = os.path.join(VOICES_DIR, relative)
    if not os.path.isfile(ref_audio):
        raise ValueError("That saved voice's reference audio is missing")
    resolved.update({
        "ref_audio": ref_audio,
        "ref_text": row["ref_text"],
        "requires_cloning": True,
    })
    return resolved


async def _run_batch_pipeline(job_id: str, job: dict):
    """Full batch dub pipeline: extract → transcribe → translate → generate → mix → export."""
    import subprocess

    loop = asyncio.get_running_loop()
    video_path = job["video_path"]
    langs = job["langs"]
    batch_dir = os.path.join(DATA_DIR, "batch", job_id)
    os.makedirs(batch_dir, exist_ok=True)

    # ── 1. Extract audio ──────────────────────────────────────────────
    _set_progress(job, "extract", 0)
    audio_path = os.path.join(batch_dir, "audio.wav")

    from services.ffmpeg_utils import (
        bed_mix_filter,
        find_ffmpeg,
        raise_for_audio_extract_failure,
        require_audio_stream,
    )
    ffmpeg = find_ffmpeg()

    def _extract():
        # A video with no audio stream has nothing to dub: say so rather than
        # fail with ffmpeg's bare "returned non-zero exit status 234".
        require_audio_stream(video_path)
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", video_path,
                 "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                 audio_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=300, check=True,
            )
        except subprocess.CalledProcessError as e:
            raise_for_audio_extract_failure(e.stderr or b"", video_path)
            raise
        # Get duration
        result = subprocess.run(
            [ffmpeg, "-i", audio_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30,
        )
        import re
        match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", result.stderr.decode("utf-8", errors="replace"))
        if match:
            h, m, s, cs = match.groups()
            return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
        return 0.0

    duration = await loop.run_in_executor(None, _extract)
    job["duration"] = duration
    _set_progress(job, "extract", 100)

    if job["status"] == "cancelled":
        return

    # ── 2. Transcribe ─────────────────────────────────────────────────
    _set_progress(job, "transcribe", 0)

    from services.asr_backend import load_active_asr_backend
    from services.model_manager import _gpu_pool, _cpu_pool, run_on_gpu_pool_guarded
    from services.segmentation import (
        segment_transcript, assign_speakers_heuristic,
    )

    def _transcribe():
        # `load_*`, not `get_*`: the plain selector returns engines whose
        # shallow probe passed but whose deep import chain is broken, failing
        # the whole batch job at `.transcribe()` instead of degrading (#1185).
        backend = load_active_asr_backend()
        result = backend.transcribe(audio_path, word_timestamps=True)
        detected_lang = result.get("language", "en")
        segments = segment_transcript(result, duration=duration)
        segments = assign_speakers_heuristic(segments)
        for i, s in enumerate(segments):
            s["id"] = f"s{i:05x}"
            s.setdefault("text_original", s.get("text", ""))
        try:
            backend.unload()
        except Exception:
            pass
        return segments, detected_lang

    # Bound the batch transcribe (#730) so a wedged whisperx/CTranslate2 call
    # can't hold its GPU-pool worker forever and starve the rest of the backend
    # ("can't reach backend"); run_transcribe_guarded also resets the pool on
    # timeout to restore capacity.
    from services.asr_backend import run_transcribe_guarded
    segments, source_lang = await run_transcribe_guarded(_gpu_pool, _transcribe, what="Batch")
    source_lang = (source_lang or "en").split("_")[0][:2].lower()
    job["segments"] = segments
    job["source_lang"] = source_lang
    _set_progress(job, "transcribe", 100, segments_count=len(segments))

    if job["status"] == "cancelled" or not segments:
        if not segments:
            job["error"] = "Transcription produced no segments"
            job["status"] = "failed"
        return

    # ── Engine resolution (issue #312 class) ────────────────────────────
    # Batch used to hardcode VoiceStudio regardless of the engine selected in
    # Model Catalogue. Clone profiles require a cloning-capable engine; presets
    # and designed voices use instruction mode. Resolve once for the whole job.
    # below shares the same active engine); an uncaught ValueError here
    # propagates to _worker()'s existing except-Exception handling, which
    # already records a structured job failure via core.failure.build_failure.
    voice = _batch_voice(job.get("voice_id"))
    engine_id, execution_target, backend = await _resolve_batch_execution(voice)
    sr = backend.sample_rate if backend is not None else 0
    from services.performance_profiles import tts_defaults
    _profile_defaults = tts_defaults(engine_id)
    _batch_num_step = _profile_defaults.get("num_step", 16)
    _batch_postprocess = _profile_defaults.get("postprocess_output", True)
    batch_run = gpu_gateway.JobRun("batch")

    async def _prepare_local_batch() -> gpu_gateway.LocalCall:
        nonlocal backend, sr
        if backend is None:
            backend = await resolve_generation_backend(
                require_cloning=voice["requires_cloning"],
                cloning_purpose="this batch job's pinned voice",
            )
            sr = backend.sample_rate
        return gpu_gateway.LocalCall(fn=lambda: None, what="Batch TTS fallback")

    # ── 3. Translate + Generate per language ───────────────────────────
    total_langs = len(langs)
    outputs = {}

    for lang_idx, target_lang in enumerate(langs):
        if job["status"] == "cancelled":
            return

        # ── 3a. Translate ─────────────────────────────────────────────
        _set_progress(
            job, "translate",
            percent=int((lang_idx / total_langs) * 100),
            current_lang=target_lang,
        )

        translated_segments = list(segments)  # copy
        if target_lang != source_lang:
            # Use the same provider dispatch as interactive Dubbing. The old
            # batch-only implementation hardcoded Google and silently kept the
            # source text on failure, which could make an English track labelled
            # "es" while also sending text online despite an offline selection.
            from api.routers.dub_translate import dub_translate
            from schemas.requests import TranslateRequest

            from core import prefs

            provider = job.get("translation_provider") or prefs.get("translation_backend", "argos")
            translation = await dub_translate(TranslateRequest(
                segments=[
                    {
                        "id": str(segment["id"]),
                        "text": segment.get("text", ""),
                        "start": segment.get("start"),
                        "end": segment.get("end"),
                    }
                    for segment in segments
                ],
                source_lang=source_lang,
                target_lang=target_lang,
                provider=provider,
                quality="fast",
            ))
            if isinstance(translation, JSONResponse):
                try:
                    payload = json.loads(translation.body)
                    detail = payload.get("error") or payload.get("detail")
                    if payload.get("code") == "argos_pack_missing":
                        job["setup_required"] = {
                            "kind": "argos_packs",
                            "source_lang": source_lang,
                            "target_langs": [
                                pair["target_lang"]
                                for pair in payload.get("pairs", [])
                                if isinstance(pair, dict) and pair.get("target_lang")
                            ],
                        }
                except Exception:  # noqa: BLE001 — retain the stable fallback
                    detail = None
                raise RuntimeError(
                    detail or f"{provider} could not translate this batch"
                )
            rows = {
                str(row.get("id")): row
                for row in translation.get("translated", [])
                if isinstance(row, dict)
            }
            failed = [row for row in rows.values() if row.get("error")]
            if failed or len(rows) != len(segments):
                raise RuntimeError(
                    f"{provider} translation failed for "
                    f"{len(failed) or len(segments) - len(rows)} segment(s)"
                )
            translated_segments = [
                {**segment, "text": rows[str(segment["id"])]["text"]}
                for segment in segments
            ]

        if job["status"] == "cancelled":
            return

        # ── 3b. Generate TTS ──────────────────────────────────────────
        _set_progress(
            job, "generate",
            percent=int((lang_idx / total_langs) * 100),
            current_lang=target_lang,
            current_segment=0,
            total_segments=len(translated_segments),
        )

        from services.audio_dsp import apply_mastering, normalize_audio
        from services.audio_io import atomic_save_wav
        import torch

        remote_segments: dict[int, str] = {}
        valid_rows = [
            (i, segment)
            for i, segment in enumerate(translated_segments)
            if segment.get("end", 0) - segment.get("start", 0) > 0.05
            and segment.get("text", "").strip()
        ]
        if execution_target.remote and valid_rows:
            remote_rows = [
                {
                    "index": i,
                    "text": segment.get("text", "").strip(),
                    "language": target_lang,
                    "ref_text": voice["ref_text"],
                    "instruct": voice["instruct"] or None,
                    "duration": segment.get("end", 0) - segment.get("start", 0),
                    "num_step": _batch_num_step,
                    "postprocess_output": _batch_postprocess,
                    "guidance_scale": 2.0,
                    "speed": 1.0,
                    "effect_preset": "batch",
                    "seed": (
                        voice["seed"] + i if voice["seed"] is not None else None
                    ),
                    # The assembled track receives one watermark below. Marking
                    # each line here would double-process remote output.
                    "watermark": False,
                }
                for i, segment in valid_rows
            ]
            expected = {row["index"] for row in remote_rows}

            def _remote_state(state: dict) -> None:
                fraction = max(0.0, min(1.0, float(state.get("progress") or 0.0)))
                _set_progress(
                    job,
                    "generate",
                    percent=int(((lang_idx + fraction) / total_langs) * 100),
                    current_lang=target_lang,
                    current_segment=min(len(remote_rows), round(fraction * len(remote_rows))),
                    total_segments=len(remote_rows),
                    execution_target=execution_target.label,
                    execution_phase=state.get("phase"),
                )

            route_task = asyncio.create_task(
                gpu_gateway.run(
                    "batch",
                    local=gpu_gateway.LocalCall(prepare=_prepare_local_batch),
                    remote=gpu_gateway.RemoteCall(
                        engine=engine_id,
                        operation=_REMOTE_BATCH_OPERATION,
                        params={
                            "segments": remote_rows,
                            "ref_audio": [voice["ref_audio"] for _ in remote_rows],
                            "input_seconds": sum(
                                float(row.get("duration") or 0.0) for row in remote_rows
                            ),
                        },
                        idempotency_key=f"batch:{job_id}:{target_lang}",
                        decode=lambda result: _decode_remote_batch(
                            result, batch_dir, expected
                        ),
                    ),
                    decision=execution_target,
                    job=batch_run,
                    on_state=_remote_state,
                )
            )
            while not route_task.done():
                await asyncio.wait({route_task}, timeout=0.25)
                if job["status"] == "cancelled":
                    route_task.cancel()
                    try:
                        await route_task
                    except asyncio.CancelledError:
                        pass
                    return
            routed = route_task.result()
            if routed is not None:
                remote_segments, sr = routed

        # A remote-only empty transcript still needs a valid silent-track rate.
        sr = sr or 24_000
        total_samples = int(duration * sr)
        full_audio = torch.zeros(1, total_samples)
        total_segs = len(translated_segments)

        # Native engines can amortize encoder/decoder setup across a small
        # batch. Keep the adapter seam optional: engines without a real batch
        # implementation inherit TTSBackend.generate_batch(), which preserves
        # the established one-segment behavior below.
        from services.tts_backend import TTSBackend
        batched_audio: dict[int, torch.Tensor] = {}
        has_native_batch = (
            backend is not None
            and type(backend).generate_batch is not TTSBackend.generate_batch
        )
        if has_native_batch:
            from services.text_normalization import normalize_for_tts

            batch_ref_audio = voice["ref_audio"]
            batch_ref_text = voice["ref_text"]

            batch_width = _native_batch_width(backend)

            async def _prefetch_batch(first_index: int) -> None:
                """Render the batch beginning at ``first_index`` into
                ``batched_audio``.

                Rendered on demand rather than prerendering the whole track:
                the tensors are popped as they are placed, so peak host memory
                is one batch instead of every segment of the language — and
                the progress bar tracks placement instead of running to the
                end and restarting at segment 1.
                """
                if job["status"] == "cancelled":
                    return
                batch_rows = []
                index = first_index
                while index < total_segs and len(batch_rows) < batch_width:
                    seg = translated_segments[index]
                    if (seg.get("end", 0) - seg.get("start", 0) > 0.05
                            and seg.get("text", "").strip()):
                        batch_rows.append((index, seg))
                    index += 1
                if len(batch_rows) < 2:
                    return  # nothing to amortize — the per-segment path is equal
                batch_indices = [index for index, _ in batch_rows]
                batch_texts = [
                    normalize_for_tts(row.get("text", "").strip(), target_lang)
                    for _, row in batch_rows
                ]
                batch_durations = [
                    row.get("end", 0) - row.get("start", 0)
                    for _, row in batch_rows
                ]

                def _render_native_batch():
                    if voice["seed"] is not None:
                        torch.manual_seed(voice["seed"])
                    generated = backend.generate_batch(
                        batch_texts,
                        language=target_lang,
                        ref_audio=batch_ref_audio,
                        ref_text=batch_ref_text,
                        instruct=voice["instruct"] or None,
                        duration=batch_durations,
                        num_step=_batch_num_step,
                        guidance_scale=2.0,
                        speed=1.0,
                        denoise=True,
                        postprocess_output=_batch_postprocess,
                    )
                    if len(generated) != len(batch_indices):
                        raise RuntimeError(
                            f"native batch returned {len(generated)} outputs for "
                            f"{len(batch_indices)} segments"
                        )
                    rendered = []
                    for audio_out in generated:
                        if not getattr(backend, "applies_own_mastering", False):
                            audio_out = apply_mastering(audio_out, sample_rate=sr)
                        rendered.append(normalize_audio(audio_out, target_dBFS=-2.0))
                    return rendered

                try:
                    rendered = await run_on_gpu_pool_guarded(
                        _render_native_batch,
                        what="Batch generate",
                        timeout=_batch_timeout_s(batch_texts, backend),
                    )
                    batched_audio.update(zip(batch_indices, rendered))
                except TimeoutError:
                    # Do not immediately queue the same expensive work again:
                    # the timed-out pool task may still be holding the device.
                    raise
                except Exception as e:
                    logger.warning(
                        "Native TTS batch failed for segments %s-%s; falling back per segment: %s",
                        batch_indices[0] + 1,
                        batch_indices[-1] + 1,
                        e,
                    )

        for i, seg in enumerate(translated_segments):
            if job["status"] == "cancelled":
                remove_segment_wavs(remote_segments)
                return

            _set_progress(
                job, "generate",
                percent=int(((lang_idx + (i / total_segs)) / total_langs) * 100),
                current_lang=target_lang,
                current_segment=i + 1,
                total_segments=total_segs,
            )

            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_duration = seg_end - seg_start
            seg_text = seg.get("text", "").strip()

            if seg_duration <= 0.05 or not seg_text:
                continue

            def _gen(text=seg_text, lang=target_lang, dur=seg_duration):
                # Normalize once at the segment's text→engine choke point —
                # the same pre-pass as /generate and dub_generate's _gen.
                # `lang` is the job's target language code. Pref-gated,
                # idempotent, never raises.
                from services.text_normalization import normalize_for_tts
                text = normalize_for_tts(text, lang)

                try:
                    if backend is None:
                        raise RuntimeError("the local TTS fallback was not prepared")
                    if voice["seed"] is not None:
                        torch.manual_seed(voice["seed"] + i)
                    audio_out = backend.generate(
                        text=text, language=lang,
                        ref_audio=voice["ref_audio"], ref_text=voice["ref_text"],
                        instruct=voice["instruct"] or None,
                        duration=dur, num_step=_batch_num_step,
                        guidance_scale=2.0, speed=1.0,
                        denoise=True, postprocess_output=_batch_postprocess,
                    )
                    if not getattr(backend, "applies_own_mastering", False):
                        audio_out = apply_mastering(audio_out, sample_rate=sr)
                    return normalize_audio(audio_out, target_dBFS=-2.0)
                except Exception as e:
                    logger.warning("TTS failed for seg %d (lang=%s): %s", i, lang, e)
                    # #1190: the silence still stands in for the segment (one
                    # bad line shouldn't bin an otherwise good dub), but it is
                    # no longer INVISIBLE — the job carries a warning the UI /
                    # API consumer can see instead of shipping a
                    # finished-looking track with unexplained silence.
                    job.setdefault("warnings", []).append(
                        f"Segment {i + 1} of the {lang} track failed to "
                        f"synthesize and was left silent: {e}"
                    )
                    return torch.zeros(1, int(dur * sr))

            try:
                # Bounded + pool-reset on hang so a wedged batch segment can't
                # starve the GPU pool and brick the backend (#730 class).
                # Budget is the shared length-scaled one (#1190): a long segment
                # on CPU-class hardware no longer dies on the flat 300s.
                from services.model_manager import generate_timeout_s
                remote_path = remote_segments.pop(i, None)
                if remote_path is not None:
                    import soundfile as sf

                    try:
                        audio_array, remote_sr = sf.read(
                            remote_path,
                            dtype="float32",
                            always_2d=True,
                        )
                        if int(remote_sr) != sr:
                            raise ValueError(
                                f"remote segment sample rate changed from {sr} to {remote_sr}"
                            )
                        audio_tensor = torch.from_numpy(audio_array.T).mean(
                            dim=0,
                            keepdim=True,
                        )
                    finally:
                        remove_segment_wavs({i: remote_path})
                else:
                    if backend is None:
                        await _prepare_local_batch()
                        # This path means a validated remote bundle lost a row
                        # after dispatch. Recover only that row; native batches
                        # were not planned for this language.
                        has_native_batch = False
                    if has_native_batch and i not in batched_audio:
                        await _prefetch_batch(i)
                    if i in batched_audio:
                        audio_tensor = batched_audio.pop(i)
                    else:
                        audio_tensor = await run_on_gpu_pool_guarded(
                            _gen,
                            what="Batch generate",
                            timeout=generate_timeout_s(seg_text, engine=backend),
                        )

                # Fit to slot
                target_samples_seg = int(seg_duration * sr)
                current_samples = audio_tensor.shape[-1]
                if target_samples_seg > current_samples:
                    audio_tensor = torch.nn.functional.pad(
                        audio_tensor, (0, target_samples_seg - current_samples)
                    )
                elif current_samples > target_samples_seg:
                    audio_tensor = audio_tensor[..., :target_samples_seg]

                # Crossfade
                fade_samples = int(0.015 * sr)
                wl = audio_tensor.shape[-1]
                if wl > fade_samples * 2:
                    ramp_up = torch.linspace(0, 1, fade_samples)
                    ramp_down = torch.linspace(1, 0, fade_samples)
                    audio_tensor[0, :fade_samples] *= ramp_up
                    audio_tensor[0, -fade_samples:] *= ramp_down

                s_idx = int(seg_start * sr)
                e_idx = min(s_idx + wl, total_samples)
                full_audio[:, s_idx:e_idx] += audio_tensor[:, :e_idx - s_idx]

            except TimeoutError as e:
                # #1190/#1202: a GPU timeout (or a saturated pool) used to be
                # swallowed into a silent gap in the dubbed track — the user got
                # a finished-looking video with missing speech and no warning,
                # and on a 1-worker host the abandoned job made every later
                # segment likelier to time out too (the "22-chunk batch dies at
                # chunk 3" cascade). Fail the job loudly instead: _worker()'s
                # except-Exception handler records a structured failure the UI
                # surfaces. Non-timeout per-segment errors keep the old
                # degrade-to-gap behaviour, but are now recorded on the job.
                logger.error("Batch TTS seg %d timed out — failing the job: %s", i, e)
                raise RuntimeError(
                    f"Segment {i + 1} of the {target_lang} track did not "
                    f"render, so the dubbed track would have shipped with a "
                    f"silent gap. {e}"
                ) from e
            except Exception as e:
                logger.warning("Batch TTS seg %d failed: %s", i, e)
                job.setdefault("warnings", []).append(
                    f"Segment {i + 1} of the {target_lang} track failed and was "
                    f"left silent: {e}"
                )

        remove_segment_wavs(remote_segments)

        # ── 3c. Save dubbed audio track ───────────────────────────────
        # Invisible provenance mark on the assembled track (#1169), tensor
        # stage, before the WAV write / aac mux — batch dubs used to ship
        # unmarked while the interactive dub pipeline marked every segment.
        # One whole-track embed (chunked internally, #1045) is equivalent to
        # dub_generate's per-segment marks: the 16-bit message repeats
        # throughout. Never raises (degrades to unmarked on failure, same as
        # every producer).
        # Dispatched to the dedicated watermark pool, not the GPU pool (#1190):
        # AudioSeal embedding is CPU work that holds no VRAM, and a whole-track
        # embed is long enough that occupying a GPU worker with it stalled the
        # next language's segments on 1-worker hosts.
        from services.watermark import mark_synthetic_async
        full_audio = await mark_synthetic_async(
            full_audio, sr, context="batch.dub_track",
        )

        # Same assembly pattern as dub_generate.py:390 — `full_audio` is a
        # zero-init tensor that gets +='d from torch.cat-style slices, so
        # it can land non-contiguous + out-of-range. Go through the
        # audited + atomic helper to defend against #48 silent corruption
        # and partial-write truncation simultaneously.
        track_path = os.path.join(batch_dir, f"dubbed_{target_lang}.wav")
        atomic_save_wav(track_path, full_audio, sr)

        # ── 3d. Mix with original video ───────────────────────────────
        _set_progress(
            job, "mix",
            percent=int(((lang_idx + 0.8) / total_langs) * 100),
            current_lang=target_lang,
        )

        output_path = os.path.join(batch_dir, f"output_{target_lang}.mp4")

        def _mix(bg=job.get("preserve_bg", True)):
            if bg:
                # Mix dubbed audio with original background
                subprocess.run(
                    [ffmpeg, "-y",
                     "-i", video_path,
                     "-i", track_path,
                     "-filter_complex",
                     bed_mix_filter("0:a", "1:a", out="out", duration="first"),
                     "-map", "0:v", "-map", "[out]",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-shortest", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=True,
                )
            else:
                # Replace audio entirely
                subprocess.run(
                    [ffmpeg, "-y",
                     "-i", video_path,
                     "-i", track_path,
                     "-map", "0:v", "-map", "1:a",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-shortest", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=True,
                )

        await loop.run_in_executor(None, _mix)
        outputs[target_lang] = output_path

    job["outputs"] = outputs
    job.pop("setup_required", None)
    _set_progress(job, "done", 100)


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/batch/enqueue")
async def enqueue_batch_job(
    video: UploadFile = File(...),
    langs: str = Form("es"),            # comma-separated lang codes
    voice_id: Optional[str] = Form(None),
    preserve_bg: bool = Form(True),
    translation_provider: Optional[str] = Form(None),
):
    """Enqueue a video for batch dubbing.

    The video is saved to disk and a job is added to the queue.
    Returns the job ID for status polling.
    """
    _ensure_queue()

    job_id = str(uuid.uuid4())[:12]
    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "At least one target language is required")

    # Validate the snapshot before persisting a potentially large upload.
    # Resolve it again in the worker so deleting or editing a queued profile
    # cannot silently fall back to the engine's default voice.
    try:
        await asyncio.to_thread(_batch_voice, voice_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # TTS-only install: no ASR model on disk → typed 409 with a download CTA
    # now, instead of accepting the job and having the transcribe stage
    # silently auto-download multi-GB whisper weights (or fail) in the worker.
    from services.asr_backend import asr_model_missing_detail, asr_model_missing_error
    missing = await asyncio.to_thread(asr_model_missing_error)
    if missing is not None:
        raise HTTPException(409, {**missing, "message": asr_model_missing_detail(missing)})

    # Snapshot the selected translation engine when the user enqueues the job,
    # so a later Settings change cannot alter work already waiting in the queue.
    from core import prefs
    from services import translation_engines

    provider = translation_provider or prefs.get("translation_backend", "argos")
    if not translation_engines.get_engine(provider):
        raise HTTPException(400, "Unknown translation engine")
    if not translation_engines.is_installed(provider):
        raise HTTPException(409, "Install the selected translation engine before adding this batch")
    if not translation_engines.is_ready(provider):
        raise HTTPException(409, "Configure the selected translation provider before adding this batch")

    # Save the uploaded video
    batch_dir = os.path.join(DATA_DIR, "batch")
    os.makedirs(batch_dir, exist_ok=True)
    ext = os.path.splitext(video.filename or "video.mp4")[1] or ".mp4"
    video_path = os.path.join(batch_dir, f"{job_id}{ext}")

    await _save_upload(video, video_path)

    job = {
        "id": job_id,
        "status": "queued",
        "filename": video.filename or f"{job_id}{ext}",
        "video_path": video_path,
        "langs": lang_list,
        "voice_id": voice_id,
        "preserve_bg": preserve_bg,
        "translation_provider": provider,
        "created_at": time.time(),
        "attempts": 1,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
    }
    _jobs[job_id] = job
    await _queue.put(job_id)

    logger.info(
        "Batch job %s enqueued (%d target languages)",
        log_safe(job_id), len(lang_list),
    )
    return {"job_id": job_id, "status": "queued", "queue_position": _queue.qsize()}


@router.get("/batch/jobs")
def list_batch_jobs(status: Optional[str] = None, limit: int = 50):
    """List batch jobs, optionally filtered by status."""
    jobs = list(_jobs.values())
    if status:
        if status == "active":
            jobs = [j for j in jobs if j["status"] in ("queued", "running")]
        elif status == "retryable":
            jobs = [j for j in jobs if j["status"] in ("failed", "cancelled")]
        else:
            jobs = [j for j in jobs if j["status"] == status]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs[:limit]


@router.get("/batch/jobs/{job_id}")
def get_batch_job(job_id: str):
    """Get the status of a specific batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/batch/jobs/{job_id}/cancel")
def cancel_batch_job(job_id: str):
    """Cancel a queued or running batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("done", "failed", "cancelled"):
        return {"already": job["status"]}
    was_running = job["status"] == "running" or job_id in _processing_job_ids
    job["status"] = "cancelled"
    job["retry_ready"] = not was_running
    job["finished_at"] = time.time()
    return {"cancelled": True}


@router.post("/batch/jobs/{job_id}/retry")
async def retry_batch_job(job_id: str):
    """Retry a terminal job using its original app-owned upload and settings."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"Job is {job['status']}, not retryable")
    if job_id in _processing_job_ids or not job.get("retry_ready", True):
        raise HTTPException(409, "The cancelled job is still stopping")
    if not os.path.isfile(job.get("video_path") or ""):
        raise HTTPException(409, "The original batch input is no longer available")

    try:
        await asyncio.to_thread(_batch_voice, job.get("voice_id"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from services.asr_backend import asr_model_missing_detail, asr_model_missing_error
    missing = await asyncio.to_thread(asr_model_missing_error)
    if missing is not None:
        raise HTTPException(409, {**missing, "message": asr_model_missing_detail(missing)})

    from services import translation_engines
    provider = job.get("translation_provider") or "argos"
    if not translation_engines.is_ready(provider):
        raise HTTPException(409, "Configure the selected translation provider before retrying")
    if provider == "argos" and job.get("source_lang"):
        try:
            status = await asyncio.to_thread(
                translation_engines.argos_pack_status,
                job["source_lang"],
                job["langs"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if any(not pair["installed"] for pair in status["pairs"]):
            raise HTTPException(409, "Install the required Argos language packs before retrying")

    batch_root = os.path.realpath(os.path.join(DATA_DIR, "batch"))
    output_dir = os.path.realpath(os.path.join(batch_root, job_id))
    if os.path.dirname(output_dir) != batch_root:
        raise HTTPException(status_code=400, detail="Invalid batch job path")
    try:
        if os.path.isdir(output_dir):
            await asyncio.to_thread(shutil.rmtree, output_dir)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not reset the batch output files. Close any app using them and retry.",
        ) from exc

    for key in (
        "duration",
        "segments",
        "source_lang",
        "outputs",
        "warnings",
        "setup_required",
        "retry_ready",
        "docs_topic",
    ):
        job.pop(key, None)
    job.update({
        "status": "queued",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
        "attempts": int(job.get("attempts", 1)) + 1,
    })
    _ensure_queue()
    await _queue.put(job_id)
    return {"job_id": job_id, "status": "queued", "queue_position": _queue.qsize()}


@router.delete("/batch/jobs/{job_id}")
def delete_batch_job(job_id: str):
    """Delete a batch job record and every app-owned input/output file."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("video_path"):
        try:
            unlink_if_present(job["video_path"])
        except FileCleanupError as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not delete the batch video file. Close any app using it and retry.",
            ) from exc
    batch_root = os.path.realpath(os.path.join(DATA_DIR, "batch"))
    output_dir = os.path.realpath(os.path.join(batch_root, job_id))
    if os.path.dirname(output_dir) != batch_root:
        raise HTTPException(status_code=400, detail="Invalid batch job path")
    try:
        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not delete the batch output files. Close any app using them and retry.",
        ) from exc
    _jobs.pop(job_id, None)
    return {"deleted": True}


@router.get("/batch/download/{job_id}/{lang}")
def download_batch_output(job_id: str, lang: str):
    """Download a completed batch job's output video for a given language."""
    from fastapi.responses import FileResponse

    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done":
        raise HTTPException(400, f"Job is {job['status']}, not done")

    outputs = job.get("outputs", {})
    path = outputs.get(lang)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"No output for language '{lang}'")

    filename = f"{os.path.splitext(job['filename'])[0]}_{lang}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
    )
