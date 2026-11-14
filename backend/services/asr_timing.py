"""Local duration discovery and one deadline across ASR engine layers."""
from time import monotonic

from core.asr_budget import transcribe_budget_s


def file_transcribe_budget_s(path: str, *, floor_s: float | None = None) -> float:
    from services.ffmpeg_utils import probe_audio_duration

    return transcribe_budget_s(probe_audio_duration(path), floor_s=floor_s)


def transcribe_deadline(path: str, *, floor_s: float | None = None) -> float:
    """Reuse the caller's guard deadline, including queue/load/conversion time."""
    from services.inference_cancellation import current_cancellation

    cancellation = current_cancellation()
    if cancellation is not None and cancellation.deadline is not None:
        return cancellation.deadline
    started = monotonic()
    return started + file_transcribe_budget_s(path, floor_s=floor_s)


def remaining_s(deadline: float) -> float:
    return max(0.0, deadline - monotonic())
