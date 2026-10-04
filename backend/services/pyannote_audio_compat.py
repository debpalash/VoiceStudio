"""Restore pyannote 3.x's soundfile boundary without replacing tensor operations."""

from functools import wraps
from numbers import Integral
import re
from threading import RLock
from typing import NamedTuple


_INSTALL_LOCK = RLock()


class AudioMetaData(NamedTuple):
    """The five fields consumed by pyannote's legacy audio reader."""

    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


def _soundfile_info(uri, format=None, buffer_size=4096, backend=None):
    """Read legacy metadata, including floating-point width and FLAC encoding."""
    import soundfile

    if backend not in (None, "soundfile"):
        raise ValueError(f"Unsupported legacy audio backend: {backend}")
    details = soundfile.info(uri)
    subtype = details.subtype
    fixed_width = re.fullmatch(r"(?:PCM|DWVW|DPCM|ALAC)_(\d+)", subtype)
    bits = int(fixed_width.group(1)) if fixed_width else {
        "PCM_U8": 8, "PCM_S8": 8, "FLOAT": 32, "DOUBLE": 64,
        "ULAW": 8, "ALAW": 8,
    }.get(subtype, 0)
    if details.format == "FLAC":
        encoding = "FLAC"
    elif subtype == "PCM_U8":
        encoding = "PCM_U"
    elif subtype.startswith("PCM_"):
        encoding = "PCM_S"
    elif subtype in ("FLOAT", "DOUBLE"):
        encoding = "PCM_F"
    else:
        encoding = subtype if subtype in ("ULAW", "ALAW", "VORBIS") else "UNKNOWN"
    return AudioMetaData(
        details.samplerate, details.frames, details.channels, bits, encoding,
    )


def _soundfile_load(
    uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True,
    format=None, buffer_size=4096, backend=None,
):
    """Decode only the requested frames through soundfile, never TorchCodec."""
    import soundfile
    import torch

    if backend not in (None, "soundfile"):
        raise ValueError(f"Unsupported legacy audio backend: {backend}")
    if not isinstance(frame_offset, Integral) or frame_offset < 0:
        raise ValueError("frame_offset must be a nonnegative integer")
    if not isinstance(num_frames, Integral) or num_frames < -1:
        raise ValueError("num_frames must be -1 or a nonnegative integer")
    with soundfile.SoundFile(uri) as source:
        sample_type = "float32"
        unsigned_pcm = False
        if source.format == "WAV" and not normalize:
            sample_type = {
                "PCM_16": "int16", "PCM_24": "int32", "PCM_32": "int32",
                "PCM_U8": "int16", "FLOAT": "float32", "DOUBLE": "float64",
            }.get(source.subtype)
            if sample_type is None:
                raise ValueError(f"Unsupported unnormalized WAV subtype: {source.subtype}")
            unsigned_pcm = source.subtype == "PCM_U8"
        start = min(int(frame_offset), source.frames)
        if source.seekable() or frame_offset:
            source.seek(start)
        remaining = source.frames - start
        frames = remaining if num_frames == -1 else min(int(num_frames), remaining)
        samples = source.read(frames=frames, dtype=sample_type, always_2d=True)
        sample_rate = source.samplerate
    waveform = torch.from_numpy(samples)
    if unsigned_pcm:
        waveform = ((waveform.to(torch.int32) >> 8) + 128).to(torch.uint8)
    if channels_first:
        waveform = waveform.transpose(0, 1)
    return waveform, sample_rate


def _soundfile_backends():
    """Advertise the decoder implemented by the compatibility boundary."""
    return ["soundfile"]


def ensure_pyannote_audio_compat() -> bool:
    """Fill retired APIs and route explicit soundfile loads; preserve old stacks.

    Call before importing pyannote 3.x. Default/other torchcodec loads and all
    tensor operations remain untouched. A complete older API is a strict no-op.
    """
    import torchaudio

    with _INSTALL_LOCK:
        replacements = {
            "AudioMetaData": AudioMetaData,
            "info": _soundfile_info,
            "list_audio_backends": _soundfile_backends,
        }
        missing = {name: value for name, value in replacements.items() if not hasattr(torchaudio, name)}
        if not missing:
            return False
        import soundfile

        if not callable(getattr(soundfile, "SoundFile", None)):
            raise RuntimeError("The pyannote audio adapter requires soundfile")
        original_load = torchaudio.load
        if not callable(original_load):
            raise RuntimeError("The torchaudio loader is unavailable")
        if not getattr(original_load, "_voicestudio_soundfile_compat", False):
            @wraps(original_load)
            def load(*args, **kwargs):
                backend = kwargs.get("backend", args[7] if len(args) > 7 else None)
                if backend == "soundfile":
                    return _soundfile_load(*args, **kwargs)
                return original_load(*args, **kwargs)

            load._voicestudio_soundfile_compat = True
            torchaudio.load = load
        for name, value in missing.items():
            setattr(torchaudio, name, value)
        return True
