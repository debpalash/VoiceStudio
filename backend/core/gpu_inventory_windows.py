"""Windows adapter enumeration shared by advisory inventory and safe repair."""
from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gpu_inventory import HostGPU

def read_windows(winreg: ModuleType, *, api: ModuleType, require_complete: bool = False) -> tuple[HostGPU, ...]:
    """Read registry adapters; provisioning rejects partial identity reads."""
    gpus = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, api._WIN_DISPLAY_CLASS) as cls:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(cls, index)
            except OSError as exc:
                if require_complete and getattr(exc, "winerror", None) != 259:
                    raise
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
                            if require_complete and name == "MatchingDeviceId":
                                raise
                            return default

                    matching = str(val("MatchingDeviceId"))
                    if require_complete and not matching.strip():
                        raise OSError("Graphics adapter identity is unavailable")
                    vendor = api._vendor_for(api._vendor_id_from_matching(matching))
                    if vendor is None:
                        continue  # Basic Display, Hyper-V, remote-desktop adapters
                    name = str(val("DriverDesc") or val("HardwareInformation.AdapterString"))
                    vram = api._vram_from_registry(val("HardwareInformation.qwMemorySize", 0))
                    gpus.append(api.HostGPU(
                        vendor=vendor,
                        name=name.strip() or f"{vendor.upper()} GPU",
                        vram_gb=vram,
                        pci_device_id=api._device_id_from_matching(matching),
                        discrete=vram >= 2,
                    ))
            except OSError:
                if require_complete:
                    raise
                continue
    return tuple(gpus)
