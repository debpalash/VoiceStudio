"""One execution clock shared by ASR guards and child engines."""
from time import monotonic
from typing import Callable


def transcribe_deadline(budget_s: Callable[[], float], *, started_at: float | None = None) -> float:
    """Reuse the caller's guard deadline, including queue/load/conversion time."""
    from services.inference_cancellation import current_cancellation

    cancellation = current_cancellation()
    if cancellation is not None and cancellation.deadline is not None:
        return cancellation.deadline
    started = monotonic() if started_at is None else started_at
    return started + budget_s()


def remaining_s(deadline: float) -> float:
    return max(0.0, deadline - monotonic())
