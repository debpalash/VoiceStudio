"""Free-memory probe + a non-blocking low-memory advisory.

The device-caps probe (core.device_caps) reports *total* memory, resolved once
per process. Load decisions need *free* memory at the moment of loading — and on
Apple Silicon the number that matters is free **system RAM**, because MPS uses
unified memory (there is no separate VRAM pool). This module fills that gap.

Deliberately advisory, never blocking: a hard "refuse to load" on an estimate
would brick legitimate loads on machines that would actually cope (the estimate
can't know a model's true resident size ahead of time, and the OS can reclaim
cache under pressure). Instead it surfaces a warning so the UI and logs can say
"you're low on memory" — and the single-active-engine eviction
(services.engine_memory) is what actually reclaims room before a load.

Stdlib + psutil (already a runtime dep). Never raises.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional
from core.logging_utils import log_safe

logger = logging.getLogger("omnivoice.memory_budget")

# Below this much free RAM, a heavy model load is at real risk of tipping the
# machine into the OOM-kill territory behind the 16 GB-Mac "Can't reach the
# backend" reports. Tunable for smaller/larger boxes.
def _configured_headroom_gb() -> float:
    try:
        value = float(os.environ.get("OMNIVOICE_LOW_MEMORY_HEADROOM_GB", "2.0"))
    except ValueError:
        return 2.0
    return value if math.isfinite(value) and value >= 0 else 2.0


_LOW_RAM_HEADROOM_GB = _configured_headroom_gb()


def available_memory() -> dict:
    """Free/total memory right now. Never raises; fields absent when unknown.

    Always includes system RAM (``ram_available_gb`` / ``ram_total_gb``). On a
    CUDA/ROCm host also includes GPU VRAM (``vram_free_gb`` / ``vram_total_gb``)
    from ``torch.cuda.mem_get_info``. On MPS the relevant figure is system RAM
    (unified memory), so no separate VRAM fields are reported."""
    out: dict = {}
    try:
        import psutil

        vm = psutil.virtual_memory()
        out["ram_available_gb"] = round(vm.available / (1024 ** 3), 2)
        out["ram_total_gb"] = round(vm.total / (1024 ** 3), 2)
    except Exception:  # noqa: BLE001 — psutil missing/failed: RAM unknown, not fatal
        pass
    try:
        torch = __import__("torch")
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            out["vram_free_gb"] = round(free / (1024 ** 3), 2)
            out["vram_total_gb"] = round(total / (1024 ** 3), 2)
    except Exception:  # noqa: BLE001 — no CUDA / probe failed
        pass
    return out


def low_memory_warning(headroom_gb: float = _LOW_RAM_HEADROOM_GB) -> Optional[str]:
    """A one-line advisory when free memory is below ``headroom_gb``, else None.

    Checks free VRAM on a dedicated-GPU host, otherwise free system RAM (the
    figure that matters on MPS/CPU). Pure given ``available_memory`` output —
    ``_format`` does the wording — so the threshold logic is unit-testable."""
    return _format(available_memory(), headroom_gb)


def _format(mem: dict, headroom_gb: float) -> Optional[str]:
    vram = mem.get("vram_free_gb")
    if vram is not None:
        if vram < headroom_gb:
            return (
                f"Low GPU memory: {vram:.1f} GB free. Loading another model may "
                "run out of VRAM — unload one you're not using "
                "(Unload in Model Catalogue), or switch to a smaller engine."
            )
        return None
    ram = mem.get("ram_available_gb")
    if ram is not None and ram < headroom_gb:
        return (
            f"Low memory: {ram:.1f} GB free. Loading a large model here risks the "
            "backend being killed by the OS — close some apps, or unload a model "
            "you're not using (Unload in Model Catalogue)."
        )
    return None


def log_if_low(context: str, headroom_gb: float = _LOW_RAM_HEADROOM_GB) -> Optional[str]:
    """Log (once, at WARNING) and return the advisory when memory is low before
    a heavy operation named by ``context``. Non-blocking — the caller proceeds
    regardless; this is forensics, so a later OOM death has a breadcrumb."""
    msg = low_memory_warning(headroom_gb)
    if msg:
        logger.warning("%s: %s", log_safe(context), log_safe(msg))
    return msg
