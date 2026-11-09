"""Admit only the pinned Windows HIP binary to CTranslate2 GPU execution."""
import sys
import time
from importlib import metadata, util
from threading import RLock

from .artifacts import sha256
from .recipe import expected_versions, load_recipe, recipe_identity, validate_target
from .runtime import initialize_dll_search, package_root
from .verify import verify_environment

_VERIFIED = set()
_FAILED = {}
_VERIFY_LOCK = RLock()


def windows_hip_installed():
    """Metadata only: never import Torch while preparing its native loader."""
    if sys.platform != "win32":
        return False
    try:
        return "+rocm" in metadata.version("torch").lower()
    except metadata.PackageNotFoundError:
        return False


def hip_ct2_available():
    """Local binary integrity plus device/float16 discovery, not ASR proof.

    The caller separately applies the user's effective compute-device override.
    No model access, driver change or network request occurs. Native model-free
    checks (including Torch arithmetic) execute only in the bounded child.
    """
    if not windows_hip_installed():
        return False
    try:
        validate_target()
        recipe = load_recipe()
        pins = expected_versions(recipe)
        if {name: metadata.version(name) for name in pins} != pins:
            return False
        root = package_root("ctranslate2")
        if root is None:
            return False
        binary = root / "ctranslate2.dll"
        digest = sha256(binary)
        if digest != recipe["ctranslate2"]["binary_sha256"]:
            return False
        identity = (recipe_identity(), sys.executable, tuple(pins.items()), digest)
        with _VERIFY_LOCK:
            if identity not in _VERIFIED:
                if identity in _FAILED:
                    # A failed native probe can take up to 90 s and runs from
                    # catalogue refreshes; repeating it on every refresh would
                    # stall the UI. It is retried after a restart, a recipe or
                    # package change (new identity), or a backend restart (setup rerun).
                    return False
                # Never discover native GPUs in a catalogue/UI probe. Timeout,
                # crash and invalid/CPU reports all fail in the bounded child.
                try:
                    verify_environment()
                except Exception:  # noqa: BLE001 - retryable, content-free native failure.
                    _FAILED[identity] = time.monotonic()
                    return False
                _FAILED.pop(identity, None)
                _VERIFIED.add(identity)
        return True
    except Exception:  # noqa: BLE001 - broken metadata/child verification never authorizes HIP.
        return False


def gpu_compatible():
    """Hardware families admitted by this installed CT2, for honest routing."""
    from core.device_caps import detect_host_caps

    if detect_host_caps().family != "rocm":
        return ("cuda", "cpu")
    return ("cuda", "rocm", "cpu") if hip_ct2_available() else ("cuda", "cpu")


def prepare_import():
    """Register DLL directories in the actual importing process, for its lifetime."""
    initialize_dll_search()


def availability_status():
    """Windows HIP availability is child-only; generic/explicit CPU paths stay old."""
    if not windows_hip_installed():
        return None
    from core.device_caps import detect_host_caps

    if detect_host_caps().family != "rocm":
        return None
    if not util.find_spec("faster_whisper"):
        return False, "faster-whisper is not installed"
    if not hip_ct2_available():
        return False, (
            "Windows HIP CTranslate2 verification failed. Rerun ROCm setup or "
            "select CPU in Settings > Performance. See docs/install/windows-rocm.md."
        )
    return True, "Windows HIP CT2 discovery verified; local ASR inference is a separate check"


def gpu_device_available():
    """The isolated worker shares the parent's CPU override and binary gate."""
    from core.device_caps import detect_host_caps

    family = detect_host_caps().family
    return family == "cuda" or (family == "rocm" and hip_ct2_available())
