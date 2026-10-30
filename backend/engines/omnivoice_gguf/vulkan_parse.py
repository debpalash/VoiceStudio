"""Parse Vulkan Tools reports without importing PyTorch or spawning processes."""
from __future__ import annotations

from .vulkan_types import DeviceType, VulkanAdapter, _vendor, effective_vram

_GIB = 1024 ** 3

def _int_value(raw: str) -> int | None:
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
    current: dict | None = None
    heap: dict | None = None
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
