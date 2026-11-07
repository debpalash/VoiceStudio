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
