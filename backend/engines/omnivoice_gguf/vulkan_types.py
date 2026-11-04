"""Native Vulkan adapter identities and conservative memory budgeting."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

DeviceType = Literal["DISCRETE_GPU", "INTEGRATED_GPU"]
APU_MIN_SYSTEM_RAM_GB = 16.0
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
    bf16: bool | None = None

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

    def best(self) -> VulkanAdapter | None:
        """The adapter ``ggml_backend_init_best`` opens: first discrete, else
        first integrated."""
        if not self.verified:
            return None
        for kind in ("DISCRETE_GPU", "INTEGRATED_GPU"):
            for adapter in self.adapters:
                if adapter.device_type == kind:
                    return adapter
        return None

    def by_index(self, index: int) -> VulkanAdapter | None:
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


def effective_vram(
    device_type: DeviceType, dedicated_gb: float, ram_gb: float,
) -> tuple[float, bool]:
    """``(budget_gb, shared_memory)`` for one adapter (see module docstring)."""
    shared = device_type == "INTEGRATED_GPU" or 0 < dedicated_gb <= APU_MAX_DEDICATED_GB
    if shared and ram_gb >= APU_MIN_SYSTEM_RAM_GB:
        return max(dedicated_gb, round(ram_gb * 0.5, 2)), True
    return dedicated_gb, False


def _vendor(vendor_id: int | None, name: str) -> str:
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


def with_inventory_memory(devices: list[dict], gpus) -> list[dict]:
    """Fill missing heaps only for a unique vendor/name identity on both sides.

    Vulkan still proves the driver, type, and numbering. OS inventory supplies
    only capacity; it never proves BF16 or that a GPU driver works.
    """
    import math

    def key(vendor, name):
        return vendor, " ".join(name.split()).casefold()

    native_keys = [key(_vendor(d.get("vendor_id"), d.get("name", "")), d.get("name", ""))
                   for d in devices]
    inventory = {}
    for gpu in gpus:
        inventory.setdefault(key(gpu.vendor, gpu.name), []).append(gpu)
    result = []
    for device, identity in zip(devices, native_keys):
        matches = inventory.get(identity, [])
        copy = dict(device)
        physical_ids = {d.get("uuid") or ("index", d["vk_index"])
                        for d, native_key in zip(devices, native_keys) if native_key == identity}
        if not copy.get("heap_bytes") and len(matches) == 1 and len(physical_ids) == 1:
            memory = float(matches[0].vram_gb or 0)
            if math.isfinite(memory) and memory > 0:
                copy["heap_bytes"] = int(memory * 1024 ** 3)
        result.append(copy)
    return result
