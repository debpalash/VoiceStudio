"""Metadata-only isolation policy; safe for engine and pipeline admission."""
import sys
from importlib import metadata


def isolation_required():
    """Keep Windows ROCm 10 inference away from Torch's OpenMP runtime."""
    if sys.platform != "win32":
        return False
    try:
        return "+rocm10." in metadata.version("torch").lower()
    except metadata.PackageNotFoundError:
        return False


ARGOS_UNAVAILABLE = (
    "Offline Argos translation is unavailable in the Windows ROCm 10 environment: "
    "it runs CTranslate2 inside the backend process, and Torch and CTranslate2 use "
    "incompatible OpenMP runtimes there. Choose NLLB (local) or an online provider "
    "in the Engine dropdown. See docs/install/windows-rocm.md."
)


def argos_error():
    """Refuse Argos before `argostranslate.translate` loads CTranslate2 beside Torch."""
    return ARGOS_UNAVAILABLE if isolation_required() else None
