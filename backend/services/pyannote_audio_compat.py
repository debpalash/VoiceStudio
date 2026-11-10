"""Compatibility surface for pyannote.audio on recent torchaudio releases.

torchaudio 2.9 removed ``info`` and ``list_audio_backends`` and routes ``load``
through TorchCodec only. Once installed, this module restores the removed
entry points and makes ``torchaudio.load`` fall back to soundfile whenever
TorchCodec is unavailable, for every caller in the process.
"""

from functools import wraps
from numbers import Integral
from threading import RLock
from typing import NamedTuple


class AudioMetaData(NamedTuple):
    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


_INSTALL_LOCK = RLock()
_WRAPPER_MARKER = "_voicestudio_pyannote_soundfile_adapter"


def _sample_width(subtype: str) -> int:
    for prefix in ("PCM_", "DWVW_", "DPCM_", "ALAC_"):
        if subtype.startswith(prefix) and subtype[len(prefix):].isdigit():
            return int(subtype[len(prefix):])
    if subtype in {"PCM_U8", "PCM_S8", "ULAW", "ALAW"}:
        return 8
    if subtype == "FLOAT":
        return 32
    if subtype == "DOUBLE":
        return 64
    return 0


def _legacy_encoding(container: str, subtype: str) -> str:
    if container == "FLAC":
        return "FLAC"
    if subtype == "PCM_U8":
        return "PCM_U"
    if subtype.startswith("PCM_"):
        return "PCM_S"
    if subtype in {"FLOAT", "DOUBLE"}:
        return "PCM_F"
    if subtype in {"ULAW", "ALAW", "VORBIS"}:
        return subtype
    return "UNKNOWN"


def _soundfile_info(uri, format=None, buffer_size=4096, backend=None):
    """Describe an audio file the way legacy ``torchaudio.info`` did.

    ``format`` and ``buffer_size`` exist only so old call sites keep working;
    soundfile works both out on its own.
    """
    del format, buffer_size
    if backend not in (None, "soundfile"):
        raise ValueError(f"This adapter cannot read metadata with backend {backend!r}")

    import soundfile

    details = soundfile.info(uri)
    subtype = details.subtype or ""
    container = details.format or ""
    return AudioMetaData(
        int(details.samplerate),
        int(details.frames),
        int(details.channels),
        _sample_width(subtype),
        _legacy_encoding(container, subtype),
    )


# Read dtype used for unnormalised WAV input, keyed by soundfile subtype.
# Unsigned 8-bit has no soundfile dtype, so it is read as int16 and unpacked.
_NATIVE_WAV_DTYPES = {
    "PCM_16": "int16",
    "PCM_24": "int32",
    "PCM_32": "int32",
    "FLOAT": "float32",
    "DOUBLE": "float64",
    "PCM_U8": "int16",
}


def _soundfile_load(
    uri,
    frame_offset=0,
    num_frames=-1,
    normalize=True,
    channels_first=True,
    format=None,
    buffer_size=4096,
    backend=None,
):
    """Decode audio through soundfile with the legacy ``torchaudio.load`` contract.

    Returns ``(waveform, sample_rate)``. ``format`` and ``buffer_size`` are
    accepted but have no effect.
    """
    del format, buffer_size
    if backend not in (None, "soundfile"):
        raise ValueError(f"This adapter cannot decode audio with backend {backend!r}")
    if not isinstance(frame_offset, Integral) or frame_offset < 0:
        raise ValueError(f"frame_offset must be a non-negative integer, got {frame_offset!r}")
    if not isinstance(num_frames, Integral) or num_frames < -1:
        raise ValueError(f"num_frames must be an integer of at least -1, got {num_frames!r}")

    import soundfile
    import torch

    with soundfile.SoundFile(uri) as handle:
        subtype = handle.subtype
        native = handle.format == "WAV" and not normalize
        if native:
            try:
                dtype = _NATIVE_WAV_DTYPES[subtype]
            except KeyError as exc:
                raise ValueError(
                    f"Unnormalised loading is not available for WAV subtype {subtype!r}"
                ) from exc
        else:
            dtype = "float32"

        total = int(handle.frames)
        start = min(int(frame_offset), total)
        if handle.seekable() or start:
            handle.seek(start)
        remaining = total - start
        count = remaining if num_frames == -1 else min(int(num_frames), remaining)
        samples = handle.read(frames=count, dtype=dtype, always_2d=True)
        sample_rate = int(handle.samplerate)

    waveform = torch.from_numpy(samples)
    if native and subtype == "PCM_U8":
        waveform = ((waveform.to(torch.int32) >> 8) + 128).to(torch.uint8)
    if channels_first:
        waveform = waveform.transpose(0, 1)
    return waveform, sample_rate


