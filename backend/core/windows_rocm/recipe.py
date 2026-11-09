"""The same checked-in Windows recipe is consumed by every bootstrap path."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import sysconfig
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from collections.abc import Iterable
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..gpu_inventory import HostGPU

from .failures import VerificationFailure
from .ct2_recipe import ct2_pinned, validate_ct2_recipe

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ALLOWED_SOURCES = frozenset({
    "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/",
    "https://stable.repo.amd.com/rocm/whl-next/",
})

# AMD's rocminfo PCI-to-gfx mapping: https://github.com/ROCm/rocminfo/blob/amd-staging_deprecated/rocm_agent_enumerator
# gfx1010 is the RDNA 1 part this overlay has hardware evidence for (Navi 10:
# RX 5700 / 5700 XT / 5600 XT, which all enumerate as 0x731f) and it ships its own
# pinned device wheel on the same index. gfx1011 (Navi 12) and gfx1012 (Navi 14,
# the RX 5500 XT class) have device wheels there too, but no such card was
# available to verify them, so they stay out: an unlisted card keeps CPU routing
# instead of an unproven GPU path.
_RDNA_PCI_IDS = {
    "gfx1010": frozenset("7310 7312 7318 7319 731a 731b 731e 731f".split()),
    "gfx1030": frozenset("73a0 73a1 73a2 73a3 73a5 73a8 73a9 73ab 73ac 73ad 73ae 73af 73bf".split()),
    "gfx1031": frozenset("73c0 73c1 73c3 73da 73db 73dc 73dd 73de 73df".split()),
    "gfx1032": frozenset("73e0 73e1 73e2 73e3 73e8 73e9 73ea 73eb 73ec 73ed 73ef 73ff".split()),
}

RECIPE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "windows-rocm-recipe.json"


def detected_rdna_arch(*, inventory: Iterable[HostGPU] | None = None) -> str | None:
    """Select the native overlay from PCI metadata, before Torch is installed."""
    if sys.platform != "win32":
        return None
    from ..gpu_inventory import detect_host_gpus

    gpus = detect_host_gpus() if inventory is None else inventory
    amd = [gpu for gpu in gpus if gpu.vendor == "amd"]
    arches = [next((arch for arch, ids in _RDNA_PCI_IDS.items()
                    if gpu.pci_device_id.lower().removeprefix("0x") in ids), None) for gpu in amd]
    found = {arch for arch in arches if arch}
    if found and (len(found) != 1 or any(arch is None for arch in arches)):
        raise VerificationFailure("unsupported_gpu", "Multiple AMD architectures cannot be selected safely for Windows ROCm")
    return next(iter(found), None)


def _validate_artifacts(section):
    """Reject unsafe artifact paths and incomplete or malformed SHA-256 pins."""
    hashes = section.get("wheel_sha256") or {}
    files = section["wheel_files"]
    if set(files) != set(hashes):
        raise RuntimeError("Windows ROCm recipe has missing or extra sha256 pins")
    for name in files:
        decoded = unquote(name)
        path = PurePosixPath(decoded)
        if (path.is_absolute() or ".." in path.parts
                or not re.fullmatch(r"[A-Za-z0-9_./+%-]+", name)
                or not re.fullmatch(r"[A-Za-z0-9_./+-]+", decoded)):
            raise RuntimeError("Windows ROCm recipe has an unsafe artifact path")
        if not _SHA256.fullmatch(str(hashes.get(name, ""))):
            raise RuntimeError(f"Windows ROCm recipe has no valid sha256 for {name}")


def load_recipe(path: str | Path | None = None, *, architecture: str | None = None) -> dict[str, Any]:
    """Load, validate, and select an overlay without network or native imports."""
    recipe = json.loads(Path(path or RECIPE_PATH).read_text(encoding="utf-8"))
    if recipe["schema_version"] != 1:
        raise RuntimeError("Unsupported Windows ROCm recipe schema")
    variant = recipe["rocm10"]
    validate_ct2_recipe(recipe["ctranslate2"])
    validate_ct2_recipe(variant["ctranslate2"], allow_unpinned=True)
    if not set(variant["ctranslate2"]["gpu_architectures"]) <= set(variant["devices"]):
        raise RuntimeError("Windows ROCm recipe pins a CT2 build for an undeclared device")
    for torch in (recipe["torch"], variant["torch"]):
        if torch["find_links"] not in _ALLOWED_SOURCES:
            raise RuntimeError("Windows ROCm recipe has an unapproved source")
    for section in (recipe["torch"], recipe["runtime"], variant["torch"], variant["runtime"],
                    *variant["devices"].values()):
        _validate_artifacts(section)
    arch = architecture if architecture is not None else detected_rdna_arch()
    if arch in variant["devices"]:
        device = variant["devices"][arch]
        common = variant["runtime"]
        selected_runtime = {key: common[key] + device[key] for key in ("packages", "wheel_files")}
        selected_runtime["wheel_sha256"] = common["wheel_sha256"] | device["wheel_sha256"]
        return {**recipe, "gpu_architectures": [arch], "torch": variant["torch"],
                "runtime": selected_runtime, "ctranslate2": _device_ct2(variant["ctranslate2"], arch)}
    return recipe


def _device_ct2(config, arch):
    """The overlay's HIP CTranslate2 only for the architectures it was built and verified for.

    The ROCm 7.2.1 HIP binary loads on ROCm 10 but launches no kernel
    (hipErrorInvalidImage on gfx1010), so any other overlay device gets an
    explicit "unsupported" entry: no CT2 is downloaded or verified, and every
    CT2-backed path (ASR, diarization) stays on CPU instead of claiming GPU
    readiness it never proved. A build listed for this device but not yet
    published (pins unset) is treated the same way.
    """
    if arch not in config["gpu_architectures"]:
        return {"supported": False,
                "reason": f"No verified ROCm 10 HIP CTranslate2 covers {arch}; ASR and diarization stay on CPU."}
    if not ct2_pinned(config):
        # The in-repo build is not published and pinned yet: same CPU path as gfx1010.
        return {"supported": False,
                "reason": "The ROCm 10 HIP CTranslate2 build is not published yet; ASR and diarization stay on CPU."}
    return {key: value for key, value in config.items() if key != "gpu_architectures"}


def recipe_identity(path=None):
    """Hash actual bytes so packaged readiness changes with any recipe edit."""
    return hashlib.sha256(Path(path or RECIPE_PATH).read_bytes()).hexdigest()


def validate_target(platform=None, version=None, architecture=None):
    """Refuse incompatible interpreters before modifying the environment."""
    if (
        (platform or sys.platform) != "win32"
        or tuple((version or sys.version_info)[:2]) != (3, 12)
        or (architecture or sysconfig.get_platform()) != "win-amd64"
    ):
        raise VerificationFailure(
            "unsupported_target", "Windows ROCm requires Windows x64 Python 3.12 (cp312); select that interpreter first"
        )


def ct2_supported(recipe):
    """Whether the selected recipe pins a verified HIP CTranslate2 build."""
    return (recipe.get("ctranslate2") or {}).get("supported", True)


def expected_versions(recipe):
    pins = recipe["torch"]["packages"] + recipe["runtime"]["packages"]
    pinned = dict(pin.split("==", 1) for pin in pins)
    # A device without a verified HIP CT2 must not pin whatever CPU build the
    # environment happens to carry: its version is the sync's business, not ours.
    return pinned | {"ctranslate2": recipe["ctranslate2"]["version"]} if ct2_supported(recipe) else pinned
