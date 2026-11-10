"""Zero-PyTorch Vulkan adapter discovery for the OmniVoice GGUF runtime.

The native ``omnivoice-tts`` binary reaches the GPU through GGML's Vulkan
module, independently of whatever PyTorch build the parent runs. On the
commonest AMD and Intel hosts (a Radeon or Arc card with the CPU or CUDA
torch wheel) PyTorch therefore sees no GPU while the native runtime has one.
This module answers, without torch, the question the quant policy and the
Compute-device report need: *which adapter will GGML use, and how much memory
can it address?*

Sources, in order:

1. ``vulkaninfo --summary`` (ships with the AMD/Intel/NVIDIA drivers on
   Windows and with ``vulkan-tools`` on Linux). It proves a working driver and
   gives Vulkan's physical-device order. Since the summary omits memory heaps,
   VRAM is matched by GPU name from the OS inventory.
2. Without it, the OS GPU inventory (``core.gpu_inventory``: the 64-bit
   ``HardwareInformation.qwMemorySize`` registry value on Windows, DRM sysfs on
   Linux), counted only when the platform Vulkan loader is installed. This can
   size a quant but does not prove Vulkan can open the adapter. The WMI
   ``Win32_VideoController.AdapterRAM`` field is never used: it is 32-bit and
   wraps at 4 GB.

Device numbering mirrors the pinned GGML (``ggml-vulkan.cpp``,
``ggml_vk_instance_init``): every discrete or integrated GPU, in Vulkan
enumeration order, with a second driver for the same GPU (RADV + AMDVLK)
dropped. ``Vulkan<N>`` names the N-th entry of that list. Automatic selection
follows ``ggml_backend_init_best``: the first discrete GPU, else the first
integrated one — so the adapter sized here is the adapter the runtime opens.

Integrated GPUs (an AMD Ryzen AI APU, an Intel Core Ultra iGPU) address shared
system memory: a 512 MB BIOS frame buffer says nothing about what Vulkan can
allocate. For those the budget is ``max(device-local heap, system RAM / 2)``
when the host has at least 16 GB of RAM — the same half-of-RAM rule the MPS
path uses for unified memory.

Contract: never raises; no network; one bounded ``vulkaninfo`` spawn per
process (cached — :func:`refresh` re-probes).
"""
from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess  # nosec B404 -- fixed argv, resolved vulkaninfo path
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger("omnivoice.gguf.vulkan")

DeviceType = Literal["DISCRETE_GPU", "INTEGRATED_GPU"]

_GIB = 1024 ** 3
_VULKANINFO_TIMEOUT_S = 10.0
#: An integrated GPU on a host with at least this much RAM is budgeted on
#: shared memory (half the RAM), not on its BIOS frame buffer.
APU_MIN_SYSTEM_RAM_GB = 16.0
#: An adapter whose dedicated memory is at or below this is treated as using
#: shared memory even when the driver does not report it as integrated.
APU_MAX_DEDICATED_GB = 2.0

_VENDOR_IDS = {0x1002: "amd", 0x8086: "intel", 0x10DE: "nvidia"}


@dataclass(frozen=True)
class VulkanAdapter:
    """One GPU in GGML's Vulkan device list."""

    index: int                  #: ``Vulkan<index>`` in GGML's numbering
    vk_index: int               #: physical-device index vulkaninfo printed
    name: str
    vendor: str                 #: "amd" | "intel" | "nvidia" | "other"
    device_type: DeviceType
    dedicated_vram_gb: float    #: largest device-local heap (or OS VRAM)
    effective_vram_gb: float    #: budget the quant policy sizes against
    shared_memory: bool         #: budgeted on shared system RAM (APU / iGPU)
    #: native bfloat16 shaders (``VK_KHR_shader_bfloat16``); None when unknown
    bf16: Optional[bool] = None

    @property
    def effective_vram_mb(self) -> int:
        """The effective budget in MiB, as the quant buckets expect."""
        return int(self.effective_vram_gb * 1024)

    @property
    def ggml_name(self) -> str:
        """The device name GGML uses, e.g. ``Vulkan1``."""
        return f"Vulkan{self.index}"


