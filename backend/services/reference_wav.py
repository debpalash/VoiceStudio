"""Hand sidecar engines a reference clip they can all read (#3688).

Profiles keep the reference in whatever container the user uploaded (M4A from
a phone, MP3, FLAC, float or WAVE_FORMAT_EXTENSIBLE WAV). Sidecar venvs carry
only their engine's own dependencies: PocketTTS reads ``.wav`` with the stdlib
``wave`` module (integer PCM, format tag 1 only) and needs ``soundfile`` for
anything else. Decoding once here, where ffmpeg and the full fallback chain
live, keeps every engine venv free of extra audio dependencies.
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
import tempfile

logger = logging.getLogger("omnivoice.reference_wav")

#: Converted clips kept on disk. A stable name per source lets sidecars that
#: cache voice state by path (PocketTTS) reuse it across generations.
MAX_CACHED_REFERENCES = 32


def _cache_dir() -> str:
    from core.config import DATA_DIR

    return os.path.join(DATA_DIR, "cache", "reference_wav")


def is_plain_pcm_wav(path: str) -> bool:
    """True for a RIFF/WAVE file whose ``fmt`` chunk is integer PCM (tag 1).

    That is the one WAV layout every reader accepts, including the stdlib
    ``wave`` module. Float (3) and WAVE_FORMAT_EXTENSIBLE (0xFFFE) are not.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
            if len(head) != 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return False
            while True:
                chunk = handle.read(8)
                if len(chunk) != 8:
                    return False
                cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if cid == b"fmt ":
                    body = handle.read(2)
                    return len(body) == 2 and struct.unpack("<H", body)[0] == 1
                handle.seek(size + (size % 2), os.SEEK_CUR)
    except OSError:
        return False


def _cache_key(path: str) -> str:
    info = os.stat(path)
    identity = f"{os.path.realpath(path)}\0{info.st_size}\0{info.st_mtime_ns}"
    return hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _evict(directory: str, keep: str) -> None:
    entries = []
    for name in os.listdir(directory):
        if name.endswith(".wav") and name != keep:
            try:
                entries.append((os.stat(os.path.join(directory, name)).st_mtime_ns, name))
            except OSError:
                continue
    for _, name in sorted(entries)[: max(0, len(entries) + 1 - MAX_CACHED_REFERENCES)]:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError:
            continue


def sidecar_reference_wav(path: str) -> str:
    """``path`` if it is already plain PCM WAV, else a cached PCM16 mono copy.

    Anything that is not an existing local file (an unset or remote value the
    sidecar validates itself) is returned unchanged. If the clip cannot be
    decoded here either, the original path is returned so an engine that reads
    the format natively keeps working; the engine then reports its own error.
    """
    if not path or not os.path.isfile(path) or is_plain_pcm_wav(path):
        return path
    try:
        import soundfile as sf

        from services.audio_io import load_audio

        directory = _cache_dir()
        name = _cache_key(path) + ".wav"
        target = os.path.join(directory, name)
        if os.path.isfile(target):
            os.utime(target)  # most recently used survives eviction
            return target
        wave, sample_rate = load_audio(path)
        mono = wave.float().mean(dim=0).clamp(-1.0, 1.0).numpy()
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                sf.write(handle, mono, sample_rate, format="WAV", subtype="PCM_16")
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        _evict(directory, name)
        return target
    except Exception as exc:  # noqa: BLE001 — the engine may still read the original
        logger.warning("Could not convert reference %s to PCM WAV: %s", os.path.basename(path), exc)
        return path
