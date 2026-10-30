"""Admit only registered Windows HIP pipeline capabilities, preserving CPU use."""
import sys
from importlib import metadata

from .ct2 import windows_hip_installed
from .recipe import expected_versions, load_recipe, recipe_identity, validate_target

WHISPERX_ASR = "whisperx-asr"
WHISPERX_ALIGNMENT = "whisperx-alignment"
_WHISPERX_NATIVE_PROFILE = {
    "recipe_sha256": "ead2dabccecf271ab2e66bd369e86f392ddd6feb3d214f22dad0470aecd6714f",
    "packages": {"whisperx": "3.4.5", "pyannote.audio": "3.4.0"},
    "gpu_architecture": "gfx1201",
}


class PipelineCompatibilityError(ImportError):
    """Retryable device incompatibility, never a broken Python dependency."""


def _whisperx_profile_error():
    """One current profile for availability, execution and runtime GPU reporting."""
    from .ct2 import hip_ct2_available
    from .runtime import miopen_profile_error

    try:
        validate_target()
        if recipe_identity() != _WHISPERX_NATIVE_PROFILE["recipe_sha256"]:
            return "The Windows ROCm recipe has no registered WhisperX qualification"
        recipe = load_recipe()
        pins = expected_versions(recipe) | _WHISPERX_NATIVE_PROFILE["packages"]
        if any(metadata.version(name) != version for name, version in pins.items()):
            return "Installed native/WhisperX versions differ from the registered profile"
        if reason := miopen_profile_error():
            return reason
        torch = sys.modules.get("torch")
        if (torch is None or str(torch.__version__) != pins["torch"]
                or not str(torch.version.hip).startswith(recipe["torch"]["hip_prefix"])
                or not torch.cuda.is_available()):
            return "The registered HIP GPU is not available in this process"
        architecture = str(torch.cuda.get_device_properties(0).gcnArchName).split(":", 1)[0]
        if architecture != _WHISPERX_NATIVE_PROFILE["gpu_architecture"]:
            return "This GPU architecture has no registered WhisperX qualification"
        if not hip_ct2_available():
            return "Pinned HIP CTranslate2 binary/readiness verification failed"
        return None
    except Exception as error:
        return f"Native WhisperX profile could not be qualified ({type(error).__name__})"


def pipeline_error(device=None, *, capability=None):
    """Return an actionable gate before imports/cache use, without caching failure."""
    if not windows_hip_installed():
        return None
    if device is None:
        from core.device_caps import detect_host_caps

        device = detect_host_caps().family
    if str(device) == "cpu":
        return None
    reason = "This capability is unqualified; pyannote speaker diarization and unknown pipelines remain gated"
    if capability in (WHISPERX_ASR, WHISPERX_ALIGNMENT) and str(device) in ("cuda", "rocm"):
        reason = _whisperx_profile_error()
        if reason is None:
            return None
    return (
        f"Windows ROCm MIOpen pipeline qualification failed: {reason}. "
        "Select CPU in Settings > Performance (OMNIVOICE_DEVICE=cpu) for these "
        "pipelines, or use faster-whisper for basic ASR. Main runtime readiness "
        "does not certify these optional pipelines. See docs/install/windows-rocm.md."
    )


def prepare_audio_imports():
    """Keep native DLL handles alive before torchaudio/pyannote imports."""
    from .runtime import initialize_dll_search

    initialize_dll_search()
    from importlib import metadata

    try:
        version = metadata.version("torchaudio").split("+", 1)[0].split(".")
        if tuple(map(int, version[:2])) < (2, 9):
            return
    except (metadata.PackageNotFoundError, ValueError):
        return
    from services.pyannote_audio_compat import ensure_pyannote_audio_compat

    ensure_pyannote_audio_compat()