@dataclass(frozen=True)
class VulkanProfile:
    """What the probe found. ``verified`` means a working Vulkan driver."""

    verified: bool
    source: Literal["vulkaninfo", "os", "none"]
    system_ram_gb: float
    adapters: tuple[VulkanAdapter, ...] = field(default_factory=tuple)

    def best(self) -> Optional[VulkanAdapter]:
        """The adapter ``ggml_backend_init_best`` opens: first discrete, else
        first integrated."""
        if not self.verified:
            return None
        for kind in ("DISCRETE_GPU", "INTEGRATED_GPU"):
            for adapter in self.adapters:
                if adapter.device_type == kind:
                    return adapter
        return None

    def by_index(self, index: int) -> Optional[VulkanAdapter]:
        """The adapter GGML numbers ``index``, or None (also when unverified)."""
        if not self.verified:
            return None
        return next((a for a in self.adapters if a.index == index), None)

    def to_dict(self) -> dict:
        """JSON-ready profile, with the adapter GGML would open as ``selected``."""
        data = asdict(self)
        best = self.best()
        data["selected"] = asdict(best) if best else None
        return data


# ── system memory / loader ──────────────────────────────────────────────────


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
    except Exception:  # noqa: BLE001
        pass
    return False


# ── budgeting ───────────────────────────────────────────────────────────────


def effective_vram(
    device_type: DeviceType, dedicated_gb: float, ram_gb: float,
) -> tuple[float, bool]:
    """``(budget_gb, shared_memory)`` for one adapter (see module docstring)."""
    shared = device_type == "INTEGRATED_GPU" or 0 < dedicated_gb <= APU_MAX_DEDICATED_GB
    if shared and ram_gb >= APU_MIN_SYSTEM_RAM_GB:
        return max(dedicated_gb, round(ram_gb * 0.5, 2)), True
    return dedicated_gb, False


def _vendor(vendor_id: Optional[int], name: str) -> str:
    """Vendor slug from the PCI vendor ID, else from the device name."""
    if vendor_id in _VENDOR_IDS:
        return _VENDOR_IDS[vendor_id]
    upper = name.upper()
    if "AMD" in upper or "RADEON" in upper:
        return "amd"
    if "INTEL" in upper or " ARC" in upper or upper.startswith("ARC"):
        return "intel"
    if "NVIDIA" in upper or "GEFORCE" in upper:
        return "nvidia"
    return "other"


# ── vulkaninfo parsing ──────────────────────────────────────────────────────


def _int_value(raw: str) -> Optional[int]:
    """``12868124672 (0x2ff000000) (11.98 GiB)`` → 12868124672; ``0x1002`` →
    4098. Plain token parsing, no regex."""
    token = raw.strip().split(" ", 1)[0].strip()
    try:
        return int(token, 0)
    except ValueError:
        return None


