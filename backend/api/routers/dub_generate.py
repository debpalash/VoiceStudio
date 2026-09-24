import os
import re
import json
import struct
import logging
import time
import asyncio
import torch
import torchaudio
from fastapi import APIRouter, HTTPException

from core.db import db_conn
from core.config import DUB_DIR, VOICES_DIR, dub_seg_path
from core.tasks import task_manager
from schemas.requests import DubRequest
from services.model_manager import _gpu_pool, run_on_gpu_pool_guarded
from services.srt_parser import vouch_cue_source
from services.tts_backend import TTSBackend, resolve_generation_backend, active_backend_id
from services.dub_batching import batch_timeout_s, native_batch_width
from services import gpu_gateway
from services.audio_dsp import apply_mastering, normalize_audio, apply_effects_chain, get_effect_chain
from services.audio_io import atomic_save_wav, _safe_torchaudio_save
from services.ffmpeg_utils import (
    find_ffmpeg,
    spawn_subprocess,
    # Moved to ffmpeg_utils so the Smart Fit export pipeline (Phase B) can
    # reuse them; re-imported here so `dub_generate._atempo_chain` /
    # `_pitch_preserving_stretch` keep working for existing importers.
    _atempo_chain,
    _pitch_preserving_stretch,
)
from services.rvc import apply_rvc, is_enabled as rvc_is_enabled
from services.incremental import segment_fingerprint, fit_fingerprint
from services.fit_planner import FitParams, plan_fit
from services.watermark import mark_synthetic
from services.speaker_clone import auto_profile_id
from services.segment_bundle import extract_segment_wavs
from api.routers.dub_core import _get_job, _save_job
from omnivoice.utils.voice_design import heal_design_instruct

logger = logging.getLogger("omnivoice.dub")

class _RemoteDubBackend:
    """Sample-rate carrier while Dubbing runs without local TTS weights."""

    sample_rate = 24_000


async def _resolve_dub_execution():
    """Resolve routing without loading local weights for a remote dub."""
    engine_id = active_backend_id()
    decision = gpu_gateway.decide("dub_segments")
    if decision.remote:
        await gpu_gateway.preflight(engine_id, decision, operation="dub_segments")
        return engine_id, decision, _RemoteDubBackend()
    return engine_id, decision, await resolve_generation_backend(require_cloning=True)


def _prepare_oom_retry(error: Exception, *, execution_target: str) -> bool:
    """Prepare one *local* low-step retry after a genuine device OOM.

    The cache being flushed must belong to the device that raised the error.
    A remote worker owns its own recovery policy; flushing this process's CUDA
    cache after a remote failure both stalls the wrong GPU and can evict an
    unrelated local job.  Keep this guard at the retry chokepoint so a future
    ``dub_segments`` producer cannot accidentally inherit the old behaviour.

    Returns ``False`` for non-OOM errors.  Remote OOMs are deliberately raised
    unchanged: the worker may classify/retry them, but this process must not.
    """
    is_oom = (
        isinstance(error, torch.cuda.OutOfMemoryError)
        or "out of memory" in str(error).lower()
        or "CUDA error" in str(error)
    )
    if not is_oom:
        return False
    if execution_target != "local":
        raise error

    import gc
    from services.model_manager import release_device_cache

    gc.collect()
    release_device_cache()
    return True


