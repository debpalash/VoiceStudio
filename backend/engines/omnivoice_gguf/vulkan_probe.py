"""Bounded hidden Vulkan Tools probes, including a surface-free fallback."""
from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger("omnivoice.gguf.vulkan")


def run_vulkaninfo() -> str | None:
    """Keep full heaps/BF16 when available; retry driver enumeration on failure.

    Full reports can hang probing presentation surfaces in a hidden Windows
    process. --summary skips those surfaces but still enumerates real GPUs.
    Both probes stay bounded, hidden, and offline. Failed output is untrusted.
    """
    exe = shutil.which("vulkaninfo")
    if not exe:
        return None
    for args, timeout in (([exe], 10.0), ([exe, "--summary"], 3.0)):
        try:
            proc = subprocess.run(  # nosec B603 -- fixed argv, resolved executable
                args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Vulkan probe failed: %s", exc)
            continue
        if proc.returncode == 0 and "deviceName" in (proc.stdout or ""):
            return proc.stdout
    return None
