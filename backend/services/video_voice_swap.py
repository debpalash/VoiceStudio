"""One-click voice swap for a video file (#3750).

Convert's "Keep my delivery" (``services.voice_conversion``) already changes the
voice in a recording and keeps its timing. This chains it with the dubbing
pipeline's pieces, so a video goes in and a video comes out:

1. extract the soundtrack (full-quality stereo, as Dubbing separates it),
2. split vocals from background with Demucs when it is installed,
3. convert the vocals (or, without Demucs, the whole soundtrack) in the
   engine's <= 30 s pieces,
4. mix the background back at its original level,
5. remux with the original picture stream copied, never re-encoded.

Nothing here fails the request for a recoverable reason. No separation model
converts the whole track and says so; a failed remux retries with a compatible
audio re-encode into MP4; if that fails too the converted audio is returned
instead. The uploaded file is only ever read.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("omnivoice.video_voice_swap")

#: Containers the swap remuxes. ``.webm`` is not listed: Convert has always
#: taken WebM as audio (mic recordings), and still does.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv")
#: Video containers recognised only to refuse them with a useful message.
UNSUPPORTED_VIDEO_SUFFIXES = (
    ".avi", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".ogv",
)
SUPPORTED_VIDEO_NAMES = "MP4, MOV, M4V and MKV"

#: Result notes the UI turns into a localized sentence.
NOTE_NO_SEPARATION = "no_separation"
NOTE_AUDIO_ONLY = "audio_only"
NOTE_NO_BACKGROUND = "no_background"

_EXTRACT_SR = 44_100


def video_kind(filename: "str | None") -> "str | None":
    """``"video"`` for a supported container, ``"unsupported"`` for a known
    video container the swap cannot remux, ``None`` for everything else."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in VIDEO_SUFFIXES:
        return "video"
    if ext in UNSUPPORTED_VIDEO_SUFFIXES:
        return "unsupported"
    return None


def unsupported_video_message(filename: "str | None") -> str:
    ext = os.path.splitext(filename or "")[1].lower() or "this"
    return (
        f"Voice swap cannot edit {ext} video. Use a {SUPPORTED_VIDEO_NAMES} file, "
        "or convert only its audio track."
    )


def separation_available() -> bool:
    """Whether the Demucs separation model package is installed."""
    return importlib.util.find_spec("demucs") is not None


@dataclass
class SwapResult:
    #: Final video, or None when the remux fell back to audio only.
    video_path: Optional[str]
    #: Converted soundtrack (background mixed in when it could be separated).
    audio_path: str
    notes: list = field(default_factory=list)


async def _ffmpeg(cmd: list, timeout: float = 1800.0) -> "tuple[int, bytes]":
    from services.ffmpeg_utils import run_ffmpeg

    rc, _out, err = await run_ffmpeg(cmd, timeout=timeout)
    return rc, err or b""


async def extract_track(video: str, out_wav: str) -> None:
    """Decode the video's soundtrack to full-quality stereo WAV."""
    from services.ffmpeg_utils import find_ffmpeg
    from services.voice_conversion import MAX_SOURCE_S

    # Bounded like convert_recording's own decode: an hour-long video must not
    # become gigabytes of WAV before the length check below rejects it.
    rc, err = await _ffmpeg([
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", video, "-t", f"{MAX_SOURCE_S + 1.0:.3f}", "-vn", "-acodec", "pcm_s16le", "-ar", str(_EXTRACT_SR), "-ac", "2", out_wav,
    ])
    if rc != 0 or not os.path.isfile(out_wav):
        from services.ffmpeg_utils import raise_for_audio_extract_failure

        raise_for_audio_extract_failure(err, video)
        raise ValueError(
            "The soundtrack of this video could not be read. Use a finished, playable "
            f"{SUPPORTED_VIDEO_NAMES} file."
        )


