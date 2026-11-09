"""Speech-to-speech voice conversion for Convert's "Keep my delivery" (#3421).

The default Convert transcribes the source clip and re-speaks the text in the
target voice, so the speaker's pauses, emphasis, melody and timing are lost.
This path hands the recording itself to an engine that declares
``supports_voice_conversion`` (CosyVoice 3's ``inference_vc``): the source's
speech tokens are kept and re-rendered with the target voice's timbre, so the
take follows the source timing exactly. No transcript is made.

Engines cap one conversion at ``max_vc_source_seconds`` (CosyVoice's speech
tokenizer refuses clips over 30 s), so a longer source is split at its
quietest moments and the converted pieces are joined in order. The pure-NumPy
split keeps behaviour identical on macOS, Windows and Linux.
"""
from __future__ import annotations

import functools
import logging
import os
import shutil
import tempfile
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger("omnivoice.voice_conversion")

#: Sample rate the source and reference are decoded to before conversion.
#: CosyVoice resamples to 16 kHz for tokens and needs at least that.
DECODE_SR = 24_000
#: Default per-conversion cap when an engine does not declare one.
DEFAULT_MAX_SOURCE_S = 30.0
#: Margin below the engine cap, so resampling rounding never crosses it.
_CAP_MARGIN_S = 2.0
#: A split is placed at the quietest frame in this last stretch of a chunk.
_SPLIT_SEARCH_S = 6.0
_FRAME_S = 0.02
#: Longest source recording one Keep my delivery request converts. Longer
#: uploads would decode to gigabytes of samples and queue hundreds of jobs.
MAX_SOURCE_S = 600.0
#: The reference clip is trimmed to this length (the tokenizer cap minus margin).
MAX_REF_S = 28.0
#: Speech runs at roughly this many characters a second. Used to give each
#: conversion the same wall-clock budget a synthesis of that much speech gets.
CHARS_PER_SECOND = 15


async def resolve_voice_conversion_backend():
    """The engine Convert's "Keep my delivery" mode runs on (#3421).

    The active engine when it converts speech itself; otherwise the first
    installed engine that does, so the user does not have to switch their
    TTS engine for one conversion. Raises ``LookupError`` with
    :data:`services.tts_backend.VOICE_CONVERSION_MISSING` when none is installed, and
    ``ValueError`` when the one installed cannot run on this host.
    """
    from core.device_caps import detect_host_caps
    from services import tts_backend as tb
    from services.engine_routing import runtime_compute_profile_async

    active = tb.active_backend_id()
    candidates = tb.voice_conversion_engine_ids()
    if active in candidates:
        candidates.remove(active)
        candidates.insert(0, active)
    blocked: list[str] = []
    for engine_id in candidates:
        cls = tb.get_backend_class(engine_id)
        try:
            tb.ensure_engine_licence(cls)
            ok, _msg = cls.is_available()
        except Exception:  # noqa: BLE001 — an unusable engine is skipped
            ok = False
        if not ok:
            continue
        routing = await runtime_compute_profile_async(cls, detect_host_caps())
        if routing["routing_status"] == "unavailable":
            blocked.append(str(routing["routing_reason"]))
            continue
        return tb.get_engine_instance_for(engine_id)
    if blocked:
        raise ValueError(blocked[0])
    raise LookupError(tb.VOICE_CONVERSION_MISSING)