def _cached_payload_intact(path: str, info) -> bool:
    """Cheap truth check on a cached WAV whose header we are about to trust.

    The natural-rate fast path hands the mixer a PATH instead of decoded
    audio, so a cache whose header reads fine but whose payload is truncated
    would only fail later, during assembly — after the timing plan (Smart Fit,
    video stretch) had been computed from the header's frame count. The plan
    would then describe audio that no longer exists and the segment would be
    replaced by slot-length silence, leaving the persisted video plan and the
    rendered track disagreeing.

    Comparing the declared frame count against the physical ``data`` chunk
    catches that without decoding: a truncated file cannot hold the samples
    its header claims. Anything failing here falls through to the decoding path, which
    already degrades to a warning plus silence. Formats with no fixed
    bits-per-sample (compressed caches) are left to the decoder as before.
    """
    try:
        bits = int(getattr(info, "bits_per_sample", 0) or 0)
        frames = int(getattr(info, "num_frames", 0) or 0)
        channels = int(getattr(info, "num_channels", 0) or 0)
        if bits <= 0 or frames <= 0 or channels <= 0:
            # Undecidable metadata fails CLOSED (review on #1620): these caches
            # are PCM WAVs this module wrote itself, so anything else is
            # unexpected — and the decode path this falls through to handles
            # every format the fast path would have.
            return False
        payload = frames * channels * (bits // 8)
        if payload <= 0:
            return False

        # A WAV may carry JUNK/LIST metadata before data, so its header is not
        # necessarily 44 bytes. Locate the data chunk instead of counting
        # metadata as audio; otherwise an extended header can mask truncation.
        file_size = os.path.getsize(path)
        with open(path, "rb") as wav:
            header = wav.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return False
            offset = 12
            while offset + 8 <= file_size:
                wav.seek(offset)
                chunk_id = wav.read(4)
                chunk_size_raw = wav.read(4)
                if len(chunk_id) != 4 or len(chunk_size_raw) != 4:
                    return False
                chunk_size = struct.unpack("<I", chunk_size_raw)[0]
                data_offset = offset + 8
                if chunk_id == b"data":
                    return chunk_size >= payload and file_size >= data_offset + payload
                offset = data_offset + chunk_size + (chunk_size % 2)
        return False
    except Exception:  # noqa: BLE001 — an unstattable cache is the decoder's problem
        return False


def _underrun_min_rate() -> float:
    """Floor for the underrun fill (audio slowed toward its slot, never below
    this rate). Default 0.85 stays natural-sounding; OMNIVOICE_UNDERRUN_MIN_RATE=1.0
    disables the fill. Clamped to atempo's per-stage sane range."""
    try:
        v = float(os.environ.get("OMNIVOICE_UNDERRUN_MIN_RATE", "0.85"))
    except ValueError:
        v = 0.85
    return min(1.0, max(0.5, v))
# How far a too-long segment is allowed to bleed into the silent gap
# before the next segment. Buys headroom on languages with higher
# information density (Bengali, Hindi, Arabic…) without the audio
# colliding with the next speaker's onset.
GAP_OVERFLOW_MAX_S = 0.25
GAP_OVERFLOW_BUFFER_S = 0.05


def _sync_job_segments(job: dict, req: DubRequest) -> None:
    """Persist the segments this dub was actually generated from back onto the job.

    The editor only sends the (translated / user-edited) segment text in the
    generate request; the job itself kept the original-language ASR transcript.
    SRT/VTT export and ffmpeg subtitle burn-in read `job["segments"]`, so they
    rendered the source language instead of the dub the user just heard (#309).

    Merge strategy: rebuild `job["segments"]` from the request, carrying over
    per-segment metadata (speaker_id, id, …) from the existing job segment
    matched by stable id (fallback: index). `text_original` always keeps the
    source-language text so dual-subtitle layouts can still stack it under the
    translation.
    """
    if not req.segments:
        return
    existing = [s for s in (job.get("segments") or []) if isinstance(s, dict)]
    by_id = {str(s["id"]): s for s in existing if s.get("id") is not None}
    seg_ids = req.segment_ids or []
    merged: list[dict] = []
    vouched: list[str | None] = []
    for i, seg in enumerate(req.segments):
        seg_id = seg_ids[i] if i < len(seg_ids) else None
        prev = by_id.get(str(seg_id)) if seg_id is not None else None
        if prev is None and i < len(existing):
            prev = existing[i]
        row = dict(prev) if prev else {}
        if seg_id is not None:
            # The request id is authoritative — seg_order and the per-segment
            # WAV manifest are keyed by it.
            row["id"] = seg_id
        # Source-language text survives the overwrite so dual-subtitle export
        # keeps working; never let the translation clobber it.
        row["text_original"] = row.get("text_original") or row.get("text") or ""
        row["start"] = seg.start
        row["end"] = seg.end
        row["text"] = seg.text
        # Imported cue markup is reused only when the client vouches this is
        # still that import's text, never because the text happens to match.
        vouched.append(vouch_cue_source(row, seg.text, seg.cue_source_id))
        merged.append(row)
    job["segments"] = merged

    # P1.2 — per-language text, additively. `job["segments"]` stays the flat
    # single-slot map every existing consumer reads (last generated language);
    # `job["segments_i18n"]` preserves EACH generated track's text so
    # /dub/srt|vtt?lang= can emit that language instead of N identical files.
    # Shape: { langCode: { segKey: text } } where segKey is the segment's
    # stable id (str) or, for id-less legacy segments, its list index (str).
    # The whole per-language map is rebuilt on every generate of that language
    # (the request always carries the full segment list), so deleted segments
    # never linger. Jobs predating this field simply lack it — every reader
    # falls back to `job["segments"]`.
    lang = (req.language_code or "und").strip() or "und"
    i18n = job.setdefault("segments_i18n", {})
    i18n[lang] = {
        (str(row["id"]) if row.get("id") is not None else str(i)): row["text"]
        for i, row in enumerate(merged)
    }
    # Which imported cue each track's text still is, so a per-language export
    # reuses markup only for the track that carried the import's own text.
    job.setdefault("segments_i18n_cue_sources", {})[lang] = {
        (str(row["id"]) if row.get("id") is not None else str(i)): cue_id
        for i, (row, cue_id) in enumerate(zip(merged, vouched))
        if cue_id
    }


def _seg_hashes_by_lang(job: dict) -> dict:
    """Per-language segment fingerprints: { langCode: { segId: hash } }.

    Additive migration (P1.3): jobs written by previous builds carry ONE flat
    `seg_hashes` map that was overwritten by whichever language generated
    last. That flat map can only describe the job's last-generated track, so
    it is attributed to `job["language_code"]` (which generate has always
    kept in lock-step with the last run). When even that is unknown the
    legacy hashes are dropped — segments then read as stale and regenerate
    cleanly, which is safer than guessing a language and splicing wrong-track
    audio. Note the legacy hashes also predate language-scoped fingerprints
    (see services.incremental.segment_fingerprint), so they compare stale
    once regardless — carrying them over just preserves the job shape.
    """
    by_lang = job.get("seg_hashes_by_lang")
    if not isinstance(by_lang, dict):
        by_lang = {}
        legacy = job.get("seg_hashes")
        prev_lang = job.get("language_code")
        if isinstance(legacy, dict) and legacy and prev_lang:
            by_lang[prev_lang] = dict(legacy)
        job["seg_hashes_by_lang"] = by_lang
    return by_lang


def _legacy_seg_cache_ok(job: dict, lang_code: str) -> bool:
    """May this run reuse legacy un-keyed ``seg_<id>.wav`` files?

    Only when no OTHER language's audio could be sitting in them: the job has
    no dubbed track in a different language. Single-language jobs rendered by
    previous builds therefore keep their whole on-disk cache; the moment a
    job carries a second language the un-keyed files are ambiguous (they hold
    whichever language wrote them last) and must never be spliced into a
    track again — the P1.3 cross-contamination class.
    """
    tracks = job.get("dubbed_tracks") or {}
    return not any(lc != lang_code for lc in tracks)


# ── voice_match="consistent" resolution ─────────────────────────────────────
# Owner report: "still 4 segments different in voice". Per-segment refs (Wave
# 3.2) clone each line from a clip of its own source audio — best prosody
# match, but the voice IDENTITY drifts line to line, and heuristic-diarized
# jobs have no pooled speaker clones to anchor it. `voice_match="consistent"`
# resolves every segment of a speaker to ONE reference: the per-speaker clone
# when it exists, otherwise a deterministic pick among that speaker's own
# per-segment clips.

# Below ~3 s zero-shot prompt-priming gets unstable, so prefer clips at or
# above it when choosing the one shared reference.
CONSISTENT_MIN_REF_S = 3.0


def _speaker_key_matches(speaker_id: str, key: str) -> bool:
    """Same matching rule the `auto:` branch has always used: the safe-name
    slug first (`auto_profile_id`), the raw speaker id as fallback."""
    return auto_profile_id(speaker_id) == f"auto:{key}" or speaker_id == key


def _find_speaker_clone(clones: dict, key: str):
    for spk, info in (clones or {}).items():
        if _speaker_key_matches(spk, key):
            return info
    return None


def _seg_id_order(sid: str):
    """Sort key for the tie-break: numeric suffix when there is one (so
    'seg_2' < 'seg_10'), plain string ordering otherwise. Deterministic for
    any id shape."""
    m = re.search(r"(\d+)$", sid)
    return (0, int(m.group(1)), sid) if m else (1, 0, sid)


def _speaker_key_for_segment(job: dict, sid) -> str | None:
    """The `auto:`-style key of the speaker that owns segment `sid`, from the
    job's diarized segment rows. None when the segment is unknown (the caller
    then keeps per-line behaviour for it — best effort, never a crash)."""
    for row in job.get("segments") or []:
        if isinstance(row, dict) and str(row.get("id", "")) == str(sid):
            spk = row.get("speaker_id") or "Speaker 1"
            return auto_profile_id(spk)[len("auto:"):]
    return None


#: ``job_id -> {(segment, path)}`` already warned about, so a 300-segment dub
#: whose clip directory was cleaned logs once per segment rather than once per
#: retry.
#:
#: Keyed on the PATH as well as the segment, not the segment alone. The
#: single-segment preview endpoint has no segment identity to pass — it is a
#: "render this text" call — so every preview shared one key and only the first
#: missing reference in a job was ever reported, silencing the rest (greptile).
#: A distinct path is distinct information wherever it comes from, and on the
#: render path this also means a segment whose binding was changed to a second
#: missing clip is not mistaken for the one already reported.
_MISSING_REF_WARNED: dict = {}


def warn_if_ref_missing(ref_audio, *, job_id: str = "", seg_id="", where: str = "dub"):
    """Say so when a clone reference points at a file that is gone (#1331).

    Reported as: re-dubbing a single sentence loses the cloned voice, while
    re-dubbing everything keeps it.

    Clone references are FILE PATHS into the job's extracted-clip directory,
    and the whole job dict — those paths included — is persisted to
    ``dub_history.job_data`` so saved projects reopen after a restart. The job
    therefore outlives its clips. Reopen a saved dub once the clip directory
    has been cleaned, regenerate one line, and every resolution branch hands
    the engine a path that is no longer there. An engine given a missing
    reference renders *uncloned* rather than failing, so the line comes back in
    a default voice matching nothing else in the dub, with no error anywhere.
    Re-running the full dub re-extracts the clips — which is exactly why that
    appears to fix it, and is the workaround the reporter found unaided.

    Deliberately DIAGNOSTIC ONLY: ``ref_audio`` is returned unchanged. Nulling
    it would not change what the user hears (the engine already falls back),
    and it would decide on the engine's behalf that a reference it cannot
    ``stat`` is unusable — untrue for anything resolved inside a sidecar's own
    namespace. The defect here is the silence, not the fallback.
    """
    if not ref_audio:
        return ref_audio
    try:
        if os.path.exists(ref_audio):
            return ref_audio
    except OSError:  # unreadable path (permissions, dead mount) — same symptom
        pass
    seen = _MISSING_REF_WARNED.setdefault(str(job_id), set())
    key = (str(seg_id), str(ref_audio))
    if key not in seen:
        seen.add(key)
        logger.warning(
            "%s: the voice reference for segment %s is gone (%s), so this line "
            "will most likely render in a DEFAULT voice instead of the cloned "
            "one, with no error of its own. Clone clips are extracted per job "
            "and the job outlives them, so a saved dub regenerated after "
            "cleanup loses them — re-running the full dub re-extracts the "
            "clips, which is why that appears to fix it (#1331).",
            where, seg_id, ref_audio,
        )
    return ref_audio


def forget_missing_ref_warnings(job_id: str) -> None:
    """Drop the once-per-segment warning memo for a job (a fresh render should
    warn again if the clips are still gone)."""
    _MISSING_REF_WARNED.pop(str(job_id), None)



def _ref_within_limit(info) -> bool:
    from services.speaker_clone import MAX_REF_DURATION_S
    return bool(info) and float(info.get("duration") or 0.0) <= MAX_REF_DURATION_S


def resolve_consistent_ref(job: dict, speaker_key: str, memo: dict | None = None):
    """ONE clone reference for every segment of `speaker_key`.

    Preference order:
      1. the pooled per-speaker clone (job["speaker_clones"]) — same lookup
         the per-line path uses as its fallback;
      2. no speaker clone (heuristic diarization skips extraction entirely —
         the key case): a deterministic pick among that speaker's per-segment
         clips: longest usable clip ≥3 s within the shared reference limit,
         tie-break lowest segment id. Short clips fall back to longest usable.
         Oversized references must never strand every short line for a speaker.

    Returns the clone info dict ({"ref_audio", "ref_text", ...}) or None.
    Pure function of the job dict; `memo` (keyed by speaker_key) just avoids
    rescanning per segment — the pick is deterministic with or without it.
    """
    if memo is not None and speaker_key in memo:
        return memo[speaker_key]

    from services.speaker_clone import MAX_REF_DURATION_S

    ref = _find_speaker_clone(job.get("speaker_clones") or {}, speaker_key)
    if ref and float(ref.get("duration") or 0.0) > MAX_REF_DURATION_S:
        ref = None
    if ref is None:
        seg_clones = job.get("segment_clones") or {}
        candidates = []
        for row in job.get("segments") or []:
            if not isinstance(row, dict):
                continue
            spk = row.get("speaker_id") or "Speaker 1"
            if not _speaker_key_matches(spk, speaker_key):
                continue
            sid = str(row.get("id", ""))
            info = seg_clones.get(sid)
            if (info and info.get("ref_audio")
                    and float(info.get("duration") or 0.0) <= MAX_REF_DURATION_S):
                candidates.append((sid, info))
        if candidates:
            usable = [
                c for c in candidates
                if float(c[1].get("duration") or 0.0) >= CONSISTENT_MIN_REF_S
            ] or candidates
            usable.sort(
                key=lambda c: (-float(c[1].get("duration") or 0.0), _seg_id_order(c[0]))
            )
            ref = usable[0][1]

    if memo is not None:
        memo[speaker_key] = ref
    return ref


def _remote_voice(job: dict, profile_id: str | None, seg_id, voice_match: str,
                  memo: dict) -> tuple[str | None, str | None, bool, str | None, int | None]:
    """Resolve a dub binding without touching the TTS model."""
    ref_audio = ref_text = instruct = None
    seed = None
    single_use = False
    if profile_id and profile_id.startswith("auto-seg:"):
        sid = profile_id[len("auto-seg:"):]
        info = (job.get("segment_clones") or {}).get(sid)
        shared = False
        if voice_match == "consistent" and sid == str(seg_id):
            key = _speaker_key_for_segment(job, sid)
            alternate = resolve_consistent_ref(job, key, memo) if key else None
            if alternate:
                info = alternate
                shared = True
        if info:
            ref_audio, ref_text = info.get("ref_audio"), info.get("ref_text")
            single_use = not shared
    elif profile_id and profile_id.startswith("auto:"):
        key = profile_id[len("auto:"):]
        if voice_match == "consistent":
            info = resolve_consistent_ref(job, key, memo)
        else:
            info = ((job.get("segment_clones") or {}).get(str(seg_id))
                    or _find_speaker_clone(job.get("speaker_clones") or {}, key))
            single_use = str(seg_id) in (job.get("segment_clones") or {})
            if not _ref_within_limit(info):
                info = resolve_consistent_ref(job, key, memo)
                single_use = False
        if info:
            ref_audio, ref_text = info.get("ref_audio"), info.get("ref_text")
    elif profile_id:
        with db_conn() as conn:
            row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if row:
            seed = row["seed"]
            if row["is_locked"] and row["locked_audio_path"]:
                ref_audio = os.path.join(VOICES_DIR, row["locked_audio_path"])
                ref_text = row["ref_text"]
            elif row["instruct"] and not row["is_locked"]:
                try:
                    vd_states = row["vd_states"]
                except (KeyError, IndexError):
                    vd_states = None
                instruct = heal_design_instruct(row["instruct"], vd_states)
            else:
                ref_audio = os.path.join(VOICES_DIR, row["ref_audio_path"])
                ref_text = row["ref_text"]
    return ref_audio, ref_text, single_use, instruct, seed


def _decode_remote_dub(result: gpu_gateway.RemoteResult) -> dict[int, str]:
    """Extract the worker bundle into a task-scoped directory, path-safely."""
    target = os.path.join(DUB_DIR, ".remote", result.task_id)
    try:
        return extract_segment_wavs(result.path or "", target)
    except ValueError as exc:
        # Preserve the established route-specific error wording consumed by
        # diagnostics and regression tests.
        raise ValueError(str(exc).replace("segment artifact", "dub artifact")) from exc


router = APIRouter()

@router.post("/dub/generate/{job_id}")
async def dub_generate(job_id: str, req: DubRequest):
    """Adds a dub generation job to the async batch task pool."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail="This dub session has expired or was never created. Re-upload the video to start a new one.",
        )

    # ── Engine resolution (issue #312 class) ────────────────────────────────
    # Every rendered segment clones either source speech or a saved profile, so
    # local execution still requires a cloning-capable engine. Remote execution
    # validates the selected worker here without loading duplicate local weights;
    # its local backend is prepared only if gateway fallback actually selects it.
    try:
        engine_id, decision, backend = await _resolve_dub_execution()
    except gpu_gateway.ModelNotDownloaded as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "model_not_downloaded",
                "message": str(e),
                "engine": e.engine,
                "repo_ids": e.repo_ids,
                "target": e.target,
                "target_label": e.target_label,
                "downloadable": e.downloadable,
            },
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        from core.failure import is_gpu_oom

        if not is_gpu_oom(e):
            raise
        from core.public_errors import public_exception_response

        payload = public_exception_response(
            e,
            fallback="The TTS model could not be loaded.",
        )
        raise HTTPException(status_code=503, detail=payload["detail"]) from e

    # Resolve the global profile once for this job. Explicit Production
    # overrides remain authoritative, while ordinary Dubbing now follows the
    # same Fast/Balanced/Quality/Max contract as Clone and long-form work.
    from services.performance_profiles import tts_defaults
    _profile_defaults = tts_defaults(engine_id)
    _job_num_step = req.num_step if req.num_step is not None else _profile_defaults.get("num_step", 16)
    _job_postprocess = _profile_defaults.get("postprocess_output", True)

    async def _stream(task_id):
        total = len(req.segments)
        all_segment_wavs = []
        sync_scores = []

        # Track language for this run. Everything per-track — the per-segment
        # WAV cache, fingerprints, seg_wav_kind — is keyed by it (P1.3) so a
        # multi-language job's tracks can't cross-contaminate.
        lang_code = req.language_code or "und"

        def _seg_lang_path(seg_key) -> str:
            # Per-language per-segment WAV: seg_{lang}_{id}.wav. Built through
            # dub_seg_path so the sanitisation + DUB_DIR containment guard
            # apply to the combined key. Legacy un-keyed seg_{id}.wav files
            # remain readable via the gated fallback (_legacy_seg_cache_ok).
            return dub_seg_path(job_id, f"{lang_code}_{seg_key}")

        # Throttle the device cache flush. empty_cache() is a synchronous
        # device stall, so calling it every segment (as the old code did)
        # serialised the GPU loop; the batched-I/O design it replaced kept
        # it off the hot path on purpose. Flush every ~16 releases instead —
        # frequent enough to bound VRAM, rare enough to stay invisible.
        from services.model_manager import release_device_cache

        _RELEASE_FLUSH_EVERY = 16
        _release_count = {"n": 0}

        def _release_audio_tensors(*objs) -> None:
            """Best-effort VRAM cleanup after a segment is safely on disk.

            Tensors are freed by the callers' own ``del`` once they fall out
            of scope; this only throttles the device cache flush, which has to
            cover every backend an engine can synthesize on (CUDA, MPS, XPU,
            Ascend NPU) — the CUDA/MPS pair this used to open-code skipped the
            rest. ``*objs`` is kept for call-site compatibility but
            intentionally unused — a local ``del`` here would only unbind the
            parameter, never the caller's reference.
            """
            _release_count["n"] += 1
            if _release_count["n"] % _RELEASE_FLUSH_EVERY != 0:
                return
            release_device_cache()

        # mix_<id> scratch WAVs written for silence/cached-fail/error slots are
        # pure assembly inputs (no preview/regen contract), so they're deleted
        # once the final track is written.
        _mix_temp_paths: list[str] = []

        def _store_mix_wav(start: float, end: float, wav: torch.Tensor, sr: int, seg_key: str):
            """Write one segment to disk and keep only its path in the mix manifest.

            A zero/negative-length buffer is never written (``atomic_save_wav``
            raises on empty audio); instead a harmless zero-length in-memory
            entry is returned, which the assembly tolerates via its ``e > s``
            guard.
            """
            if wav.shape[-1] <= 0:
                return (start, end, torch.zeros(1, 0), sr)
            path = dub_seg_path(job_id, seg_key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            atomic_save_wav(path, wav.detach().cpu(), sr)
            if seg_key.startswith("mix_"):
                _mix_temp_paths.append(path)
            _release_audio_tensors(wav)
            return (start, end, path, sr)

        def _entry_num_samples(entry) -> int:
            # Zero/negative-duration slots are kept as in-memory tensors (never
            # written to disk); report their length directly.
            if isinstance(entry[2], torch.Tensor):
                return int(entry[2].shape[-1])
            try:
                info = torchaudio.info(entry[2])
                return int(info.num_frames)
            except Exception:
                wav, _sr = torchaudio.load(entry[2])
                n = int(wav.shape[-1])
                _release_audio_tensors(wav)
                return n

        def _load_entry_wav(entry, target_sr: int) -> torch.Tensor:
            if isinstance(entry[2], torch.Tensor):
                return entry[2]
            wav, loaded_sr = torchaudio.load(entry[2])
            if loaded_sr != target_sr:
                import torchaudio.functional as AF
                wav = AF.resample(wav, loaded_sr, target_sr)
            return wav

        def _write_memmap_wav_atomic(target_path: str, samples, sample_rate: int) -> None:
            """Write a mono float32 memmap to int16 WAV without loading it all.

            Intentionally does NOT watermark: the final track is assembled from
            per-segment WAVs that were already watermarked once at synthesis
            time (see the seg-write path below), exactly as ``main`` does.
            Re-marking here would double-mark every segment in the final mix.
            """
            import tempfile
            import wave
            import numpy as np

            target_dir = os.path.dirname(target_path) or "."
            target_base = os.path.basename(target_path)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{target_base}.",
                suffix=".wav",
                dir=target_dir,
            )
            os.close(fd)
            chunk_samples = max(sample_rate * 30, 1)
            try:
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    total_len = int(samples.shape[0])
                    for off in range(0, total_len, chunk_samples):
                        chunk = np.array(samples[off: off + chunk_samples], dtype=np.float32, copy=True)
                        if chunk.size == 0:
                            continue
                        np.nan_to_num(chunk, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
                        chunk = np.clip(chunk, -1.0, 1.0)
                        pcm = (chunk * 32767.0).astype("<i2", copy=False)
                        wf.writeframes(pcm.tobytes())
                os.replace(tmp_path, target_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        # Phase 4.1 — partial regen. If `regen_only` is set, we only run TTS
        # on segments whose id is in that set; the others reuse their existing
        # `seg_i.wav` on disk and slot into the final mix unchanged.
        regen_only = set(req.regen_only or []) if req.regen_only is not None else None
        seg_ids = req.segment_ids or []
        strategy = (req.timing_strategy or "concise").lower()
        # Voice-identity mode (see DubRequest.voice_match). The memo makes the
        # "consistent" pick once per speaker and hands the SAME reference to
        # every segment of that speaker for the whole run.
        voice_match = (req.voice_match or "per_line").lower()
        _consistent_ref_memo: dict = {}
        remote_audio: dict[int, str] = {}
        # Strategy-transition guard: concise, stretch_video and smart_fit all
        # re-mix *natural-rate* per-segment WAVs. If the previous run used
        # strict_slot, the on-disk WAVs are slot-squeezed ("slotted") — the
        # missing tails cannot be recovered by a re-mix. Force one full regen;
        # afterwards partial regen / fit-only re-mix (regen_only=[]) is safe.
        # Jobs predating this field have unknown kind → also regen once.
        # P1.3: the kind is per-track now (each language renders under its own
        # strategy); the flat job["seg_wav_kind"] is only consulted for jobs
        # written before the per-language map existed — once the map is
        # present, a language without an entry has unknown-kind WAVs (or none
        # at all) and must regen once, exactly like the pre-field case.
        _kind_map = job.get("seg_wav_kind_by_lang")
        _wav_kind = (
            _kind_map.get(lang_code) if isinstance(_kind_map, dict) else job.get("seg_wav_kind")
        )
        if regen_only is not None and _wav_kind != "natural":
            regen_only = None
        # A partial rerun must repair absent or corrupt caches, including before
        # remote batching decides which lines need synthesis.
        if regen_only is not None:
            for index, segment in enumerate(req.segments):
                sid = seg_ids[index] if index < len(seg_ids) else f"seg_{index}"
                if sid in regen_only or not segment.text.strip():
                    continue
                cache = _seg_lang_path(sid)
                if not os.path.exists(cache) and _legacy_seg_cache_ok(job, lang_code):
                    for key in (sid, index):
                        legacy = dub_seg_path(job_id, key)
                        if os.path.exists(legacy):
                            cache = legacy
                            break
                try:
                    info = torchaudio.info(cache)
                    intact = info.num_frames > 0 and _cached_payload_intact(cache, info)
                except Exception:
                    intact = False
                if not intact:
                    regen_only.add(sid)
        # Manifest: stable segment id per current index. Per-segment WAVs are
        # named by stable id (dub_seg_path) so regen reuses the right audio after
        # reorder; index-keyed readers (preview/export) resolve via this manifest.
        job["seg_order"] = [seg_ids[k] if k < len(seg_ids) else f"seg_{k}" for k in range(len(req.segments))]

        # Per-segment metadata to persist after the hot loop. Audio itself is
        # written immediately and only file paths are kept, so long videos don't
        # retain every generated tensor in RAM until final assembly.
        _pending_seg_writes: list[tuple] = []

        # Calibration records for the pre-synthesis duration planner
        # (services/duration_planner.py): text length + the NATURAL-rate TTS
        # duration of every freshly synthesized segment. Only meaningful for
        # the natural-rate strategies — strict_slot forces the audio to the
        # slot length, which would poison the observed chars-per-second.
        _natural_dur_records: dict[str, dict] = {}

        # Phase 4.1 bench instrumentation: measure where incremental time goes.
        # Only prints when regen_only is active (real-user incremental path).
        _t_start = time.perf_counter()
        _t_cache = 0.0
        _t_tts = 0.0
        _batched_audio: dict[int, torch.Tensor] = {}
        _profile_row_cache: dict[str, object | None] = {}
        _has_native_batch = (
            getattr(type(backend), "generate_batch", TTSBackend.generate_batch)
            is not TTSBackend.generate_batch
        )
        _native_batch_width = native_batch_width(backend) if _has_native_batch else 1

        async def _prepare_local_dub():
            """Load local TTS only when gateway fallback actually needs it."""
            nonlocal backend, _has_native_batch, _native_batch_width
            if isinstance(backend, _RemoteDubBackend):
                backend = await resolve_generation_backend(require_cloning=True)
                _has_native_batch = (
                    getattr(type(backend), "generate_batch", TTSBackend.generate_batch)
                    is not TTSBackend.generate_batch
                )
                _native_batch_width = (
                    native_batch_width(backend) if _has_native_batch else 1
                )
            return gpu_gateway.LocalCall(fn=lambda: {})

        def _segment_generation_args(index, segment) -> dict:
            """Resolve the per-row controls shared by serial and native batches."""
            current_id = seg_ids[index] if index < len(seg_ids) else f"seg_{index}"
            duration = segment.end - segment.start
            profile_id = segment.profile_id or None
            speed = segment.speed if segment.speed is not None else req.speed
            language = segment.target_lang or req.language
            instruct = segment.instruct or req.instruct
            direction_text = getattr(segment, "direction", None)
            if direction_text and direction_text.strip():
                try:
                    from services.director import parse as _parse_direction

                    direction = _parse_direction(direction_text)
                    extra = direction.instruct_prompt()
                    if extra:
                        instruct = f"{instruct}, {extra}" if instruct else extra
                    bias = direction.rate_bias()
                    if (
                        bias
                        and abs(bias - 1.0) > 0.01
                        and strategy == "strict_slot"
                    ):
                        speed = (speed or 1.0) * bias
                except Exception as error:
                    logger.debug("direction parse skipped for %s: %s", current_id, error)
            return {
                "seg_id": current_id,
                "text": segment.text,
                "language": language,
                "instruct": instruct,
                "duration": duration if strategy == "strict_slot" else None,
                "num_step": 8 if req.preview else _job_num_step,
                "guidance_scale": req.guidance_scale,
                "speed": speed,
                "profile_id": profile_id,
                "effect_preset": getattr(segment, "effect_preset", None) or "broadcast",
            }

        # One coarse remote lease for every segment that actually needs fresh
        # synthesis. Assembly, fitting and the separately-pooled RVC pass stay
        # here; the worker returns a single verified bundle of segment WAVs.
        if decision.remote:
            remote_rows: list[dict] = []
            remote_refs: list[str | None] = []
            for i, seg in enumerate(req.segments):
                seg_id = seg_ids[i] if i < len(seg_ids) else f"seg_{i}"
                if (regen_only is not None and seg_id not in regen_only) or not seg.text.strip():
                    continue
                ref_audio, ref_text, ref_single_use, profile_instruct, seed = _remote_voice(
                    job, seg.profile_id or None, seg_id, voice_match, _consistent_ref_memo
                )
                ref_audio = warn_if_ref_missing(
                    ref_audio, job_id=job_id, seg_id=seg_id, where="remote dub render"
                )
                seg_instruct = seg.instruct or req.instruct or profile_instruct
                seg_speed = seg.speed if seg.speed is not None else req.speed
                if seg.direction and seg.direction.strip():
                    try:
                        from services.director import parse as _parse_direction
                        direction = _parse_direction(seg.direction)
                        extra = direction.instruct_prompt()
                        if extra:
                            seg_instruct = f"{seg_instruct}, {extra}" if seg_instruct else extra
                        bias = direction.rate_bias()
                        if bias and abs(bias - 1.0) > 0.01 and strategy == "strict_slot":
                            seg_speed = (seg_speed or 1.0) * bias
                    except Exception:
                        logger.debug("direction parse skipped for remote segment %s", seg_id,
                                     exc_info=True)
                remote_rows.append({
                    "index": i, "text": seg.text,
                    "language": seg.target_lang or req.language,
                    "ref_text": ref_text, "ref_single_use": ref_single_use,
                    "instruct": seg_instruct,
                    "duration": (seg.end - seg.start) if strategy == "strict_slot" else None,
                    "num_step": 8 if req.preview else _job_num_step,
                    "postprocess_output": _job_postprocess,
                    "guidance_scale": req.guidance_scale, "speed": seg_speed,
                    "effect_preset": seg.effect_preset or "broadcast",
                    "seed": seed,
                    # RVC changes the waveform locally after TTS, so that path
                    # is marked at the existing post-RVC chokepoint below.
                    "watermark": not rvc_is_enabled(),
                })
                remote_refs.append(ref_audio)
            if remote_rows:
                states: asyncio.Queue = asyncio.Queue()
                call = gpu_gateway.RemoteCall(
                    engine=engine_id, operation="dub_segments",
                    params={"segments": remote_rows, "ref_audio": remote_refs},
                    decode=_decode_remote_dub,
                )
                dub_run = gpu_gateway.JobRun("dub_segments")
                run = asyncio.create_task(gpu_gateway.run(
                    "dub_segments", local=gpu_gateway.LocalCall(prepare=_prepare_local_dub),
                    remote=call, decision=decision, job=dub_run,
                    on_state=states.put_nowait,
                ))
                while not run.done():
                    if task_manager.is_cancelled(task_id):
                        run.cancel()
                        try:
                            await run
                        except asyncio.CancelledError:
                            pass
                        yield f"data: {json.dumps({'type': 'cancelled', 'segments_processed': 0})}\n\n"
                        return
                    try:
                        state = await asyncio.wait_for(states.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    fraction = float(state.get("progress") or 0.0)
                    yield f"data: {json.dumps({'type': 'progress', 'current': round(fraction * total, 2), 'total': total, 'text': state.get('stage') or state.get('phase')})}\n\n"
                try:
                    remote_audio = await run
                except Exception as error:
                    from core.public_errors import stream_generation_failure

                    detail = stream_generation_failure(error)["detail"]
                    yield f"data: {json.dumps({'type': 'error', 'error': detail})}\n\n"
                    return
                notice = dub_run.notice()
                if notice is not None:
                    yield f"data: {json.dumps({'type': 'routing_notice', 'status': notice[0], 'reason': notice[1]})}\n\n"

        if remote_audio and isinstance(backend, _RemoteDubBackend):
            first_remote = next(iter(remote_audio.values()))
            backend.sample_rate = int(torchaudio.info(first_remote).sample_rate)
        elif isinstance(backend, _RemoteDubBackend):
            # Fit-only / cache-only reruns synthesize nothing. Keep the cached
            # track's native rate when one exists instead of resampling it to
            # the carrier's conservative 24 kHz default.
            for cached_id in seg_ids:
                cached_path = _seg_lang_path(cached_id)
                if os.path.exists(cached_path):
                    try:
                        backend.sample_rate = int(torchaudio.info(cached_path).sample_rate)
                        break
                    except Exception:
                        continue

        for i, seg in enumerate(req.segments):
            seg_id = seg_ids[i] if i < len(seg_ids) else f"seg_{i}"

            # Check abort flag before each segment
            if task_manager.is_cancelled(task_id):
                yield f"data: {json.dumps({'type': 'cancelled', 'segments_processed': i})}\n\n"
                return

            if not remote_audio:
                yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total, 'text': seg.text[:50]})}\n\n"

            seg_duration = seg.end - seg.start
            if seg_duration <= 0.05 or not seg.text.strip():
                sr = backend.sample_rate
                # max(0, …): a zero/negative-duration slot must not feed a
                # negative length to torch.zeros (raises) — _store_mix_wav
                # turns the empty buffer into a harmless in-memory entry.
                silence = torch.zeros(1, max(0, int(seg_duration * sr)))
                all_segment_wavs.append(_store_mix_wav(seg.start, seg.end, silence, sr, f"mix_{seg_id}"))
                try:
                    del silence
                except Exception:
                    pass
                _release_audio_tensors()
                sync_scores.append(1.0)
                continue

            # Partial regen: if this segment isn't in the allow-list, reuse its
            # previously-rendered WAV so the final mix still covers the timeline.
            if regen_only is not None and seg_id not in regen_only:
                # This track's own cache first (seg_{lang}_{id}.wav). Legacy
                # un-keyed files (seg_{id}.wav / seg_{index}.wav) are reused
                # ONLY when no other-language track exists on the job — a
                # multi-track job's un-keyed files hold whichever language
                # rendered last, and splicing them here was exactly how
                # "Regen N changed" mixed language B into track A (P1.3).
                seg_wav_path = _seg_lang_path(seg_id)
                if not os.path.exists(seg_wav_path) and _legacy_seg_cache_ok(job, lang_code):
                    for _legacy_key in (seg_id, i):
                        _legacy = dub_seg_path(job_id, _legacy_key)
                        if os.path.exists(_legacy):
                            seg_wav_path = _legacy
                            break
                if os.path.exists(seg_wav_path):
                    try:
                        _t_cache_0 = time.perf_counter()
                        # Natural-rate caches are already the exact assembly
                        # input.  Keep the durable path in the manifest so the
                        # mixer decodes it once; the old path decoded here,
                        # wrote an identical mix_<id> scratch WAV, then decoded
                        # that copy again.  Header-only inspection preserves
                        # the resample fallback for caches made by an engine
                        # with a different sample rate.
                        if strategy != "strict_slot":
                            try:
                                cached_info = torchaudio.info(seg_wav_path)
                            except Exception:
                                cached_info = None
                            if (
                                cached_info is not None
                                and int(cached_info.sample_rate) == int(backend.sample_rate)
                                and _cached_payload_intact(seg_wav_path, cached_info)
                            ):
                                all_segment_wavs.append(
                                    (seg.start, seg.end, seg_wav_path, backend.sample_rate)
                                )
                                sync_scores.append(round(cached_info.num_frames / cached_info.sample_rate / max(seg_duration, 0.01), 3))
                                _t_cache += time.perf_counter() - _t_cache_0
                                continue

                        cached_wav, cached_sr = torchaudio.load(seg_wav_path)
                        if cached_sr != backend.sample_rate:
                            import torchaudio.functional as AF
                            cached_wav = AF.resample(cached_wav, cached_sr, backend.sample_rate)
                        cached_ratio = round(cached_wav.shape[-1] / backend.sample_rate / max(seg_duration, 0.01), 3)
                        all_segment_wavs.append(_store_mix_wav(seg.start, seg.end, cached_wav, backend.sample_rate, f"mix_{seg_id}"))
                        try:
                            del cached_wav
                        except Exception:
                            pass
                        _release_audio_tensors()
                        sync_scores.append(cached_ratio)
                        _t_cache += time.perf_counter() - _t_cache_0
                        continue
                    except Exception:
                        logger.exception("Dub cached segment could not be read: %s", seg_id)
                yield f"data: {json.dumps({'type': 'error', 'segment': i, 'segment_id': seg_id, 'error_code': 'dub_speech_missing', 'error': 'Cached speech could not be read. Regenerate this segment before exporting.'})}\n\n"
                return

            def _gen(text, lang, instruct_str, dur_s, nstep, cfg, spd, profile_id, effect_preset,
                     *, execution_target="local", prepare_only=False, current_seg_id=None):
                # Normalize once at the segment's text→engine choke point
                # (covers the OOM-retry generate below too, which reuses this
                # closure's `text`). Pref-gated, idempotent, never raises.
                from services.text_normalization import normalize_for_tts
                text = normalize_for_tts(text, lang)

                effective_seg_id = seg_id if current_seg_id is None else current_seg_id
                ref_audio = None
                ref_text = None
                used_seed = None
                # Per-segment refs are a distinct file per segment, each used
                # exactly once in this render — telling the prompt cache to
                # store them would evict the per-speaker / locked-profile
                # prompts that every OTHER segment reuses (LRU of 8 vs
                # potentially hundreds of segment clips). cache_ref=False =
                # "encode it, don't let it displace anything".
                ref_single_use = False

                # Auto-clones extracted from the source video during prepare
                # (see services/speaker_clone.py) live at job["speaker_clones"]
                # keyed by speaker_id. We use the `auto:` prefix so they can't
                # collide with persistent voice_profiles.id values.
                # Wave 3.2: a per-segment clone ref (cut from this line's own
                # source audio) takes precedence over the per-speaker clone.
                if profile_id and profile_id.startswith("auto-seg:"):
                    sid = profile_id[len("auto-seg:"):]
                    info = (job.get("segment_clones") or {}).get(sid)
                    # voice_match="consistent": an auto-seg binding to the
                    # segment's OWN id is the server default from prepare —
                    # heuristic diarization skips speaker-clone extraction, so
                    # every long line gets `auto-seg:{its own id}` (see
                    # dub_core's assignment loop). That's not a user choice
                    # (the Voice dropdown can't even render auto-seg ids), so
                    # swap it for the speaker's ONE consistent reference. A
                    # CROSS binding (sid != this segment) can only come from an
                    # explicit request — honour its clip unchanged.
                    _consistent_alt = None
                    if voice_match == "consistent" and sid == str(effective_seg_id):
                        _spk_key = _speaker_key_for_segment(job, sid)
                        if _spk_key:
                            _consistent_alt = resolve_consistent_ref(
                                job, _spk_key, _consistent_ref_memo
                            )
                    if _consistent_alt:
                        ref_audio = _consistent_alt.get("ref_audio")
                        ref_text = _consistent_alt.get("ref_text")
                        # Shared by every segment of the speaker → multi-use;
                        # keep it warm in the prompt cache (#1132 semantics).
                    elif info:
                        ref_audio = info.get("ref_audio")
                        ref_text = info.get("ref_text")
                        ref_single_use = True
                    profile_id = None  # prevent the voice_profiles lookup below

                elif profile_id and profile_id.startswith("auto:"):
                    key = profile_id[len("auto:"):]
                    if voice_match == "consistent":
                        # ONE reference per speaker for the whole dub: the
                        # pooled per-speaker clone, else the deterministic
                        # segment-clip pick (heuristic-diarized jobs have no
                        # speaker_clones at all — the key case). Multi-use by
                        # construction → ref_single_use stays False so the
                        # prompt cache keeps it warm across segments (#1132).
                        auto = resolve_consistent_ref(job, key, _consistent_ref_memo)
                        if auto:
                            ref_audio = auto.get("ref_audio")
                            ref_text = auto.get("ref_text")
                    else:
                        # per_line (DEFAULT) — #486: an `auto:{speaker}`
                        # binding still prefers THIS segment's own per-segment
                        # ref when one exists (cut from this line's source
                        # audio → matches its prosody), falling back to the
                        # per-speaker clone otherwise. This keeps the Wave 3.2
                        # per-segment-ref quality win while letting every
                        # segment carry the UI-visible `auto:` id the dub
                        # editor's Voice dropdown can actually render ("From
                        # Video → Speaker N"). `seg_id` is closed over from
                        # the per-segment loop below.
                        segment_speaker_key = _speaker_key_for_segment(
                            job, effective_seg_id
                        )
                        # Legacy jobs may not persist diarized segment rows.
                        # Preserve their established per-line preference; only
                        # suppress it when current metadata proves the user
                        # explicitly selected a different speaker.
                        selected_is_segment_speaker = (
                            segment_speaker_key is None or segment_speaker_key == key
                        )
                        seg_ref = (
                            (job.get("segment_clones") or {}).get(str(effective_seg_id))
                            if selected_is_segment_speaker
                            else None
                        )
                        if _ref_within_limit(seg_ref):
                            ref_audio = seg_ref.get("ref_audio")
                            ref_text = seg_ref.get("ref_text")
                            ref_single_use = True
                        else:
                            auto = _find_speaker_clone(
                                job.get("speaker_clones") or {}, key
                            )
                            if not _ref_within_limit(auto):
                                # Short lines may have no line-specific clip.
                                # Reuse this speaker's best source instead of
                                # silently reverting to the engine default.
                                auto = resolve_consistent_ref(
                                    job, key, _consistent_ref_memo
                                )
                            if auto:
                                ref_audio = auto.get("ref_audio")
                                ref_text = auto.get("ref_text")
                    profile_id = None  # prevent the voice_profiles lookup below

                if profile_id:
                    if profile_id not in _profile_row_cache:
                        with db_conn() as conn:
                            _profile_row_cache[profile_id] = conn.execute(
                                "SELECT * FROM voice_profiles WHERE id=?",
                                (profile_id,),
                            ).fetchone()
                    row = _profile_row_cache[profile_id]
                    if row:
                        if row["is_locked"] and row["locked_audio_path"]:
                            ref_audio = os.path.join(VOICES_DIR, row["locked_audio_path"])
                            ref_text = row["ref_text"]
                            used_seed = row["seed"]
                        elif row["instruct"] and not row["is_locked"]:
                            used_seed = row["seed"] 
                        else:
                            ref_audio = os.path.join(VOICES_DIR, row["ref_audio_path"])
                            ref_text = row["ref_text"]
                            used_seed = row["seed"]
                            
                        if not instruct_str:
                            try:
                                _vd = row["vd_states"]
                            except (KeyError, IndexError):
                                _vd = None
                            instruct_str = heal_design_instruct(row["instruct"], _vd)

                if used_seed is not None and not prepare_only:
                    torch.manual_seed(used_seed)

                # Last gate before the engine: every resolution branch above
                # produces a PATH, and none of them can know it still exists.
                ref_audio = warn_if_ref_missing(
                    ref_audio, job_id=job_id, seg_id=effective_seg_id, where="dub render",
                )

                if prepare_only:
                    return {
                        "text": text,
                        "language": lang if lang != "Auto" else None,
                        "ref_audio": ref_audio,
                        "ref_text": ref_text,
                        "cache_ref": not ref_single_use,
                        "instruct": instruct_str if instruct_str else None,
                        "duration": dur_s,
                        "num_step": nstep,
                        "guidance_scale": cfg,
                        "speed": spd,
                        "denoise": True,
                        "postprocess_output": _job_postprocess,
                        "effect_preset": effect_preset or "broadcast",
                        "seed": used_seed,
                    }

                try:
                    audio_out = backend.generate(
                        text=text, language=lang if lang != "Auto" else None,
                        ref_audio=ref_audio, ref_text=ref_text,
                        cache_ref=not ref_single_use,
                        instruct=instruct_str if instruct_str else None,
                        duration=dur_s, num_step=nstep, guidance_scale=cfg,
                        speed=spd, denoise=True, postprocess_output=_job_postprocess,
                    )
                    sr = backend.sample_rate

                    # Apply per-segment DSP effect preset (default: broadcast)
                    seg_effect_preset = effect_preset or "broadcast"
                    if seg_effect_preset == "raw":
                        return audio_out

                    mastered_audio = audio_out
                    if not getattr(backend, "applies_own_mastering", False):
                        mastered_audio = apply_mastering(audio_out, sample_rate=sr)
                    effect_chain = get_effect_chain(seg_effect_preset)
                    if effect_chain:
                        mastered_audio = apply_effects_chain(
                            mastered_audio,
                            sample_rate=sr,
                            chain=effect_chain,
                        )
                    return normalize_audio(mastered_audio, target_dBFS=-2.0)
                except Exception as e:
                    if not _prepare_oom_retry(e, execution_target=execution_target):
                        raise

                    retry_steps = min(nstep, 8)
                    logger.warning(
                        "OOM on segment (nstep=%d), retrying with %d steps after cache flush",
                        nstep, retry_steps,
                    )
                    try:
                        # An OOM retry on a single-use ref pays the reference
                        # encode a second time (~0.4s) — deliberate: caching it
                        # would reintroduce the eviction this flag exists to
                        # prevent, to optimize a path that only runs after an
                        # OOM already cost seconds.
                        audio_out = backend.generate(
                            text=text, language=lang if lang != "Auto" else None,
                            ref_audio=ref_audio, ref_text=ref_text,
                            cache_ref=not ref_single_use,
                            instruct=instruct_str if instruct_str else None,
                            duration=dur_s, num_step=retry_steps, guidance_scale=cfg,
                            speed=spd, denoise=True, postprocess_output=_job_postprocess,
                        )
                        sr = backend.sample_rate

                        seg_effect_preset = effect_preset or "broadcast"
                        if seg_effect_preset == "raw":
                            return audio_out

                        mastered_audio = audio_out
                        if not getattr(backend, "applies_own_mastering", False):
                            mastered_audio = apply_mastering(audio_out, sample_rate=sr)
                        effect_chain = get_effect_chain(seg_effect_preset)
                        if effect_chain:
                            mastered_audio = apply_effects_chain(
                                mastered_audio,
                                sample_rate=sr,
                                chain=effect_chain,
                            )
                        return normalize_audio(mastered_audio, target_dBFS=-2.0)
                    except Exception as retry_err:
                        raise RuntimeError(
                            f"Ran out of GPU memory generating this segment. "
                            f"Retried with {retry_steps} steps but still failed. "
                            f"Try the Flush button in the header to free VRAM, "
                            f"or switch to CPU in Settings. "
                            f"Underlying error: {retry_err}"
                        ) from retry_err

            async def _prefetch_native_batch(first_index: int) -> None:
                """Render one bounded batch and retain only its small output window."""
                if _native_batch_width < 2 or remote_audio:
                    return
                batch: list[tuple[int, dict]] = []
                compatibility = None
                for candidate_index in range(first_index, len(req.segments)):
                    candidate = req.segments[candidate_index]
                    candidate_id = (
                        seg_ids[candidate_index]
                        if candidate_index < len(seg_ids)
                        else f"seg_{candidate_index}"
                    )
                    if (
                        candidate_index in _batched_audio
                        or candidate.end - candidate.start <= 0.05
                        or not candidate.text.strip()
                        or (
                            regen_only is not None
                            and candidate_id not in regen_only
                        )
                    ):
                        continue
                    args = _segment_generation_args(candidate_index, candidate)
                    try:
                        prepared = _gen(
                            args["text"],
                            args["language"],
                            args["instruct"],
                            args["duration"],
                            args["num_step"],
                            args["guidance_scale"],
                            args["speed"],
                            args["profile_id"],
                            args["effect_preset"],
                            prepare_only=True,
                            current_seg_id=args["seg_id"],
                        )
                    except Exception:
                        if candidate_index == first_index:
                            raise
                        break
                    # Fixed-seed profiles deliberately keep their established
                    # one-row deterministic RNG contract.
                    if prepared["seed"] is not None:
                        if candidate_index == first_index:
                            return
                        break
                    candidate_compatibility = (
                        prepared["cache_ref"],
                        bool(prepared["ref_audio"]),
                        prepared["num_step"],
                        prepared["guidance_scale"],
                        prepared["postprocess_output"],
                    )
                    if compatibility is None:
                        compatibility = candidate_compatibility
                    elif candidate_compatibility != compatibility:
                        break
                    batch.append((candidate_index, prepared))
                    if len(batch) >= _native_batch_width:
                        break
                if len(batch) < 2:
                    return

                def _render_batch() -> list[torch.Tensor]:
                    prepared_rows = [prepared for _, prepared in batch]
                    outputs = backend.generate_batch(
                        [prepared["text"] for prepared in prepared_rows],
                        language=[prepared["language"] for prepared in prepared_rows],
                        ref_audio=[prepared["ref_audio"] for prepared in prepared_rows],
                        ref_text=[prepared["ref_text"] for prepared in prepared_rows],
                        cache_ref=prepared_rows[0]["cache_ref"],
                        instruct=[prepared["instruct"] for prepared in prepared_rows],
                        duration=[prepared["duration"] for prepared in prepared_rows],
                        num_step=prepared_rows[0]["num_step"],
                        guidance_scale=prepared_rows[0]["guidance_scale"],
                        speed=[prepared["speed"] for prepared in prepared_rows],
                        denoise=True,
                        postprocess_output=prepared_rows[0]["postprocess_output"],
                    )
                    if len(outputs) != len(prepared_rows):
                        raise RuntimeError(
                            f"native batch returned {len(outputs)} outputs for "
                            f"{len(prepared_rows)} segments"
                        )
                    rendered = []
                    for output, prepared in zip(outputs, prepared_rows):
                        preset = prepared["effect_preset"]
                        if preset == "raw":
                            rendered.append(output)
                            continue
                        mastered = output
                        if not getattr(backend, "applies_own_mastering", False):
                            mastered = apply_mastering(mastered, sample_rate=backend.sample_rate)
                        effect_chain = get_effect_chain(preset)
                        if effect_chain:
                            mastered = apply_effects_chain(
                                mastered,
                                sample_rate=backend.sample_rate,
                                chain=effect_chain,
                            )
                        rendered.append(normalize_audio(mastered, target_dBFS=-2.0))
                    return rendered

                try:
                    outputs = await run_on_gpu_pool_guarded(
                        _render_batch,
                        what="Dub generate batch",
                        timeout=batch_timeout_s(
                            [prepared["text"] for _, prepared in batch], backend
                        ),
                    )
                except TimeoutError:
                    raise
                except Exception as error:
                    _prepare_oom_retry(error, execution_target="local")
                    logger.warning(
                        "Native dub batch failed for segments %s-%s; falling back: %s",
                        batch[0][0] + 1,
                        batch[-1][0] + 1,
                        error,
                    )
                    return
                _batched_audio.update(
                    (index, output) for (index, _), output in zip(batch, outputs)
                )

            seg_profile = seg.profile_id or None
            seg_speed = seg.speed if hasattr(seg, 'speed') and seg.speed is not None else req.speed
            seg_lang = seg.target_lang if getattr(seg, 'target_lang', None) else req.language

            seg_instruct = seg.instruct or req.instruct
            # Phase 4.2 — if the segment carries a free-form direction, parse it
            # and append the taxonomy instruct (e.g. "urgent, surprised") on top
            # of whatever instruct was already set. Also apply the director's
            # speed bias so "urgent" actually sounds a bit quicker.
            seg_direction = getattr(seg, 'direction', None)
            if seg_direction and seg_direction.strip():
                try:
                    from services.director import parse as _parse_direction
                    d = _parse_direction(seg_direction)
                    extra_instruct = d.instruct_prompt()
                    if extra_instruct:
                        seg_instruct = (
                            f"{seg_instruct}, {extra_instruct}" if seg_instruct else extra_instruct
                        )
                    bias = d.rate_bias()
                    if bias and abs(bias - 1.0) > 0.01:
                        # Speed-bias from a Direction only multiplies seg_speed
                        # in strict_slot mode, which is the legacy path that
                        # compresses audio at synthesis time to fit the slot.
                        # In concise / stretch_video modes we preserve natural
                        # rate so the user gets the "urgent" or "slow" voice
                        # the director asked for without the chipmunk side-
                        # effect that overshooting the slot would otherwise
                        # cause.
                        if (req.timing_strategy or "concise") == "strict_slot":
                            seg_speed = (seg_speed or 1.0) * bias
                except Exception as e:
                    logger.debug("direction parse skipped for %s: %s", getattr(seg, 'id', '?'), e)

            loop = asyncio.get_running_loop()
            try:
                # Fast-preview mode for interactive edits — trade ~10–20 %
                # quality for ~2× speed by dropping flow-matching steps.
                # Client sends `preview=true` when the user is iterating;
                # before final export the client should re-call without the
                # flag to restore the explicit override or shared profile.
                _num_step = 8 if req.preview else _job_num_step
                _t_tts_0 = time.perf_counter()
                seg_effect_preset = getattr(seg, "effect_preset", None) or "broadcast"

                # In concise / stretch_video / smart_fit modes we pass
                # dur_s=None so the TTS model speaks at its natural rate for
                # this text length — the whole point of the new timing
                # strategies is to never squeeze the speech to fit at
                # synthesis time. strict_slot keeps the legacy behaviour
                # where dur_s is the slot hint.
                _dur_for_tts = seg_duration if strategy == "strict_slot" else None

                # Bounded + pool-reset on hang so a wedged dub segment can't
                # starve the GPU pool and brick the backend (#730 class).
                # Budget from the shared length-scaled helper (#1190): a long
                # dub segment used to die on the flat 300s even after v0.3.22.
                from services.model_manager import generate_timeout_s
                if i in remote_audio:
                    audio_tensor, remote_sr = torchaudio.load(remote_audio[i])
                    try:
                        os.unlink(remote_audio[i])
                    except OSError:
                        pass
                    if remote_sr != backend.sample_rate:
                        import torchaudio.functional as AF
                        audio_tensor = AF.resample(audio_tensor, remote_sr, backend.sample_rate)
                else:
                    if i not in _batched_audio:
                        await _prefetch_native_batch(i)
                    if i in _batched_audio:
                        audio_tensor = _batched_audio.pop(i)
                    else:
                        audio_tensor = await run_on_gpu_pool_guarded(
                            lambda: _gen(
                                seg.text, seg_lang, seg_instruct, _dur_for_tts,
                                _num_step, req.guidance_scale, seg_speed, seg_profile, seg_effect_preset,
                            ),
                            what="Dub generate",
                            timeout=generate_timeout_s(seg.text, engine=backend),
                        )
                _t_tts += time.perf_counter() - _t_tts_0

                # Check abort immediately after GPU work completes
                if task_manager.is_cancelled(task_id):
                    yield f"data: {json.dumps({'type': 'cancelled', 'segments_processed': i + 1})}\n\n"
                    return

                current_samples = audio_tensor.shape[-1]
                # Capture the real spoken duration before assembly fitting.
                # This is the evidence used by Agent timing and
                # keeps sync badges truthful for every timing strategy.
                natural_generated_dur = current_samples / backend.sample_rate

                # Keep the complete waveform in every cache. Fitting happens
                # once during assembly; pre-trimming here destroyed words before
                # the pitch-preserving stretcher could see them.
                if current_samples == 0 or not torch.isfinite(audio_tensor).all() or not torch.any(audio_tensor.abs() > 1e-6):
                    raise ValueError("The speech engine returned empty or silent audio")

                generated_dur = natural_generated_dur
                sync_ratio = round(generated_dur / max(seg_duration, 0.01), 3)

                sync_scores.append(sync_ratio)

                # Duration-planner calibration sample: this text length spoke
                # for this long at natural rate. Keyed by stable seg id and
                # merged into the per-language job map after the loop.
                if seg.text.strip() and generated_dur > 0:
                    _natural_dur_records[str(seg_id)] = {
                        "chars": len(seg.text.strip()),
                        "dur": round(generated_dur, 4),
                    }

                # Build the fingerprint now (cheap) but defer the disk write
                # and job flush to the batch-write phase after the GPU loop.
                _seg_fp = None
                try:
                    # track_lang scopes the hash to THIS track (P1.3); the
                    # client-side recompute (/tools/incremental) sends the
                    # same code, so parity (#281 class) holds per language.
                    _seg_fp = segment_fingerprint({
                        "text": seg.text,
                        "target_lang": getattr(seg, "target_lang", None),
                        "profile_id": getattr(seg, "profile_id", None),
                        "instruct": getattr(seg, "instruct", None),
                        "speed": getattr(seg, "speed", None),
                        "direction": getattr(seg, "direction", None),
                        "effect_preset": getattr(seg, "effect_preset", None),
                    }, track_lang=lang_code, voice_match=voice_match)
                except Exception as e:
                    logger.debug("seg fingerprint skipped for %s: %s", seg_id, e)

                _pending_seg_writes.append((i, backend.sample_rate, seg_id, _seg_fp, _num_step))

                # RVC needs the WAV on disk, so write it immediately only
                # when RVC is active (uncommon path).
                if rvc_is_enabled():
                    seg_wav_path = _seg_lang_path(seg_id)
                    atomic_save_wav(seg_wav_path, audio_tensor, backend.sample_rate)
                    try:
                        await loop.run_in_executor(_gpu_pool, apply_rvc, seg_wav_path)
                        rvc_wav, rvc_sr = torchaudio.load(seg_wav_path)
                        if rvc_sr == backend.sample_rate:
                            audio_tensor = rvc_wav

                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'warning', 'segment': i, 'message': f'RVC skipped: {str(e)[:120]}'})}\n\n"

                # Watermark this FRESH TTS output exactly once, right before it
                # is persisted. The same seg_{lang}_{id}.wav is BOTH the
                # downloadable per-segment file AND the assembly input for the
                # final track, so marking it here (and nowhere else) gives the
                # downloadable WAV its mark back and the final mix inherits it —
                # no double-mark. Cached-reuse audio is already marked;
                # silence/zero slots carry no speech to mark, so neither is
                # re-watermarked.
                if i not in remote_audio or rvc_is_enabled():
                    audio_tensor = mark_synthetic(audio_tensor, backend.sample_rate,
                                                  context="dub_generate.segment")

                seg_wav_path = _seg_lang_path(seg_id)
                try:
                    # Keep the existing per-segment WAV contract for previews
                    # and partial regeneration, but do not keep the tensor in RAM.
                    atomic_save_wav(seg_wav_path, audio_tensor, backend.sample_rate)
                except Exception as e:
                    logger.warning("seg write failed for %s: %s", seg_id, e)
                    # If the durable segment write fails, still preserve a mix
                    # copy so this generation can finish.
                    all_segment_wavs.append(_store_mix_wav(seg.start, seg.end, audio_tensor, backend.sample_rate, f"mix_{seg_id}"))
                    try:
                        del audio_tensor
                    except Exception:
                        pass
                    _release_audio_tensors()
                else:
                    all_segment_wavs.append((seg.start, seg.end, seg_wav_path, backend.sample_rate))
                    try:
                        del audio_tensor
                    except Exception:
                        pass
                    _release_audio_tensors()
            except Exception as e:
                # A task-stream error bypasses the global exception handler.
                # Never publish engine exception text here: allocator errors
                # carry process tables and arbitrary failures can carry paths,
                # tokens, or source text. The shared helper enriches recognized
                # classes using VoiceStudio-owned constants only.
                from core.public_errors import stream_generation_failure

                error_detail = stream_generation_failure(e)["detail"]
                logger.exception("Dub generation failed for segment %s", seg_id)
                yield f"data: {json.dumps({'type': 'error', 'segment': i, 'segment_id': seg_id, 'error': error_detail})}\n\n"
                return

        _t_loop_end = time.perf_counter()

        yield f"data: {json.dumps({'type': 'assembling'})}\n\n"

        # ── Batch metadata phase ──────────────────────────────────────
        # Per-segment WAVs were written during the loop to keep RAM bounded.
        # Flush only lightweight fingerprints/quality metadata here.
        _t_diskw_0 = time.perf_counter()
        # P1.3 — fingerprints live per language so each track's staleness is
        # judged against ITS OWN last generate. The flat job["seg_hashes"] is
        # kept as a mirror of the CURRENT track's map: every existing consumer
        # (the `done` event, dub-history restore, older frontends) already
        # treats it as "the hashes of the language generated last", which is
        # exactly what it now provably contains.
        hashes = _seg_hashes_by_lang(job).setdefault(lang_code, {})
        quality_map = job.setdefault("seg_num_step", {})
        for (_si, _sr, _sid, _fp, _nstep) in _pending_seg_writes:
            if _fp is not None:
                hashes[_sid] = _fp
            quality_map[_sid] = _nstep
        job["seg_hashes"] = dict(hashes)
        # Duration-planner calibration: per-language (chars, natural dur)
        # records. update() (not replace) so partial regens keep accumulating
        # samples from earlier runs of this track.
        if _natural_dur_records:
            job.setdefault("seg_natural_durs_by_lang", {}).setdefault(
                lang_code, {},
            ).update(_natural_dur_records)
        # Single job flush instead of one per 8 segments.
        _save_job(job_id, job)
        _t_diskw = time.perf_counter() - _t_diskw_0

        sr = backend.sample_rate
        slot_fit = (req.slot_fit or "time_stretch").lower()
        overflow_budget_s = max(0.0, float(req.overflow_budget_s or 0.0))

        # Per-segment fit_status emitted alongside the legacy sync_scores so
        # the UI can replace the lying "Sync: 100%" badge with a truthful
        # "Fits / Overflows +0.4s / Slipped 0.2s / Video stretched 1.18×".
        fit_status: list[dict] = []

        # Mode B layout: when stretch_video is on, compute a new timeline
        # where each segment's slot equals the natural-rate dub audio length;
        # gaps stay at 1.0×. Persisted on the job so dub_export.py can build
        # the matching per-segment setpts filter chain on the source video.
        new_layout: list[tuple[float, float]] = []
        video_stretch_plan: list[dict] = []
        fit_plan = None  # smart_fit only — services.fit_planner.FitPlan
        orig_total_dur = float(job.get("duration") or 0.0)

        if strategy == "stretch_video":
            cursor = 0.0
            for i, (orig_start, orig_end, wav_path, _) in enumerate(all_segment_wavs):
                wl_i = _entry_num_samples((orig_start, orig_end, wav_path, sr))
                natural_dur = (wl_i / sr) if wl_i > 0 else max(0.0, orig_end - orig_start)
                if i == 0:
                    # Preserve the pre-roll (silence before the first seg).
                    cursor = orig_start
                else:
                    prev_orig_end = all_segment_wavs[i - 1][1]
                    gap = max(0.0, orig_start - prev_orig_end)
                    cursor += gap
                new_start = cursor
                new_end = cursor + natural_dur
                new_layout.append((new_start, new_end))
                orig_dur = max(1e-3, orig_end - orig_start)
                video_stretch_plan.append({
                    "orig_start": round(orig_start, 4),
                    "orig_end": round(orig_end, 4),
                    "new_start": round(new_start, 4),
                    "new_end": round(new_end, 4),
                    "stretch_ratio": round(natural_dur / orig_dur, 4),
                })
                cursor = new_end
            # Preserve the trailing tail (anything after the last seg in the
            # original video) at 1.0× rate.
            if all_segment_wavs:
                last_orig_end = all_segment_wavs[-1][1]
                cursor += max(0.0, orig_total_dur - last_orig_end)
            new_total_dur = max(cursor, orig_total_dur)
            total_samples = int(new_total_dur * sr)
        elif strategy == "smart_fit":
            # Smart Fit: plan the audio-rate / video-ratio split per segment
            # from the natural-rate WAV lengths. Pure planning — the mix
            # loop below applies the audio side; the video side ships as
            # fit_plans[lang] for the (Phase B) export pipeline.
            _fo = req.fit_options
            _fit_defaults = FitParams()
            fit_params = FitParams(
                max_audio_only_rate=float(getattr(_fo, "max_audio_only_rate", None) or _fit_defaults.max_audio_only_rate),
                audio_rate_cap=float(getattr(_fo, "audio_rate_cap", None) or _fit_defaults.audio_rate_cap),
                video_slow_cap=float(getattr(_fo, "video_slow_cap", None) or _fit_defaults.video_slow_cap),
                gap_guard_s=float(_fo.gap_guard_s) if _fo is not None and _fo.gap_guard_s is not None else _fit_defaults.gap_guard_s,
                allow_video_retime=bool(_fo.allow_video_retime) if _fo is not None and _fo.allow_video_retime is not None else _fit_defaults.allow_video_retime,
                min_audio_rate=_underrun_min_rate(),
            )
            _seg_order = job.get("seg_order") or []
            fit_plan = plan_fit(
                [
                    {
                        "id": _seg_order[i] if i < len(_seg_order) else f"seg_{i}",
                        "start": s,
                        "end": e,
                    }
                    for i, (s, e, _path, _) in enumerate(all_segment_wavs)
                ],
                [_entry_num_samples(entry) / sr for entry in all_segment_wavs],
                orig_total_dur,
                fit_params,
            )
            total_samples = int(fit_plan.total_duration * sr)
        else:
            total_samples = int(orig_total_dur * sr)

        # smart_fit: cue times for the fitted timeline, computed from the
        # ACTUAL stretched/trimmed sample positions in the mix loop below —
        # not from the plan — so subtitles land exactly on the audio.
        fitted_cues: list[dict] = []

        track_path = os.path.join(DUB_DIR, job_id, f"dubbed_{lang_code}.wav")
        os.makedirs(os.path.dirname(track_path), exist_ok=True)

        import gc
        import tempfile
        import numpy as np

        mix_samples = max(total_samples, 1)
        fd, mix_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(track_path)}.mix.",
            suffix=".f32",
            dir=os.path.dirname(track_path),
        )
        os.close(fd)
        try:
            with open(mix_path, "r+b") as mix_file:
                mix_file.truncate(mix_samples * 4)
            mix_audio = np.memmap(mix_path, dtype=np.float32, mode="r+", shape=(mix_samples,))

            for i, (start, end, wav_path, _) in enumerate(all_segment_wavs):
                seg_ref = req.segments[i] if i < len(req.segments) else None
                seg_gain = getattr(seg_ref, "gain", None) if seg_ref is not None else None
                seg_gain = seg_gain if seg_gain is not None else 1.0
                seg_gain = max(0.0, min(2.0, seg_gain))
                try:
                    wav = _load_entry_wav((start, end, wav_path, sr), sr)
                except Exception:
                    logger.exception("Dub assembly could not read segment %d", i)
                    yield f"data: {json.dumps({'type': 'error', 'segment': i, 'error_code': 'dub_speech_missing', 'error': 'A speech segment could not be read. Regenerate it before exporting.'})}\n\n"
                    return
                from services.audio_dsp import trim_speech_padding
                if seg_ref is not None and seg_ref.text.strip():
                    if wav.numel() == 0 or not torch.isfinite(wav).all() or not torch.any(wav.abs() > 1e-6):
                        yield f"data: {json.dumps({'type': 'error', 'segment': i, 'error_code': 'dub_speech_missing', 'error': 'A speech segment is empty or silent. Regenerate it before exporting.'})}\n\n"
                        return
                    wav = trim_speech_padding(wav, sr)
                adjusted = wav * seg_gain
                if adjusted.ndim == 2 and adjusted.shape[0] > 1:
                    adjusted = adjusted.mean(dim=0, keepdim=True)
                wl = adjusted.shape[-1]
                natural_dur = wl / sr if wl > 0 else 0.0
                orig_dur = max(0.0, end - start)

                if strategy == "stretch_video":
                    # Mode B: audio at natural rate, placed on the stretched
                    # timeline. No trim, no atempo. dub_export handles the video.
                    new_start, _new_end = new_layout[i]
                    place_at = new_start
                    fit_status.append({
                        "status": "video_stretched",
                        "stretch_ratio": round(natural_dur / max(orig_dur, 1e-3), 3),
                    })

                elif strategy == "smart_fit":
                    # Smart Fit: apply the planner's audio_rate via the same
                    # pitch-preserving atempo pipe strict_slot uses, place the
                    # result at the planned new_start, and hard-trim whatever
                    # the caps couldn't absorb. The video side (video_ratio per
                    # chunk) is persisted below for the export pipeline.
                    sf = fit_plan.segments[i]
                    place_at = sf.new_start
                    # Both directions: >1 compresses an overrun, <1 slows an
                    # underrun toward the slot (the "hole" fix — a dub that
                    # finishes early leaves the mouth moving over near-silence).
                    if abs(sf.audio_rate - 1.0) > 1e-6 and wl > 0:
                        target = max(1, int(round(wl / sf.audio_rate)))
                        try:
                            adjusted = await _pitch_preserving_stretch(
                                adjusted, target, sr,
                            )
                        except Exception as e:
                            logger.warning(
                                "atempo stretch failed for seg %d (%.2f×), "
                                "falling back to linear interp: %s",
                                i, sf.audio_rate, e,
                            )
                            adjusted = torch.nn.functional.interpolate(
                                adjusted.unsqueeze(0),
                                size=target,
                                mode='linear',
                                align_corners=False,
                            ).squeeze(0)
                        wl = adjusted.shape[-1]
                    # Never publish a complete track with speech discarded by
                    # the fit caps. The user can shorten text or relax the caps.
                    new_slot_samples = int(max(0.0, sf.new_end - sf.new_start) * sr)
                    if new_slot_samples > 0 and wl > new_slot_samples + int(sr * 0.02):
                        yield f"data: {json.dumps({'type': 'error', 'segment': i, 'error_code': 'dub_timing_overflow', 'error': 'Speech exceeds the fitting limits. Shorten the translation or choose Strict Slot or Stretch Video before exporting.'})}\n\n"
                        return
                    # Truthful per-segment verdict for the UI badge.
                    entry = {"status": sf.status}
                    if abs(sf.audio_rate - 1.0) > 1e-6:
                        entry["audio_rate"] = round(sf.audio_rate, 3)
                    if sf.video_ratio > 1.0 + 1e-6:
                        entry["video_ratio"] = round(sf.video_ratio, 3)
                    if sf.overflow_s > 0:
                        entry["overflow_s"] = round(sf.overflow_s, 3)
                    fit_status.append(entry)
                    # Cue times from the ACTUAL stretched sample positions.
                    fitted_cues.append({
                        "id": sf.seg_id,
                        "start": round(place_at, 4),
                        "end": round(place_at + wl / sr, 4),
                    })

                elif strategy == "concise":
                    # Mode A: never compress. Allow the audio to extend into the
                    # silent gap before the next seg (existing heuristic) plus
                    # any extra `overflow_budget_s`. Beyond that, require a
                    # timing/text adjustment instead of discarding speech.
                    place_at = start
                    effective_end = end
                    if i + 1 < len(all_segment_wavs):
                        next_start = all_segment_wavs[i + 1][0]
                        gap = next_start - end
                        if gap > GAP_OVERFLOW_BUFFER_S:
                            effective_end = end + min(
                                gap - GAP_OVERFLOW_BUFFER_S, GAP_OVERFLOW_MAX_S,
                            )
                    effective_end += overflow_budget_s
                    slot_samples_eff = int(max(0.0, (effective_end - start)) * sr)
                    if slot_samples_eff > 0 and wl > slot_samples_eff:
                        overflow_s = (wl - slot_samples_eff) / sr
                        yield f"data: {json.dumps({'type': 'error', 'segment': i, 'segment_id': job['seg_order'][i], 'error_code': 'dub_timing_overflow', 'error': 'Speech exceeds its time slot. Shorten the translation or choose Strict Slot or Stretch Video before exporting.', 'overflow_s': round(overflow_s, 3)})}\n\n"
                        return
                    else:
                        fit_status.append({"status": "fits"})

                else:
                    # Strict Slot fits the complete speech to the original
                    # slot. Explicit legacy trim/off choices remain available.
                    place_at = start
                    effective_end = end
                    slowed_rate = None
                    slot_samples = int(max(0.0, (effective_end - start)) * sr)
                    if slot_fit != "off" and slot_samples > 0 and wl > slot_samples:
                        if slot_fit == "time_stretch":
                            ratio = wl / slot_samples
                            try:
                                adjusted = await _pitch_preserving_stretch(
                                    adjusted, slot_samples, sr,
                                )
                            except Exception as e:
                                logger.warning(
                                    "atempo stretch failed for seg %d (%.2f×), "
                                    "falling back to linear interp: %s",
                                    i, ratio, e,
                                )
                                adjusted = torch.nn.functional.interpolate(
                                    adjusted.unsqueeze(0),
                                    size=slot_samples,
                                    mode='linear',
                                    align_corners=False,
                                ).squeeze(0)
                        else:  # "trim"
                            adjusted = adjusted[..., :slot_samples]
                        wl = adjusted.shape[-1]
                    elif (
                        slot_fit == "time_stretch"
                        and slot_samples > 0
                        and wl > 0
                        and wl < slot_samples
                    ):
                        # Underrun fill (mirror of the compression above): the
                        # dub finished early, leaving the on-screen mouth moving
                        # over the thin under-speech bed residue — perceived as
                        # dead air. Slow toward the slot, never below the floor.
                        rate = wl / slot_samples
                        target = slot_samples
                        try:
                            adjusted = await _pitch_preserving_stretch(
                                adjusted, target, sr,
                            )
                            slowed_rate = rate
                        except Exception as e:
                            logger.warning(
                                "underrun fill failed for seg %d (%.2f×), "
                                "keeping natural rate: %s", i, rate, e,
                            )
                        wl = adjusted.shape[-1]
                    # Truthful verdict: a slowed segment says so (and by how
                    # much) instead of hiding behind "fits" — the same honesty
                    # contract the smart_fit branch keeps.
                    if slowed_rate is not None:
                        fit_status.append({
                            "status": "audio_slowed",
                            "audio_rate": round(slowed_rate, 3),
                        })
                    else:
                        fit_status.append({
                            "status": "fits",
                            "compression_applied": (slot_fit == "time_stretch"
                                                    and wl != int(natural_dur * sr)),
                        })

                # Common: short fades to avoid pops, then mix into disk-backed audio.
                fade_ms = 15
                fade_samples = int((fade_ms / 1000.0) * sr)
                if wl > fade_samples * 2:
                    ramp_up = torch.linspace(0, 1, fade_samples, device=adjusted.device)
                    ramp_down = torch.linspace(1, 0, fade_samples, device=adjusted.device)
                    adjusted[0, :fade_samples] *= ramp_up
                    adjusted[0, -fade_samples:] *= ramp_down

                s = int(place_at * sr)
                if s < 0:
                    adjusted = adjusted[..., -s:]
                    wl = adjusted.shape[-1]
                    s = 0
                e = min(s + wl, total_samples)
                if s < total_samples and e > s:
                    mix_len = e - s
                    seg_np = (
                        adjusted[:, :mix_len]
                        .detach()
                        .cpu()
                        .to(torch.float32)
                        .clamp(-1.0, 1.0)
                        .squeeze(0)
                        .numpy()
                    )
                    mix_audio[s:e] += seg_np
                try:
                    del wav, adjusted
                except Exception:
                    pass
                _release_audio_tensors()

            _t_save_0 = time.perf_counter()
            mix_audio.flush()
            _write_memmap_wav_atomic(track_path, mix_audio[:mix_samples], sr)
            _t_save = time.perf_counter() - _t_save_0
            _t_mix = _t_save_0 - _t_loop_end
        finally:
            try:
                mix_audio.flush()
                mix_mmap = getattr(mix_audio, "_mmap", None)
                if mix_mmap is not None:
                    mix_mmap.close()
            except Exception:
                pass
            try:
                del mix_audio
            except Exception:
                pass
            gc.collect()
            try:
                os.unlink(mix_path)
            except OSError:
                pass
            # The final track is written; the mix_<id> scratch WAVs (silence /
            # cached-fail / error slots) have served their only purpose as
            # assembly inputs and would otherwise leak into the job dir.
            for _mp in _mix_temp_paths:
                try:
                    os.unlink(_mp)
                except OSError:
                    pass
        # Per-track metadata. For stretch_video, the dub wav is at the new
        # (longer) timeline, so we record its actual duration here too — the
        # mux step needs this to know whether to use the original video as-is
        # or stretch it per the plan.
        track_dur = total_samples / sr if total_samples > 0 else 0.0
        job["dubbed_tracks"][lang_code] = {
            "path": track_path,
            "language": req.language,
            "language_code": lang_code,
            "duration": round(track_dur, 4),
            "timing_strategy": strategy,
            "source_segments": [{"start": seg.start, "end": seg.end} for seg in req.segments],
        }

        # Persist the timing strategy + (for Mode B) the per-segment stretch
        # plan so dub_export can build the matching video pipeline at mux
        # time. Plans are keyed by language code because each language gets
        # its own dub track with its own natural-rate audio layout.
        job["language"] = req.language
        job["language_code"] = lang_code
        job["timing_strategy"] = strategy
        # Keep job segments in lock-step with what was just rendered so
        # subtitle export / burn-in use the translated text (#309).
        _sync_job_segments(job, req)
        if strategy == "stretch_video":
            stretch_plans = job.setdefault("video_stretch_plans", {})
            stretch_plans[lang_code] = {
                "plan": video_stretch_plan,
                "total_duration": round(track_dur, 4),
                "orig_duration": round(orig_total_dur, 4),
            }
        elif strategy == "smart_fit" and fit_plan is not None:
            # video_stretch_plans stays untouched — smart_fit persists its
            # own keyspace so a job can carry both without clobbering.
            _fit_params_payload = {
                "timing_strategy": strategy,
                "max_audio_only_rate": fit_params.max_audio_only_rate,
                "audio_rate_cap": fit_params.audio_rate_cap,
                "video_slow_cap": fit_params.video_slow_cap,
                "gap_guard_s": fit_params.gap_guard_s,
                "allow_video_retime": fit_params.allow_video_retime,
            }
            fit_fp = fit_fingerprint(_fit_params_payload)
            job.setdefault("fit_plans", {})[lang_code] = {
                # Same dict shape _build_video_stretch_filter_graph consumes.
                "plan": fit_plan.video_plan,
                # Cue times from actual stretched sample positions — for
                # subtitle export on the fitted timeline.
                "fitted_segments": fitted_cues,
                "total_duration": round(track_dur, 4),
                "orig_duration": round(orig_total_dur, 4),
                "params": _fit_params_payload,
                "fit_fp": fit_fp,
            }
            job["dubbed_tracks"][lang_code]["fit_fp"] = fit_fp
        # Every new cache preserves natural speech. Old slotted caches must
        # be regenerated once because their missing tails cannot be recovered.
        _kind = "natural"
        job.setdefault("seg_wav_kind_by_lang", {})[lang_code] = _kind
        job["seg_wav_kind"] = _kind
        _save_job(job_id, job)

        _t_total = time.perf_counter() - _t_start
        logger.info(
            "bench[generate] total=%.2fs tts=%.2fs cache=%.2fs diskw=%.2fs mix=%.2fs save=%.2fs segs=%d%s",
            _t_total, _t_tts, _t_cache, _t_diskw, _t_mix, _t_save, total,
            f" regen={len(regen_only)}" if regen_only is not None else "",
        )

        yield f"data: {json.dumps({'type': 'done', 'segments_processed': total, 'language_code': lang_code, 'tracks': list(job['dubbed_tracks'].keys()), 'sync_scores': sync_scores, 'fit_status': fit_status, 'timing_strategy': strategy, 'seg_hashes': job.get('seg_hashes', {}), 'seg_num_step': job.get('seg_num_step', {})})}\n\n"

    task_id = f"dub_{job_id}_{int(time.time())}"
    await task_manager.add_task(task_id, "dub_generate", _stream, task_id)
    return {"task_id": task_id}


# ── Real-time segment preview ──────────────────────────────────────────
# Stream TTS for a single segment without the full pipeline overhead.
# The frontend calls this when the user edits a segment's text/instruct
# and wants to hear the result immediately.

from pydantic import BaseModel
from typing import Optional
from fastapi.responses import Response
import io


class SegmentPreviewRequest(BaseModel):
    # Optional, and only used to label diagnostics: a preview is a "render this
    # text" call, so it has never needed segment identity. Supplying it makes a
    # missing-clone warning name the line the user was editing instead of a
    # bare "preview" (greptile).
    segment_id: Optional[str] = None
    text: str
    language: str = "Auto"
    instruct: Optional[str] = None
    profile_id: Optional[str] = None
    speed: float = 1.0
    duration: Optional[float] = None


@router.post("/dub/preview-segment/{job_id}")
async def preview_segment(job_id: str, req: SegmentPreviewRequest):
    """Generate TTS for a single segment and return WAV bytes.

    This is the fast path for interactive editing — 8 diffusion steps,
    no disk write, no mix. The preview IS synthetic audio leaving the app,
    so it carries the same invisible provenance mark as every other
    producer (#1169 — "no watermark" here used to be an exemption).
    """
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # See the /dub/generate/{job_id} resolution above (issue #312 class) —
    # a segment preview resolves ref_audio from the same auto-clone /
    # voice-profile sources, so it needs the same cloning-capable gate.
    try:
        backend = await resolve_generation_backend(require_cloning=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def _gen():
        ref_audio = None
        ref_text = None

        # Resolve profile / auto-clone
        pid = req.profile_id
        if pid and pid.startswith("auto:"):
            key = pid[len("auto:"):]
            info = None
            if (
                req.segment_id is not None
                and _speaker_key_for_segment(job, req.segment_id) == key
            ):
                info = (job.get("segment_clones") or {}).get(str(req.segment_id))
            if info is None:
                info = resolve_consistent_ref(job, key)
            if info:
                ref_audio = info.get("ref_audio")
                ref_text = info.get("ref_text")
            pid = None

        instruct_str = req.instruct
        if pid:
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM voice_profiles WHERE id=?", (pid,)
                ).fetchone()
            if row:
                if row["is_locked"] and row["locked_audio_path"]:
                    ref_audio = os.path.join(VOICES_DIR, row["locked_audio_path"])
                    ref_text = row["ref_text"]
                elif row["ref_audio_path"]:
                    ref_audio = os.path.join(VOICES_DIR, row["ref_audio_path"])
                    ref_text = row["ref_text"]
                if not instruct_str and row["instruct"]:
                    instruct_str = row["instruct"]

        ref_audio = warn_if_ref_missing(
            ref_audio, job_id=job_id,
            seg_id=req.segment_id or "preview", where="dub preview",
        )
        lang = req.language if req.language != "Auto" else None
        # Same normalization as the full dub render above, so a preview
        # sounds exactly like the final segment. Pref-gated, never raises.
        from services.text_normalization import normalize_for_tts
        audio_out = backend.generate(
            text=normalize_for_tts(req.text, lang),
            language=lang,
            ref_audio=ref_audio,
            ref_text=ref_text,
            instruct=instruct_str if instruct_str else None,
            duration=req.duration,
            num_step=8,  # fast preview
            guidance_scale=2.0,
            speed=req.speed,
            denoise=True,
            postprocess_output=True,
        )
        if not getattr(backend, "applies_own_mastering", False):
            audio_out = apply_mastering(audio_out, sample_rate=backend.sample_rate)
        audio_out = normalize_audio(audio_out, target_dBFS=-2.0)
        # Invisible provenance mark before the WAV encode (#1169); pref-gated,
        # never raises, and short-preview embedding runs in this same
        # GPU-pool job.
        return mark_synthetic(audio_out, backend.sample_rate,
                              context="dub_generate.preview_segment")

    # Bounded + pool-reset on hang so a wedged preview generate can't starve the
    # GPU pool and brick the backend (#730 class). Length-scaled budget (#1190).
    from services.model_manager import generate_timeout_s
    audio_tensor = await run_on_gpu_pool_guarded(
        _gen, what="Dub preview generate",
        timeout=generate_timeout_s(req.text, engine=backend),
    )

    sr = backend.sample_rate
    buf = io.BytesIO()
    _safe_torchaudio_save(buf, audio_tensor, sr, format="wav")
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="audio/wav",
        headers={
            "X-Audio-Duration": str(round(audio_tensor.shape[-1] / sr, 2)),
        },
    )
