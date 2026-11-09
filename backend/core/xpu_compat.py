"""Intel XPU compatibility shims.

On Windows, Intel Arc drivers (e.g. B580 / Battlemage) do not expose the
free-memory query, so ``torch.xpu.mem_get_info()`` raises "doesn't support
querying the available free memory". transformers' ``caching_allocator_warmup``
calls it on every ``from_pretrained`` to a non-CPU device, which turns every
model load on XPU into a 500. Fall back to ``total - reserved`` — the same
estimate the allocator itself can vouch for.

Installed once per process by ``main.py`` right after torch is first imported,
and again (idempotently) by ``model_manager.get_best_device()`` so processes
that import model_manager without ``main.py`` (the OmniVoice subprocess) are
covered too. Isolated engine sidecars that select XPU cannot import this
module; they carry a pinned copy of the same fallback
(``tests/test_xpu_mem_get_info_shim.py`` keeps the copies in step).
"""

import logging

logger = logging.getLogger("omnivoice.model")

_PATCHED = False
_WARNED = False


def _device_index(torch, device) -> int:
    if device is None:
        return int(torch.xpu.current_device())
    if isinstance(device, int):
        return device
    index = getattr(torch.device(device), "index", None)
    return int(torch.xpu.current_device()) if index is None else int(index)


def patch_xpu_mem_get_info(torch) -> None:
    """Make ``torch.xpu.mem_get_info`` fall back when the driver refuses it.

    No-op when torch has no XPU backend; idempotent.
    """
    global _PATCHED
    xpu = getattr(torch, "xpu", None)
    if _PATCHED or xpu is None or not hasattr(xpu, "mem_get_info"):
        return
    original = xpu.mem_get_info

    def mem_get_info(device=None):
        global _WARNED
        try:
            return original(device)
        except RuntimeError as exc:
            idx = _device_index(torch, device)
            total = int(torch.xpu.get_device_properties(idx).total_memory)
            free = max(total - int(torch.xpu.memory_reserved(idx)), 0)
            if not _WARNED:
                _WARNED = True
                logger.warning(
                    "XPU driver cannot report free memory (%s); estimating it "
                    "as total minus torch-reserved", exc,
                )
            return free, total

    xpu.mem_get_info = mem_get_info
    _PATCHED = True
    logger.debug("XPU mem_get_info fallback installed")


def install() -> None:
    """Process-wide install; call after torch is importable."""
    import torch

    patch_xpu_mem_get_info(torch)