def split_audio(audio: np.ndarray, sr: int, max_s: float) -> list[tuple[int, int]]:
    """Split mono ``audio`` into pieces no longer than ``max_s`` seconds.

    Each cut lands on the quietest 20 ms frame within the final
    ``_SPLIT_SEARCH_S`` of the piece, so words are not cut in half.
    """
    n = int(audio.shape[0])
    limit = max(1, int(max_s * sr))
    if n <= limit:
        return [(0, n)] if n > 0 else []
    frame = max(1, int(_FRAME_S * sr))
    search = min(limit - frame, max(frame, int(_SPLIT_SEARCH_S * sr)))
    bounds: list[tuple[int, int]] = []
    start = 0
    while n - start > limit:
        lo = start + limit - search
        hi = start + limit
        window = audio[lo:hi].astype(np.float64, copy=False)
        usable = (window.shape[0] // frame) * frame
        if usable <= 0:
            cut = hi
        else:
            energy = (window[:usable].reshape(-1, frame) ** 2).mean(axis=1)
            # Latest of the quietest frames: keeps pieces as long as allowed.
            quietest = np.flatnonzero(energy <= energy.min() * 1.0001 + 1e-12)[-1]
            cut = lo + int(quietest) * frame + frame // 2
        cut = max(start + 1, min(cut, hi))
        bounds.append((start, cut))
        start = cut
    bounds.append((start, n))
    return bounds


async def decode_mono(path: str, sr: int, *, max_s: Optional[float] = None) -> np.ndarray:
    """Decode any audio ffmpeg reads into mono float32 at ``sr``."""
    from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg

    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path]
    if max_s is not None:
        cmd += ["-t", f"{max_s:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    rc, stdout, stderr = await run_ffmpeg(cmd, timeout=300.0)
    if rc != 0 or not stdout:
        detail = (stderr or b"").decode(errors="replace").strip()[:200]
        raise ValueError(
            "This recording could not be read. Use a WAV, MP3, M4A, FLAC or "
            f"OGG file with audible speech.{(' ' + detail) if detail else ''}"
        )
    return np.frombuffer(stdout, dtype="<f4").astype(np.float32, copy=True)


def _write_wav(path: str, audio: np.ndarray, sr: int) -> None:
    from services.audio_io import _safe_soundfile_write

    _safe_soundfile_write(path, np.clip(audio, -1.0, 1.0).astype(np.float32), sr, subtype="FLOAT")


async def convert_recording(
    backend,
    source_path: str,
    ref_audio_path: str,
    *,
    seed: Optional[int],
    run_job: Callable[[Callable[[], object], float], Awaitable[object]],
):
    """Convert ``source_path`` into ``ref_audio_path``'s voice with ``backend``.

    ``run_job(fn, seconds)`` dispatches one blocking conversion of
    ``seconds`` of source audio on the GPU pool and returns its result. Returns a ``(1, n)`` tensor at
    ``backend.sample_rate``.
    """
    import torch

    source = await decode_mono(source_path, DECODE_SR, max_s=MAX_SOURCE_S + 1.0)
    if source.size > int(MAX_SOURCE_S * DECODE_SR):
        raise ValueError(
            f"The source clip is longer than {int(MAX_SOURCE_S // 60)} minutes. "
            "Trim it, or convert it in parts."
        )
    if source.size == 0 or float(np.max(np.abs(source))) < 1e-4:
        raise ValueError(
            "The source clip is silent, so there is nothing to convert. Record "
            "or drop a clip with clear, audible speech and try again."
        )
    reference = await decode_mono(ref_audio_path, DECODE_SR, max_s=MAX_REF_S)
    if reference.size == 0:
        raise ValueError("The target voice's reference audio could not be read.")

    cap = float(getattr(backend, "max_vc_source_seconds", None) or DEFAULT_MAX_SOURCE_S)
    pieces = split_audio(source, DECODE_SR, max(1.0, cap - _CAP_MARGIN_S))
    workdir = tempfile.mkdtemp(prefix="vs-convert-")
    try:
        ref_wav = os.path.join(workdir, "reference.wav")
        _write_wav(ref_wav, reference, DECODE_SR)
        outputs = []
        for index, (a, b) in enumerate(pieces):
            piece_wav = os.path.join(workdir, f"source-{index:03d}.wav")
            _write_wav(piece_wav, source[a:b], DECODE_SR)
            seconds = (b - a) / DECODE_SR
            job = functools.partial(backend.convert_voice, piece_wav, ref_audio=ref_wav, seed=seed)
            wav = await run_job(job, seconds)
            outputs.append(torch.as_tensor(wav, dtype=torch.float32).reshape(1, -1).cpu())
        return torch.cat(outputs, dim=-1)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