async def separate_vocals(track: str, workdir: str) -> "tuple[str, str] | None":
    """Demucs two-stem split of ``track`` into ``(vocals, background)`` WAVs.

    ``None`` when Demucs is missing or fails; the caller converts the whole
    track instead. Same command Dubbing's ingest runs.
    """
    if not separation_available():
        return None
    from services.model_manager import get_best_device

    try:
        rc, err = await _ffmpeg(
            [sys.executable, "-m", "demucs.separate", "--two-stems", "vocals", "-n", "htdemucs",
             "-d", get_best_device(), track, "-o", workdir],
            timeout=1800.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Demucs timed out on a voice-swap soundtrack; converting the whole track")
        return None
    stem_dir = os.path.join(workdir, "htdemucs", os.path.splitext(os.path.basename(track))[0])
    vocals = os.path.join(stem_dir, "vocals.wav")
    background = os.path.join(stem_dir, "no_vocals.wav")
    if rc != 0 or not (os.path.isfile(vocals) and os.path.isfile(background)):
        logger.warning("Demucs failed on a voice-swap soundtrack (rc=%s); converting the whole track", rc)
        return None
    return vocals, background


async def mix_background(voice_wav: str, background_wav: str, out_wav: str) -> bool:
    """Lay the converted voice over the background at their original levels."""
    from services.ffmpeg_utils import bed_mix_filter, find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    try:
        rc, _err = await _ffmpeg([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", background_wav, "-i", voice_wav,
            "-filter_complex", bed_mix_filter("0:a", "1:a", out="aout", bed_gain=1.0),
            "-map", "[aout]", "-acodec", "pcm_s16le", out_wav,
        ])
    except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
        # The converted voice is already paid for: fall back, never raise.
        logger.warning("Voice-swap background mix failed (%s); using the voice alone", type(exc).__name__)
        return False
    return rc == 0 and os.path.isfile(out_wav)


async def mux_video(video: str, audio: str, out_path: str, *, compat: bool = False) -> bool:
    """Copy ``video``'s picture stream untouched and attach ``audio`` as AAC.

    ``compat`` re-encodes the audio to plain 48 kHz stereo AAC and drops the
    source metadata, the combination MP4 players accept from any input.
    """
    from services.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", video, "-i", audio,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    ]
    if compat:
        cmd += ["-ar", "48000", "-ac", "2", "-map_metadata", "-1", "-avoid_negative_ts", "make_zero"]
    if out_path.lower().endswith((".mp4", ".m4v", ".mov")):
        cmd += ["-movflags", "+faststart"]
    cmd.append(out_path)
    try:
        rc, err = await _ffmpeg(cmd)
    except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
        logger.warning("Voice-swap remux could not run (%s)", type(exc).__name__)
        return False
    if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        logger.warning("Voice-swap remux failed (rc=%s): %s", rc, err.decode(errors="replace")[:200])
        try:
            os.unlink(out_path)
        except OSError:
            pass
        return False
    return True


def _write_wav(path: str, tensor, sr: int) -> None:
    import numpy as np
    from services.voice_conversion import _write_wav as write

    write(path, np.asarray(tensor.reshape(-1).cpu(), dtype=np.float32), sr)


async def swap_video_voice(
    backend,
    video_path: str,
    ref_audio_path: str,
    workdir: str,
    *,
    seed: Optional[int],
    run_job: Callable[[Callable[[], object], float], Awaitable[object]],
    mark: Callable[..., Awaitable[object]],
) -> SwapResult:
    """Run the whole swap inside ``workdir``; the input file is never written.

    ``mark(tensor, sr)`` applies the provenance watermark to the converted
    voice. Raises ``ValueError`` for input the user must fix (no audio, silent
    or too-long soundtrack, unreadable file); everything else degrades.
    """
    from services.ffmpeg_utils import require_audio_stream, validate_media_source
    from services.voice_conversion import MAX_SOURCE_S, convert_recording

    await asyncio.to_thread(validate_media_source, video_path)
    await asyncio.to_thread(require_audio_stream, video_path)

    track = os.path.join(workdir, "track.wav")
    await extract_track(video_path, track)
    import soundfile as sf

    if await asyncio.to_thread(lambda: sf.info(track).duration) > MAX_SOURCE_S:
        raise ValueError(
            f"The source clip is longer than {int(MAX_SOURCE_S // 60)} minutes. "
            "Trim it, or convert it in parts."
        )

    notes: list = []
    split = await separate_vocals(track, workdir)
    if split is None:
        notes.append(NOTE_NO_SEPARATION)
    vocals = split[0] if split else track

    converted = await convert_recording(
        backend, vocals, ref_audio_path, seed=seed, run_job=run_job,
    )
    converted = await mark(converted, backend.sample_rate)
    voice_wav = os.path.join(workdir, "voice.wav")
    _write_wav(voice_wav, converted, backend.sample_rate)

    audio_path = voice_wav
    if split is not None:
        mixed = os.path.join(workdir, "mixed.wav")
        if await mix_background(voice_wav, split[1], mixed):
            audio_path = mixed
        else:
            # Mixing is polish: the converted voice alone beats a failed swap.
            notes.append(NOTE_NO_BACKGROUND)

    ext = os.path.splitext(video_path)[1].lower()
    same = os.path.join(workdir, "swapped" + ext)
    if await mux_video(video_path, audio_path, same):
        return SwapResult(same, audio_path, notes)
    # A container that will not take the stream copy (or AAC): MP4 takes both.
    fallback = os.path.join(workdir, "swapped-compat.mp4")
    if await mux_video(video_path, audio_path, fallback, compat=True):
        return SwapResult(fallback, audio_path, notes)
    notes.append(NOTE_AUDIO_ONLY)
    return SwapResult(None, audio_path, notes)
