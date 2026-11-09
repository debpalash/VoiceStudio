"""Local GPU execution gateway.

All accelerator work in the open-source app runs on this machine. This module
keeps model-load and generation budgets behind one guarded pool boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


class GatewayError(RuntimeError):
    """Base for errors raised by the local GPU gateway."""


class ModelLoadTimeout(GatewayError):
    """A local engine did not finish loading inside the model-load budget."""


@dataclass(frozen=True)
class LocalCall:
    """A zero-argument blocking callable submitted to the local GPU pool."""

    fn: Optional[Callable[[], Any]] = None
    what: str = "GPU job"
    timeout: Optional[float] = None
    queue_timeout: Optional[float] = None
    min_vram_gb: float = 0.0
    on_abandon: Optional[Callable[[], None]] = None
    prepare: Optional[Callable[[], Any]] = None


def _model_load_timeout() -> float:
    from services.model_manager import _model_load_timeout as resolve  # noqa: PLC0415

    return float(resolve())


async def prewarm(
    *,
    backend=None,
    engine: str = "",
    timeout: Optional[float] = None,
    executor=None,
) -> None:
    """Make a local engine ready under the model-load budget."""
    if backend is None:
        return
    call = LocalCall(
        backend.ensure_ready,
        what=f"TTS engine '{engine or getattr(backend, 'engine_id', '') or 'model'}' model load",
        timeout=timeout if timeout is not None else _model_load_timeout(),
    )
    try:
        await _run_local(call, executor=executor)
    except TimeoutError as exc:
        if hasattr(exc, "retry_after"):
            raise
        raise ModelLoadTimeout(
            f"{call.what} did not finish within its budget. The first load of an "
            "engine can include a multi-GB download; check the connection or set "
            "a Hugging Face mirror in Settings, then retry."
        ) from exc


async def run(
    *,
    local: LocalCall,
    admit: bool = False,
    executor=None,
) -> Any:
    """Run one unit of accelerator work on this machine."""
    return await _run_local(local, admit=admit, executor=executor)


async def _run_local(call: LocalCall, *, admit: bool = False, executor=None) -> Any:
    if call.prepare is not None:
        prepared = await call.prepare()
        if not isinstance(prepared, LocalCall):
            raise TypeError("LocalCall.prepare must return a LocalCall")
        return await _run_local(prepared, admit=admit, executor=executor)
    if call.fn is None:
        raise TypeError("LocalCall requires fn or prepare")

    from services.model_manager import check_gpu_admission, run_on_gpu_pool_guarded

    if admit:
        check_gpu_admission(what=call.what, executor=executor)
    return await run_on_gpu_pool_guarded(
        call.fn,
        what=call.what,
        timeout=call.timeout,
        queue_timeout=call.queue_timeout,
        min_vram_gb=call.min_vram_gb,
        executor=executor,
        on_abandon=call.on_abandon,
    )


__all__ = [
    "GatewayError", "LocalCall", "ModelLoadTimeout", "prewarm", "run",
]