def _torchcodec_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` says TorchCodec (or its FFmpeg libraries) is missing."""
    text = str(exc).lower()
    if isinstance(exc, ImportError):
        return "torchcodec" in text or "torchcodec" in str(getattr(exc, "name", "")).lower()
    return "could not load libtorchcodec" in text


def _rewind(source) -> None:
    """Return a file-like source to its start after a failed decode attempt."""
    seek = getattr(source, "seek", None)
    if callable(seek):
        try:
            seek(0)
        except (OSError, ValueError):
            # A non-seekable stream: the next reader gets what is left, which
            # is the best any decoder can do with it.
            return


def _backends():
    """Report the only decoding backend this adapter provides."""
    return ["soundfile"]


def ensure_pyannote_audio_compat() -> bool:
    """Install the legacy audio entry points needed by pyannote.audio 3.x."""
    import torchaudio

    with _INSTALL_LOCK:
        legacy_names = ("AudioMetaData", "info", "list_audio_backends")
        if all(hasattr(torchaudio, name) for name in legacy_names):
            return False

        import soundfile

        if not callable(getattr(soundfile, "SoundFile", None)):
            raise TypeError("The pyannote audio adapter needs a callable soundfile.SoundFile")
        current_load = getattr(torchaudio, "load", None)
        if not callable(current_load):
            raise TypeError("The torchaudio loader is unavailable to the pyannote audio adapter")

        if not getattr(current_load, _WRAPPER_MARKER, False):
            original_load = current_load

            @wraps(original_load)
            def compatible_load(*args, **kwargs):
                requested_backend = kwargs.get("backend")
                if "backend" not in kwargs and len(args) > 7:
                    requested_backend = args[7]
                if requested_backend == "soundfile":
                    return _soundfile_load(*args, **kwargs)
                try:
                    return original_load(*args, **kwargs)
                except (ImportError, RuntimeError) as exc:
                    # torchaudio >= 2.9 decodes only through TorchCodec, which
                    # VoiceStudio does not install (it needs FFmpeg shared
                    # libraries). Every caller in the process — pyannote,
                    # transformers, our own code — then gets the soundfile
                    # decoder instead of an ImportError. Any other failure is
                    # a real decode error and propagates.
                    if not _torchcodec_unavailable(exc) or requested_backend is not None:
                        raise
                    source = args[0] if args else kwargs.get("uri")
                    _rewind(source)
                    try:
                        return _soundfile_load(*args, **kwargs)
                    except (RuntimeError, OSError, ValueError):
                        # libsndfile cannot decode every container (M4A, AAC).
                        # Re-raise the TorchCodec error so callers that handle
                        # it (services.audio_io falls back to FFmpeg) still do.
                        _rewind(source)
                        raise exc from None

            setattr(compatible_load, _WRAPPER_MARKER, True)
            torchaudio.load = compatible_load

        replacements = {
            "AudioMetaData": AudioMetaData,
            "info": _soundfile_info,
            "list_audio_backends": _backends,
        }
        for name, replacement in replacements.items():
            if not hasattr(torchaudio, name):
                setattr(torchaudio, name, replacement)
        return True
