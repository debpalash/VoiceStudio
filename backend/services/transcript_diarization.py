"""Speaker labels for file transcription (`POST /transcribe`, accurate mode).

Reuses the dub pipeline's diarization loader and overlap assignment so both
features pick the same backend (pyannote or native Sortformer) and label
speakers the same way ("Speaker 1", "Speaker 2", …).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("omnivoice.transcribe")

MAX_SPEAKER_HINT = 20


def parse_speaker_hint(value: Optional[str]) -> Optional[int]:
    """Multipart `num_speakers`: a positive int up to ``MAX_SPEAKER_HINT``, else None."""
    try:
        count = int((value or "").strip())
    except ValueError:
        return None
    return count if 1 <= count <= MAX_SPEAKER_HINT else None


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
        diarization = pipeline(audio_path, num_speakers=hint) if hint else pipeline(audio_path)
        assigned = assign_speakers_from_diarization([d for _, d in timed], diarization)
        for (index, _), item in zip(timed, assigned):
            labels[index] = item.get("speaker_id")
    except Exception:
        logger.exception("Transcript diarization failed")
        return [None] * len(segments), "DIARIZATION_FAILED"
    return labels, None
