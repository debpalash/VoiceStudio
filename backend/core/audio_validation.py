"""Lightweight validation for persisted profile WAV references.

This module deliberately uses only the standard library. Gallery routers import
it during startup, so pulling in torch/torchaudio merely to validate a cached
file would make every Gallery open pay the model stack's import cost.
"""
from __future__ import annotations

import os
import struct
import wave
from pathlib import Path
from typing import Optional

from core.path_security import UnsafePath, resolve_within, safe_filename

_READ_CHUNK_BYTES = 1 << 20
_MAX_CHANNELS = 64
_MAX_SAMPLE_RATE = 768_000
_MAX_SAMPLE_WIDTH = 8


def resolve_regular_file(root: os.PathLike[str] | str, value: object) -> Optional[Path]:
    """Resolve a portable bare filename inside *root*, rejecting symlinks."""
    try:
        name = safe_filename(value)
        unresolved = Path(root).resolve(strict=False) / name
        if unresolved.is_symlink():
            return None
        return resolve_within(root, name)
    except (OSError, UnsafePath):
        return None


def _complete_wav_container(path: Path, file_size: int) -> bool:
    """Check declared RIFF chunks before decoders repair truncated lengths.

    SoundFile reports only physically available frames for an interrupted
    float WAV, so its adjusted frame count cannot prove the write completed.
    Walk fixed-size chunk headers without allocating their declared payloads.
    """
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[8:] != b"WAVE":
            return False
        if header[:4] not in (b"RIFF", b"RIFX"):
            return False
        endian = "<" if header[:4] == b"RIFF" else ">"
        end = struct.unpack(endian + "I", header[4:8])[0] + 8
        if end > file_size or end < 12:
            return False
        offset = 12
        while offset < end:
            stream.seek(offset)
            chunk = stream.read(8)
            if len(chunk) != 8 or offset + 8 > end:
                return False
            size = struct.unpack(endian + "I", chunk[4:])[0]
            payload_end = offset + 8 + size
            if payload_end > end:
                return False
            # Some PCM writers omit padding on an odd final data chunk.
            if payload_end == end:
                return True
            offset = payload_end + (size & 1)
        return offset == end


def is_playable_wav(path: Optional[Path]) -> bool:
    """Return true only for a regular, decodable WAV with audio frames."""
    if path is None:
        return False
    try:
        if not path.is_file() or path.is_symlink():
            return False
        file_size = path.stat().st_size
        if not _complete_wav_container(path, file_size):
            return False
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            frame_count = wav.getnframes()
            if (
                not 0 < channels <= _MAX_CHANNELS
                or not 0 < sample_rate <= _MAX_SAMPLE_RATE
                or not 0 < sample_width <= _MAX_SAMPLE_WIDTH
                or frame_count <= 0
            ):
                return False
            # ``wave.getnframes`` trusts the header. Read through the declared
            # payload so an interrupted write with a complete header but a
            # truncated data chunk cannot masquerade as playable audio.
            frame_size = channels * sample_width
            expected_bytes = frame_count * frame_size
            # A PCM payload cannot be larger than the containing file. Check
            # before calling ``readframes`` so hostile header values cannot
            # turn a tiny file into a multi-gigabyte allocation request.
            if expected_bytes > file_size:
                return False
            read_bytes = 0
            chunk_frames = max(1, min(frame_count, _READ_CHUNK_BYTES // frame_size))
            while read_bytes < expected_bytes:
                chunk = wav.readframes(chunk_frames)
                if not chunk or len(chunk) % frame_size:
                    return False
                read_bytes += len(chunk)
            return read_bytes == expected_bytes
    except (MemoryError, OSError, EOFError, OverflowError, wave.Error):
        # Python 3.11's wave module rejects valid IEEE-float/WAVE_EXTENSIBLE
        # files. SoundFile is already a runtime dependency and recognizes those
        # containers; import it only on the uncommon fallback path.
        try:
            import soundfile as sf

            with sf.SoundFile(str(path)) as audio:
                if (
                    audio.format != "WAV"
                    or not 0 < audio.channels <= _MAX_CHANNELS
                    or not 0 < audio.samplerate <= _MAX_SAMPLE_RATE
                    or len(audio) <= 0
                ):
                    return False
                remaining = len(audio)
                # Decode through the declared payload in byte-bounded chunks;
                # ``sf.info`` alone also trusts a truncated file's header.
                chunk_frames = max(
                    1, _READ_CHUNK_BYTES // (audio.channels * 4),
                )
                while remaining:
                    frames = audio.read(
                        min(remaining, chunk_frames), dtype="float32", always_2d=True,
                    )
                    count = len(frames)
                    if count <= 0:
                        return False
                    remaining -= count
                return True
        except Exception:
            return False


__all__ = ["is_playable_wav", "resolve_regular_file"]
