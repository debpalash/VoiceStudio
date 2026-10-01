"""Which GPUs are physically in this machine — independent of what PyTorch sees.

``device_caps`` answers "what can the installed torch accelerate on"; this
answers the question a user actually asks: "I have a Radeon, why is it idle?".
The two disagree on the commonest broken host (an AMD card + the NVIDIA-CUDA
torch wheel the Windows installer ships), and the gap between them is the
explanation.

Contract (same as ``device_caps``): never raises, no network, **no subprocess**
and no torch import — a registry read on Windows, a sysfs read on Linux — so it
is safe to call from the cold-start probe. macOS returns ``()`` (Apple Silicon
is reported through torch MPS, and Intel Macs have no accelerated path anyway).
"""
from __future__ import annotations

import functools
import os
import sys
from dataclasses import dataclass
from typing import Literal

GPUVendor = Literal["nvidia", "amd", "intel", "other"]

_PCI_VENDORS: dict[str, GPUVendor] = {
    "10de": "nvidia",
    "1002": "amd",
    "8086": "intel",
}

# Display adapter class GUID — every installed graphics driver is a numbered
# subkey under it.
_WIN_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11cd-8000-0000f3ed53be}"
)
_LINUX_DRM = "/sys/class/drm"
_GB = 1024 ** 3
_INTEL_IGPU_BDF = "0000:00:02.0"


@dataclass(frozen=True)
class HostGPU:
    vendor: GPUVendor
    name: str
    vram_gb: float = 0.0
    pci_device_id: str = ""
    #: A dedicated card rather than an integrated GPU. Only consulted for Intel
    #: (AMD/NVIDIA parts are always explained). Not derived from VRAM alone:
    #: Linux's i915/xe drivers expose no ``mem_info_vram_total``, so an Arc card
    #: reads 0 GB there and would otherwise be dropped.
    discrete: bool = False


def _vendor_for(pci_vendor_id: str) -> GPUVendor | None:
    return _PCI_VENDORS.get(pci_vendor_id.strip().lower().removeprefix("0x"))


def _vendor_id_from_matching(matching_id: str) -> str:
    """``PCI\\VEN_1002&DEV_7550&...`` -> ``1002`` (plain split, no regex)."""
    head = matching_id.upper().partition("VEN_")[2]
    return head[:4].lower() if head else ""


def _device_id_from_matching(matching_id: str) -> str:
    head = matching_id.upper().partition("DEV_")[2]
    return head[:4].lower() if head else ""


def _vram_from_registry(value) -> float:
    """``HardwareInformation.qwMemorySize`` is a QWORD on current drivers and a
    little-endian byte blob on older ones."""
    try:
        if isinstance(value, (bytes, bytearray)):
            value = int.from_bytes(bytes(value), "little")
        return round(int(value) / _GB, 1)
    except (TypeError, ValueError):
        return 0.0