def parse_vulkaninfo(text: str) -> list[dict]:
    """Physical devices from ``vulkaninfo`` text output (full or
    ``--summary``), as ``{vk_index, name, type, vendor_id, uuid, heap_bytes,
    bf16}``.

    Device sections start at a ``GPU<n>:`` line. Inside one, the memory heap
    blocks (``memoryHeaps[i]:`` … ``size = …`` … ``MEMORY_HEAP_DEVICE_LOCAL_BIT``)
    give the device-local heap; the largest one wins. A device printed in more
    than one section is merged by index.

    ``bf16`` is the ``shaderBFloat16Type`` feature when printed, else whether
    ``VK_KHR_shader_bfloat16`` is in the device's extension list, else None
    (``--summary`` output lists neither).
    """
    devices: dict[int, dict] = {}
    current: Optional[dict] = None
    heap: Optional[dict] = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("GPU") and s.endswith(":") and s[3:-1].isdigit():
            idx = int(s[3:-1])
            current = devices.setdefault(idx, {
                "vk_index": idx, "name": "", "type": "", "vendor_id": None,
                "uuid": "", "heap_bytes": 0,
                "_bf16_feature": None, "_bf16_ext": False, "_ext_list": False,
            })
            heap = None
            continue
        if current is None:
            continue
        if s.startswith("Device Extensions"):
            current["_ext_list"] = True
        elif s.startswith("VK_KHR_shader_bfloat16"):
            current["_bf16_ext"] = True
        if s.startswith("memoryHeaps[") and s.endswith(":"):
            heap = {"size": None}
            continue
        if s.startswith("memoryTypes"):
            heap = None
            continue
        if heap is not None and "MEMORY_HEAP_DEVICE_LOCAL_BIT" in s:
            current["heap_bytes"] = max(current["heap_bytes"], heap.get("size") or 0)
            continue
        key, sep, value = s.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if heap is not None and key == "size" and heap["size"] is None:
            heap["size"] = _int_value(value)
        elif key == "deviceName" and not current["name"]:
            current["name"] = value
        elif key == "deviceType" and not current["type"]:
            current["type"] = value
        elif key == "vendorID" and current["vendor_id"] is None:
            current["vendor_id"] = _int_value(value)
        elif key == "deviceUUID" and not current["uuid"]:
            current["uuid"] = value
        elif key == "shaderBFloat16Type" and current["_bf16_feature"] is None:
            current["_bf16_feature"] = value.lower() == "true"
    out = []
    for i in sorted(devices):
        dev = devices[i]
        feature, ext, listed = dev.pop("_bf16_feature"), dev.pop("_bf16_ext"), dev.pop("_ext_list")
        dev["bf16"] = feature if feature is not None else (True if ext else False if listed else None)
        out.append(dev)
    return out


def adapters_from_vulkaninfo(devices: list[dict], ram_gb: float) -> tuple[VulkanAdapter, ...]:
    """GGML's Vulkan device list from parsed ``vulkaninfo`` devices: discrete
    and integrated GPUs only (CPU rasterizers such as llvmpipe are skipped),
    a second driver for the same GPU (same UUID) dropped, numbered in order."""
    out: list[VulkanAdapter] = []
    seen_uuids: set[str] = set()
    for dev in devices:
        raw_type = dev.get("type") or ""
        if "DISCRETE_GPU" in raw_type:
            device_type: DeviceType = "DISCRETE_GPU"
        elif "INTEGRATED_GPU" in raw_type:
            device_type = "INTEGRATED_GPU"
        else:
            continue  # CPU / virtual / other: GGML does not list them
        uuid = dev.get("uuid") or ""
        if uuid and uuid in seen_uuids:
            continue
        if uuid:
            seen_uuids.add(uuid)
        name = dev.get("name") or f"Vulkan GPU {dev['vk_index']}"
        dedicated = round((dev.get("heap_bytes") or 0) / _GIB, 2)
        budget, shared = effective_vram(device_type, dedicated, ram_gb)
        out.append(VulkanAdapter(
            index=len(out), vk_index=dev["vk_index"], name=name,
            vendor=_vendor(dev.get("vendor_id"), name), device_type=device_type,
            dedicated_vram_gb=dedicated, effective_vram_gb=budget, shared_memory=shared,
            bf16=dev.get("bf16"),
        ))
    return tuple(out)


