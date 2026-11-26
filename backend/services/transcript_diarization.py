"""Speaker labels for file transcription (`POST /transcribe`, accurate mode).

Reuses the dub pipeline's diarization loader and overlap assignment so both
features pick the same backend (pyannote or native Sortformer) and label
speakers the same way ("Speaker 1", "Speaker 2", …).
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from typing import Iterator, Optional

logger = logging.getLogger("omnivoice.transcribe")

MAX_SPEAKER_HINT = 20


def parse_speaker_hint(value: Optional[str]) -> Optional[int]:
    """Multipart `num_speakers`: a positive int up to ``MAX_SPEAKER_HINT``, else None."""
    try:
        count = int((value or "").strip())
    except ValueError:
        return None
    return count if 1 <= count <= MAX_SPEAKER_HINT else None


@contextlib.contextmanager
def _diarizable_audio(audio_path: str) -> Iterator[str]:
    """A path both diarization backends can open.

    pyannote reads through soundfile/torchaudio and the native Sortformer reads
    WAV, so an upload libsndfile cannot open (M4A/AAC, some MP3/WebM builds) is
    decoded once with the shared decoder (torchaudio, soundfile, then the
    ffmpeg CLI) into a temporary WAV that is removed afterwards. A file
    libsndfile already reads is passed through untouched.
    """
    import soundfile as sf

    try:
        readable = not os.path.isfile(audio_path) or bool(sf.info(audio_path))
    except Exception:
        readable = False
    if readable:
        yield audio_path
        return
    from services.audio_io import load_audio

    waveform, sample_rate = load_audio(audio_path)
    handle, wav_path = tempfile.mkstemp(prefix="diarize-", suffix=".wav")
    os.close(handle)
    try:
        sf.write(wav_path, waveform.numpy().T, sample_rate, subtype="PCM_16")
        yield wav_path
    finally:
        with contextlib.suppress(OSError):
            os.remove(wav_path)


def diarize_segments(
    audio_path: str,
    segments: list[dict],
    num_speakers: Optional[int] = None,
) -> tuple[list[Optional[str]], Optional[str]]:
    """Label each ASR segment with a speaker.

    Returns ``(labels, error)``: one label per input segment (``None`` where
    the segment has no usable timing or no speaker turn overlaps it) and an
    error code when diarization could not run. It never raises, so a missing
    or broken diarization model degrades to a plain transcript.
    """
    labels: list[Optional[str]] = [None] * len(segments)
    timed = [
        (i, {"start": float(s["start"]), "end": float(s["end"])})
        for i, s in enumerate(segments)
        if isinstance(s.get("start"), (int, float)) and isinstance(s.get("end"), (int, float))
    ]
    if not timed:
        return labels, None
    try:
        from services.model_manager import get_diarization_pipeline
        from services.segmentation import assign_speakers_from_diarization

        pipeline, error = get_diarization_pipeline(return_error=True)
        if pipeline is None:
            return labels, error or "LOAD_FAILED"
        from services.diarization_native import NativeSortformer

        # Sortformer detects up to four speakers and rejects an exact count.
        hint = None if isinstance(pipeline, NativeSortformer) else num_speakers
        if isinstance(pipeline, NativeSortformer):
            # Its child process is the only part that can be stopped. When the
            # request is abandoned (timeout or client gone) the guard sets this
            # scope's cancellation; passing it through lets the child exit
            # instead of holding the GPU pool until its own deadline.
            from services.inference_cancellation import current_cancellation

            scope = current_cancellation()
            with _diarizable_audio(audio_path) as source:
                # Decoding can take a while; a request abandoned meanwhile must
                # not start the native child at all.
                if scope and scope.cancelled.is_set():
                    return [None] * len(segments), "DIARIZATION_FAILED"
                diarization = pipeline(
                    source, cancel_check=scope.cancelled.is_set if scope else None
                )
        else:
            with _diarizable_audio(audio_path) as source:
                diarization = pipeline(source, num_speakers=hint) if hint else pipeline(source)
        assigned = assign_speakers_from_diarization([d for _, d in timed], diarization)
        for (index, _), item in zip(timed, assigned):
            labels[index] = item.get("speaker_id")
    except Exception:
        logger.exception("Transcript diarization failed")
        return [None] * len(segments), "DIARIZATION_FAILED"
    return labels, None
