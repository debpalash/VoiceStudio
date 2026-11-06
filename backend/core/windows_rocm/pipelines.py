"""Bound unverified Windows HIP MIOpen pipelines without disabling CPU use."""
from .ct2 import windows_hip_installed


class PipelineCompatibilityError(ImportError):
    """Retryable device incompatibility, never a broken Python dependency."""


def whisperx_error(device=None):
    """WhisperX shares Torch's process; ROCm 10 CT2 CPU fallback is unsafe."""
    from .isolation_policy import isolation_required

    if isolation_required():
        return (
            "WhisperX transcription is unavailable in the Windows ROCm 10 environment: "
            "Torch and CTranslate2 use incompatible OpenMP runtimes. Use faster-whisper "
            "(isolated automatically) or PyTorch Whisper for transcription. CPU-only "
            "wav2vec2 alignment and pyannote are separate pipelines. "
            "See docs/install/windows-rocm.md."
        )
    return pipeline_error(device)


def pipeline_error(device=None):
    """Return an actionable gate before imports/cache use, without caching failure."""
    if not windows_hip_installed():
        return None
    if device is None:
        from core.device_caps import detect_host_caps

        device = detect_host_caps().family
    if str(device) == "cpu":
        return None
    return (
        "WhisperX, wav2vec2 alignment and pyannote GPU execution are not verified "
        "on Windows ROCm (MIOpen Unicode-path/SDK-header compatibility). "
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
