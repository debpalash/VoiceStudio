"""Package repair must not guess hardware or replace another ROCm family."""
import re
import sys
from importlib import metadata
from typing import Any

from .failures import VerificationFailure


def validate_repair_selection(recipe: dict[str, Any]) -> None:
    """Stop ambiguous repairs before downloads; healthy reuse stays unchanged."""
    if sys.platform != "win32":
        return
    from ..gpu_inventory import detect_host_gpus
    from .recipe import detected_rdna2_arch

    try:
        inventory = detect_host_gpus(require_complete=True)
    except Exception as exc:
        raise VerificationFailure("unsupported_gpu", "Complete GPU inventory is required before ROCm repair") from exc
    amd = [gpu for gpu in inventory if gpu.vendor == "amd"]
    if not amd or any(not re.fullmatch(r"[0-9a-f]{4}", gpu.pci_device_id.lower().removeprefix("0x")) for gpu in amd):
        raise VerificationFailure("unsupported_gpu", "Reliable AMD GPU inventory is required before ROCm repair")
    architecture = detected_rdna2_arch(inventory=inventory)
    if architecture and architecture not in recipe["gpu_architectures"]:
        raise VerificationFailure("unsupported_gpu", "GPU architecture differs from the selected ROCm recipe")
    selected = re.search(r"\+rocm(\d+)\.(\d+)", recipe["torch"]["packages"][0])
    if selected is None:
        raise VerificationFailure("unsupported_gpu", "Cannot identify selected ROCm family safely")
    family = selected.groups()
    library = "rocm-sdk-libraries" if family[0] == "10" else "rocm-sdk-libraries-custom"
    for name in ("torch", "rocm", "rocm-sdk-core", library):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        match = re.search(r"\+rocm(\d+)\.(\d+)", version) if name == "torch" else re.match(r"(\d+)\.(\d+)", version)
        if (match is None and (name != "torch" or "+rocm" in version)) or (match and match.groups() != family):
            raise VerificationFailure(
                "unsupported_gpu", "Installed ROCm family differs from this recipe; use a fresh runtime instead of automatic repair"
            )
