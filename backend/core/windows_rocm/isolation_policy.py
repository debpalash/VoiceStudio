"""Metadata-only isolation policy; safe for engine and pipeline admission."""
import sys
from importlib import metadata


def isolation_required():
    """Keep Windows ROCm CT2 inference away from Torch's OpenMP runtime.

    Every Windows HIP Torch build (ROCm 7.2.1 and ROCm 10) loads LLVM
    ``libomp140`` while CTranslate2 loads Intel ``libiomp5md``; with both in
    the API process a repeated faster-whisper run deadlocked the event loop.
    """
    if sys.platform != "win32":
        return False
    try:
        return "+rocm" in metadata.version("torch").lower()
    except metadata.PackageNotFoundError:
        return False


ARGOS_UNAVAILABLE = (
    "Offline Argos translation is unavailable in the Windows ROCm environment: "
    "it runs CTranslate2 inside the backend process, and Torch and CTranslate2 use "
    "incompatible OpenMP runtimes there. Choose NLLB (local) or an online provider "
    "in the Engine dropdown. See docs/install/windows-rocm.md."
)


def argos_error():
    """Refuse Argos before `argostranslate.translate` loads CTranslate2 beside Torch."""
    return ARGOS_UNAVAILABLE if isolation_required() else None