def _read_windows(winreg) -> tuple[HostGPU, ...]:
    gpus: list[HostGPU] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WIN_DISPLAY_CLASS) as cls:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(cls, index)
            except OSError:
                break
            index += 1
            if not sub.isdigit():
                continue  # "Configuration", "Properties"
            try:
                with winreg.OpenKey(cls, sub) as key:

                    def val(name, default=""):
                        try:
                            return winreg.QueryValueEx(key, name)[0]
                        except OSError:
                            return default

                    matching = str(val("MatchingDeviceId"))
                    vendor = _vendor_for(_vendor_id_from_matching(matching))
                    if vendor is None:
                        continue  # Basic Display, Hyper-V, remote-desktop adapters
                    name = str(val("DriverDesc") or val("HardwareInformation.AdapterString"))
                    vram = _vram_from_registry(val("HardwareInformation.qwMemorySize", 0))
                    gpus.append(HostGPU(
                        vendor=vendor,
                        name=name.strip() or f"{vendor.upper()} GPU",
                        vram_gb=vram,
                        pci_device_id=_device_id_from_matching(matching),
                        discrete=vram >= 2,
                    ))
            except OSError:
                continue
    return tuple(gpus)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _read_linux(drm_root: str = _LINUX_DRM) -> tuple[HostGPU, ...]:
    try:
        entries = sorted(os.listdir(drm_root))
    except OSError:
        return ()
    gpus: list[HostGPU] = []
    for entry in entries:
        # "card0" is a device; "card0-DP-1" / "renderD128" are connectors/nodes.
        if not entry.startswith("card") or not entry[4:].isdigit():
            continue
        dev = os.path.join(drm_root, entry, "device")
        vendor = _vendor_for(_read_text(os.path.join(dev, "vendor")))
        if vendor is None:
            continue
        device_id = _read_text(os.path.join(dev, "device")).removeprefix("0x").lower()
        name = _read_text(os.path.join(dev, "product_name")) or f"{vendor.upper()} GPU"
        vram = 0.0
        raw_vram = _read_text(os.path.join(dev, "mem_info_vram_total"))
        if raw_vram.isdigit():
            vram = round(int(raw_vram) / _GB, 1)
        # Intel's integrated GPU always sits at PCI 00:02.0; a card behind a
        # bridge (any other address) is a dedicated Arc part.
        bdf = os.path.basename(os.path.realpath(dev))
        gpus.append(HostGPU(
            vendor=vendor, name=name, vram_gb=vram, pci_device_id=device_id,
            discrete=vram >= 2 or (vendor == "intel" and bdf != _INTEL_IGPU_BDF),
        ))
    return tuple(gpus)


@functools.lru_cache(maxsize=1)
def detect_host_gpus() -> tuple[HostGPU, ...]:
    """Every PCI GPU from AMD/NVIDIA/Intel the OS knows about. Never raises.

    ``OMNIVOICE_DISABLE_GPU_INVENTORY=1`` returns ``()`` - the test suite sets it
    so a developer's own GPUs never change what a routing test resolves."""
    if os.environ.get("OMNIVOICE_DISABLE_GPU_INVENTORY", "").strip() in ("1", "true"):
        return ()
    try:
        if sys.platform == "win32":
            import winreg  # type: ignore[import-not-found]

            return _read_windows(winreg)
        if sys.platform.startswith("linux"):
            return _read_linux()
    except Exception:  # noqa: BLE001 - inventory is advisory, never fatal
        pass
    return ()


def refresh() -> tuple[HostGPU, ...]:
    """Clear the cache and re-read. **TEST-ONLY.**"""
    detect_host_gpus.cache_clear()
    return detect_host_gpus()


def discrete_candidates(gpus: tuple[HostGPU, ...]) -> tuple[HostGPU, ...]:
    """GPUs worth explaining: AMD/NVIDIA parts, or a dedicated Intel card (Arc).
    Plain Intel iGPUs are everywhere and not an acceleration target for any
    shipped engine, so they would only add noise."""
    return tuple(
        g for g in gpus
        if g.vendor in ("nvidia", "amd") or (g.vendor == "intel" and g.discrete)
    )


def pick_for_build(gpus: tuple[HostGPU, ...], build_kind: str | None) -> HostGPU | None:
    """The card a failed GPU start should be diagnosed against.

    On a Radeon + GeForce machine whose CUDA build found nothing, the NVIDIA
    path is the broken one; with a ROCm build it is the AMD one. Picking
    "the first discrete card" blamed the wrong vendor for half of mixed hosts.
    """
    cands = discrete_candidates(gpus)
    order = ("amd", "nvidia", "intel") if build_kind == "rocm" else ("nvidia", "amd", "intel")
    for vendor in order:
        for g in cands:
            if g.vendor == vendor:
                return g
    return None


__all__ = [
    "HostGPU", "GPUVendor", "detect_host_gpus", "discrete_candidates",
    "pick_for_build", "refresh",
]
