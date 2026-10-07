"""Deadline policy for remote tasks.

The original goal doc proposed 2s to accept, 30s to execute, 35s to deliver.
Every one of those numbers is wrong by one to two orders of magnitude for this
product: ``model_manager`` budgets 300s of *execution* scaled by input length,
allows a cold load up to 1800s beyond that, and takes 30s just to spawn an
engine sidecar. A dub runs for minutes; an audiobook for hours. Fixed
second-scale deadlines would mass-kill healthy work.

Two ideas replace them, and both already exist locally — this module only
carries them across the wire:

1. **Phases, not one clock.** Accepting is fast (seconds). Loading a cold model
   is slow (minutes) and gets its own bounded budget. Executing is scaled to
   the input. Delivering a result is a transfer, not compute.

2. **Progress leases, not wall clocks.** ``model_manager`` already treats a job
   as wedged only when it is *silent*, not when it is slow
   (``MODEL_LOAD_HEARTBEAT_GRACE_S``). The same rule governs remote attempts: a
   worker that keeps reporting progress keeps its lease.

All values are **relative durations computed by the server**. Worker wall
clocks are untrusted — they skew, and a laptop that slept has a clock that
jumped.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from typing import Optional

from core.generate_budget import (  # stdlib-only: safe in the torch-free control plane
    CHARS_PER_SECOND as _CHARS_PER_SECOND,
    FREE_CHARS as _FREE_CHARS,
    cpu_auto_budget_s as _cpu_auto_budget_s,
)

# Mirrors of model_manager's env knobs. Duplicated as *fallbacks* only: the
# real values are read from model_manager when it is importable (the control
# plane may run in a process that never loads torch). test_worker_deadlines.py
# asserts the two agree, so a change there cannot silently drift from here.
_GENERATE_TIMEOUT_S = float(os.environ.get("OMNIVOICE_GENERATE_TIMEOUT_S", "300.0"))
_CPU_GENERATE_TIMEOUT_S = float(
    os.environ.get("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "600.0")
)
def _progress_extension_cap_s(env=os.environ) -> float:
    """Mirror of model_manager.progress_extension_cap_s (deprecated alias:
    OMNIVOICE_MODEL_LOAD_TIMEOUT_S)."""
    return float(
        env.get("OMNIVOICE_PROGRESS_EXTENSION_CAP_S")
        or env.get("OMNIVOICE_MODEL_LOAD_TIMEOUT_S")
        or "1800.0"
    )


_MODEL_LOAD_EXTRA_S = _progress_extension_cap_s()
_HEARTBEAT_GRACE_S = float(os.environ.get("OMNIVOICE_MODEL_LOAD_HEARTBEAT_GRACE_S", "30.0"))

# How long a worker has to say "yes" to an assignment. Generous next to the
# original 2s because a busy worker may be mid-inference with the GIL held, but
# still short: silence here means the assignment fell into a void.
_ACCEPT_SECONDS = 20

# Time to push a finished artifact. Consumer uplinks are slow and dub outputs
# are large, so this is sized for "a few hundred MB on a bad connection".
_RESULT_DELIVERY_SECONDS = 900

# How long to wait for a vanished worker before giving up on its attempt. This
# is the window that makes duplicate execution avoidable: reconnect inside it
# with a finished result and the result simply commits.
_DEFAULT_GRACE_SECONDS = 45


class Operation(str, enum.Enum):
    """Deadline classes. Inference duration varies by orders of magnitude
    across these, so one timeout scheme cannot serve them all."""

    DICTATION = "dictation"
    TTS = "tts"
    CLONE = "clone"
    ASR = "asr"
    DUB = "dub"
    BATCH_SEGMENTS = "batch_segments"
    AUDIOBOOK = "audiobook"

    @classmethod
    def coerce(cls, value: str) -> "Operation":
        try:
            return cls(value)
        except ValueError:
            return cls.TTS


# Per-operation multipliers over the base execution budget, plus the grace
# window used when a worker running that operation disconnects. Long jobs get a
# longer grace: losing a 40-minute dub to a 45-second network blip and redoing
# it from zero is the expensive mistake.
_PROFILE: dict[Operation, tuple[float, int]] = {
    Operation.DICTATION: (0.5, 75),
    Operation.ASR: (2.0, 75),
    Operation.TTS: (1.0, 75),
    Operation.CLONE: (2.0, 75),
    Operation.DUB: (12.0, 90),
    Operation.BATCH_SEGMENTS: (12.0, 90),
    Operation.AUDIOBOOK: (24.0, 90),
}


@dataclass(frozen=True)
class Deadlines:
    """Relative budgets for one attempt, in seconds."""

    accept_seconds: int
    model_load_seconds: int
    execution_seconds: int
    progress_lease_seconds: int
    result_delivery_seconds: int
    grace_seconds: int

    @property
    def total_seconds(self) -> int:
        """Worst-case wall time for a single attempt that never stalls."""
        return (
            self.accept_seconds
            + self.model_load_seconds
            + self.execution_seconds
            + self.result_delivery_seconds
        )

    def to_dict(self) -> dict:
        return {
            "accept_seconds": self.accept_seconds,
            "model_load_seconds": self.model_load_seconds,
            "execution_seconds": self.execution_seconds,
            "progress_lease_seconds": self.progress_lease_seconds,
            "result_delivery_seconds": self.result_delivery_seconds,
            "grace_seconds": self.grace_seconds,
        }


def _base_execution_seconds(
    text: Optional[str], *, execution_device: Optional[str] = None,
    under_provisioned: bool = False,
) -> float:
    """Delegate to model_manager's budget; fall back to its formula.

    The lazy import keeps this module usable in a process that has no torch —
    the control plane schedules work it never executes.

    ``under_provisioned`` is the worker's own verdict that its card sits below
    the engine's declared VRAM floor (``ConnectedWorker.under_provisioned``).
    It floors the budget at what the same job would get on a CPU, because that
    is what a card paging to system RAM performs like (#1804). Derived by asking
    for the CPU budget rather than by probing VRAM here: this process is the
    control plane, and its hardware is not the worker's.
    """
    target_device = str(execution_device or "cpu").lower()
    if target_device not in {
        "cpu", "cuda", "mps", "mlx", "directml", "rocm", "vulkan", "xpu",
    }:
        target_device = "cpu"
    if under_provisioned and target_device != "cpu":
        return max(
            _base_execution_seconds(text, execution_device=target_device),
            _base_execution_seconds(text, execution_device="cpu"),
        )
    try:
        from services import model_manager  # noqa: PLC0415 — intentionally lazy

        return float(
            model_manager.generate_timeout_s(
                text, execution_device=target_device
            )
        )
    except Exception:
        base = _GENERATE_TIMEOUT_S
        try:
            if (
                target_device == "cpu"
                and "OMNIVOICE_GENERATE_TIMEOUT_S" not in os.environ
            ):
                base = _CPU_GENERATE_TIMEOUT_S
                if "OMNIVOICE_CPU_GENERATE_TIMEOUT_S" not in os.environ:
                    # Default CPU budget scales at CPU speed (#2609).
                    return _cpu_auto_budget_s(base, len(text or ""))
        except Exception:
            # Capability detection is optional in the torch-free control
            # plane; retain the configured universal bounded fallback.
            pass
        return max(
            base,
            base + max(0, len(text or "") - _FREE_CHARS) / _CHARS_PER_SECOND,
        )


def for_task(
    operation: str,
    *,
    text: Optional[str] = None,
    model_resident: bool = False,
    model_downloaded: bool = True,
    input_seconds: float = 0.0,
    execution_device: Optional[str] = None,
    under_provisioned: bool = False,
) -> Deadlines:
    """Compute the deadlines for one attempt.

    ``model_resident`` and ``model_downloaded`` matter enormously: a warm model
    is ~8s away, a cold one minutes, and one that still has to be downloaded
    can take twenty. Charging a worker the same load budget in all three cases
    is what turns a normal cold start into a quarantine.
    """
    op = Operation.coerce(operation)
    multiplier, grace = _PROFILE[op]

    execution = _base_execution_seconds(
        text, execution_device=execution_device,
        under_provisioned=under_provisioned,
    ) * multiplier
    # Media-length operations scale on duration, not characters.
    if input_seconds > 0:
        execution = max(execution, input_seconds * multiplier)

    if model_resident:
        model_load = 60
    elif model_downloaded:
        model_load = int(_MODEL_LOAD_EXTRA_S / 3)
    else:
        # Weights still to fetch — the full extension, which is what
        # model_manager already allows for a first-use generate.
        model_load = int(_MODEL_LOAD_EXTRA_S)

    return Deadlines(
        accept_seconds=_ACCEPT_SECONDS,
        model_load_seconds=model_load,
        execution_seconds=int(execution),
        # Silence, not slowness, is the failure signal.
        progress_lease_seconds=int(_HEARTBEAT_GRACE_S * 4),
        result_delivery_seconds=_RESULT_DELIVERY_SECONDS,
        grace_seconds=grace,
    )


def default_grace_seconds(operation: str = "") -> int:
    if not operation:
        return _DEFAULT_GRACE_SECONDS
    return _PROFILE[Operation.coerce(operation)][1]


__all__ = ["Deadlines", "Operation", "default_grace_seconds", "for_task"]