def _run_vulkaninfo() -> Optional[str]:
    """The short adapter summary, or None when Vulkan cannot enumerate GPUs.

    The full dump can exceed the 10-second probe budget on switchable-graphics
    drivers (including some AMD laptops). ``--summary`` proves the driver and
    preserves Vulkan's device order; VRAM is enriched from the OS inventory.
    """
    exe = shutil.which("vulkaninfo")
    if not exe:
        return None
    try:
        proc = subprocess.run(  # nosec B603 -- fixed argv
            [exe, "--summary"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VULKANINFO_TIMEOUT_S, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("vulkaninfo failed: %s", exc)
        return None
    out = proc.stdout or ""
    return out if "deviceName" in out else None


# ── OS fallback ─────────────────────────────────────────────────────────────


def adapters_from_inventory(gpus, ram_gb: float) -> tuple[VulkanAdapter, ...]:
    """GGML's list built from the OS GPU inventory, for hosts without
    ``vulkaninfo``. Physical order is the inventory's (registry / DRM card)
    order — the closest available stand-in for Vulkan enumeration order."""
    out: list[VulkanAdapter] = []
    for gpu in gpus:
        if gpu.vendor not in ("amd", "intel", "nvidia"):
            continue
        device_type: DeviceType = "DISCRETE_GPU" if gpu.discrete else "INTEGRATED_GPU"
        budget, shared = effective_vram(device_type, float(gpu.vram_gb or 0.0), ram_gb)
        out.append(VulkanAdapter(
            index=len(out), vk_index=len(out), name=gpu.name, vendor=gpu.vendor,
            device_type=device_type, dedicated_vram_gb=float(gpu.vram_gb or 0.0),
            effective_vram_gb=budget, shared_memory=shared,
        ))
    return tuple(out)


def _enrich_vulkan_memory(
    adapters: tuple[VulkanAdapter, ...], ram_gb: float,
) -> tuple[VulkanAdapter, ...]:
    """Fill summary-only adapters' VRAM from the OS inventory by GPU name.

    ``vulkaninfo --summary`` is enough to verify the driver and device order,
    but it omits memory heaps. Keep the authoritative Vulkan indices and use
    the OS inventory only for memory sizing.
    """
    if not adapters or all(adapter.dedicated_vram_gb > 0 for adapter in adapters):
        return adapters
    try:
        from core.gpu_inventory import detect_host_gpus

        inventory = {
            (gpu.vendor, gpu.name.casefold()): gpu
            for gpu in detect_host_gpus()
        }
    except Exception:  # noqa: BLE001 - memory inventory is advisory
        return adapters

    enriched = []
    for adapter in adapters:
        gpu = inventory.get((adapter.vendor, adapter.name.casefold()))
        if adapter.dedicated_vram_gb > 0 or gpu is None or gpu.vram_gb <= 0:
            enriched.append(adapter)
            continue
        budget, shared = effective_vram(adapter.device_type, gpu.vram_gb, ram_gb)
        enriched.append(replace(
            adapter,
            dedicated_vram_gb=gpu.vram_gb,
            effective_vram_gb=budget,
            shared_memory=shared,
        ))
    return tuple(enriched)


# ── public entry points ─────────────────────────────────────────────────────


def _probe() -> VulkanProfile:
    """Build the profile: ``vulkaninfo`` first, then the OS inventory when the loader exists."""
    ram = system_ram_gb()
    text = _run_vulkaninfo()
    if text is not None:
        adapters = _enrich_vulkan_memory(
            adapters_from_vulkaninfo(parse_vulkaninfo(text), ram), ram,
        )
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
    except Exception:  # noqa: BLE001 - discovery must never break the engine
        logger.debug("Vulkan discovery failed", exc_info=True)
        return VulkanProfile(verified=False, source="none", system_ram_gb=0.0)


def refresh() -> VulkanProfile:
    """Clear the cache and probe again (driver installed, GPU hot-plugged)."""
    detect_vulkan_profile.cache_clear()
    return detect_vulkan_profile()


__all__ = [
    "VulkanAdapter", "VulkanProfile", "adapters_from_inventory",
    "adapters_from_vulkaninfo", "detect_vulkan_profile", "effective_vram",
    "has_vulkan_loader", "parse_vulkaninfo", "refresh", "system_ram_gb",
]
