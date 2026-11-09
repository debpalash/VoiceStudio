"""Cached, zero-PyTorch discovery of the GPU the native GGML runtime opens.

Full Vulkan Tools reports provide heaps and BF16 capabilities. If a hidden
surface probe fails, its summary still proves the GPU driver; unique OS
inventory identities can then supply 64-bit VRAM. Without native evidence the
OS fallback remains sizing-only and cannot trigger the GPU offer.

Adapter numbering, duplicate-driver filtering, and shared-memory budgeting are
shared by sizing and reporting. Public imports remain compatible.
"""
from __future__ import annotations

import functools
import logging
import os
import sys
from pathlib import Path

from .vulkan_parse import (
    adapters_from_vulkaninfo,
    parse_vulkaninfo,
)
from .vulkan_probe import run_vulkaninfo as _run_vulkaninfo
from .vulkan_types import (
    APU_MAX_DEDICATED_GB,
    APU_MIN_SYSTEM_RAM_GB,
    VulkanAdapter,
    VulkanProfile,
    adapters_from_inventory,
    effective_vram,
    with_inventory_memory,
)

logger = logging.getLogger("omnivoice.gguf.vulkan")
_GIB = 1024 ** 3

def system_ram_gb() -> float:
    """Total physical RAM in GiB; 0.0 when it cannot be read."""
    try:
        import psutil

        return round(psutil.virtual_memory().total / _GIB, 2)
    except Exception:  # noqa: BLE001
        return 0.0


def has_vulkan_loader() -> bool:
    """Whether the platform Vulkan loader (``vulkan-1.dll`` /
    ``libvulkan.so.1``) is installed. A loader alone does not prove a GPU
    driver; it only lets the OS-inventory fallback count a vendor GPU."""
    try:
        if sys.platform == "win32":
            root = os.environ.get("SystemRoot") or r"C:\Windows"
            return (Path(root) / "System32" / "vulkan-1.dll").is_file()
        if sys.platform.startswith("linux"):
            import ctypes.util

            return ctypes.util.find_library("vulkan") is not None or any(
                Path(d, "libvulkan.so.1").exists()
                for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
                          "/usr/lib64", "/usr/lib")
            )
    except Exception:
        logger.debug("Vulkan loader probe unavailable", exc_info=True)
    return False


def _probe() -> VulkanProfile:
    """Build the profile: ``vulkaninfo`` first, then the OS inventory when the loader exists."""
    ram = system_ram_gb()
    text = _run_vulkaninfo()
    if text is not None:
        devices = parse_vulkaninfo(text)
        if any(not device.get("heap_bytes") for device in devices):
            try:
                from core.gpu_inventory import detect_host_gpus

                devices = with_inventory_memory(devices, detect_host_gpus())
            except Exception:
                logger.debug("Vulkan loader probe unavailable", exc_info=True)
        adapters = adapters_from_vulkaninfo(devices, ram)
        return VulkanProfile(
            verified=bool(adapters), source="vulkaninfo", system_ram_gb=ram, adapters=adapters,
        )
    if has_vulkan_loader():
        try:
            from core.gpu_inventory import detect_host_gpus

            adapters = adapters_from_inventory(detect_host_gpus(), ram)
        except Exception:  # noqa: BLE001 - the inventory is advisory
            adapters = ()
        if adapters:
            return VulkanProfile(verified=True, source="os", system_ram_gb=ram, adapters=adapters)
    return VulkanProfile(verified=False, source="none", system_ram_gb=ram)


@functools.lru_cache(maxsize=1)
def detect_vulkan_profile() -> VulkanProfile:
    """The cached host profile. Never raises."""
    try:
        return _probe()
    except Exception:
        logger.debug("Vulkan discovery failed", exc_info=True)
        return VulkanProfile(verified=False, source="none", system_ram_gb=0.0)


def refresh() -> VulkanProfile:
    """Clear the cache and probe again (driver installed, GPU hot-plugged)."""
    detect_vulkan_profile.cache_clear()
    return detect_vulkan_profile()


__all__ = [
    "APU_MAX_DEDICATED_GB", "APU_MIN_SYSTEM_RAM_GB",
    "VulkanAdapter", "VulkanProfile", "adapters_from_inventory",
    "adapters_from_vulkaninfo", "detect_vulkan_profile", "effective_vram",
    "has_vulkan_loader", "parse_vulkaninfo", "refresh", "system_ram_gb",
]
