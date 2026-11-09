"""The same checked-in Windows recipe is consumed by every bootstrap path."""

import hashlib
import json
import re
import sys
import sysconfig
from pathlib import Path

from .failures import VerificationFailure

_SHA256 = re.compile(r"[0-9a-f]{64}")

RECIPE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "windows-rocm-recipe.json"


def load_recipe(path=None):
    """Load packaged/source recipe bytes; no network or native imports."""
    recipe = json.loads(Path(path or RECIPE_PATH).read_text(encoding="utf-8"))
    if recipe["schema_version"] != 1:
        raise RuntimeError("Unsupported Windows ROCm recipe schema")
    # Every AMD artifact is pinned by content, not just by name: a version pin
    # alone would install whatever bytes the host serves under that filename.
    for section in ("torch", "runtime"):
        hashes = recipe[section].get("wheel_sha256") or {}
        for name in recipe[section]["wheel_files"]:
            if not _SHA256.fullmatch(str(hashes.get(name, ""))):
                raise RuntimeError(f"Windows ROCm recipe has no valid sha256 for {name}")
    return recipe


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


def expected_versions(recipe):
    pins = recipe["torch"]["packages"] + recipe["runtime"]["packages"]
    return dict(pin.split("==", 1) for pin in pins) | {"ctranslate2": recipe["ctranslate2"]["version"]}
