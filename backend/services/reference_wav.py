"""Hand sidecar engines a reference clip they can all read (#3688).

Profiles keep the reference in whatever container the user uploaded (M4A from
a phone, MP3, FLAC, float or WAVE_FORMAT_EXTENSIBLE WAV). Sidecar venvs carry
only their engine's own dependencies: PocketTTS reads ``.wav`` with the stdlib
``wave`` module (integer PCM, format tag 1 only) and needs ``soundfile`` for
anything else. Decoding once here, where ffmpeg and the full fallback chain
live, keeps every engine venv free of extra audio dependencies.

Each converted copy is named ``<source id>-<version>.wav``. The source id
derives from the source's real path and ``<source id>.source`` records that
path, so a copy is removed with its voice: when the source file is gone
(profile deleted, take replaced) or superseded by a newer version. Copies
are deleted only through :func:`core.voice_leases.remove_if_unused`, so a
copy a sidecar request still holds is never removed under it.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import os
import struct
import tempfile
import threading
from typing import Optional

from core import voice_leases

logger = logging.getLogger("omnivoice.reference_wav")

#: Converted clips kept on disk. A stable name per source lets sidecars that
#: cache voice state by path (PocketTTS) reuse it across generations.
MAX_CACHED_REFERENCES = 32

# Recency is tracked here, never by touching the copy: PocketTTS keys its
# voice state on the file's mtime, so changing it would rebuild the voice.
_lock = threading.Lock()
_uses = itertools.count(1)
_last_used: dict[str, int] = {}


def _cache_dir() -> str:
    from core.config import DATA_DIR

    return os.path.join(DATA_DIR, "cache", "reference_wav")


def is_plain_pcm_wav(path: str) -> bool:
    """True for a RIFF/WAVE file of 16-bit integer PCM (format tag 1).

    That is the one WAV layout every sidecar reads correctly: PocketTTS reads
    any ``.wav`` with the stdlib ``wave`` module and interprets the samples
    as int16 whatever the header says, so 8/24/32-bit PCM, float (3) and
    WAVE_FORMAT_EXTENSIBLE (0xFFFE) are all converted.
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
                    body = handle.read(16)
                    if len(body) != 16:
                        return False
                    tag, bits = struct.unpack("<H", body[:2])[0], struct.unpack("<H", body[14:])[0]
                    return tag == 1 and bits == 16
                handle.seek(size + (size % 2), os.SEEK_CUR)
    except OSError:
        return False


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _source_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _copy_name(path: str) -> tuple[str, str]:
    """``(source id, copy file name)`` for the current version of ``path``."""
    info = os.stat(path)
    source_id = _digest(_source_key(path))
    return source_id, f"{source_id}-{_digest(f'{info.st_size}:{info.st_mtime_ns}')}.wav"


def _source_exists(directory: str, source_id: str) -> bool:
    try:
        with open(os.path.join(directory, source_id + ".source"), encoding="utf-8",
                  errors="surrogateescape") as handle:
            return os.path.isfile(handle.read())
    except OSError:
        return False  # unknown source: drop the copy rather than keep a voice around


def _mtime(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def _prune(directory: str, keep: Optional[str] = None) -> None:
    """Remove copies whose source is gone or superseded, then bound the cache.

    ``keep`` (a copy name just produced) always survives and counts toward
    the bound. Held copies are skipped and retried on a later prune.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return
    groups: dict[str, list[str]] = {}
    for name in names:
        if name.endswith(".wav") and "-" in name:
            groups.setdefault(name.split("-", 1)[0], []).append(name)
    keep_id = keep.split("-", 1)[0] if keep else None
    doomed: set[str] = set()
    for source_id, group in groups.items():
        if source_id == keep_id:
            doomed.update(name for name in group if name != keep)  # older versions
        elif not _source_exists(directory, source_id):
            doomed.update(group)  # the voice was deleted, so is its copy
    with _lock:
        used = dict(_last_used)
    survivors = sorted(
        (name for group in groups.values() for name in group if name not in doomed and name != keep),
        key=lambda name: (used.get(name, 0), _mtime(os.path.join(directory, name))),
    )
    excess = len(survivors) + (1 if keep else 0) - MAX_CACHED_REFERENCES
    doomed.update(survivors[: max(0, excess)])
    for name in doomed:
        try:
            voice_leases.remove_if_unused(os.path.join(directory, name))
        except OSError:
            continue
        with _lock:
            _last_used.pop(name, None)
    try:
        remaining = {name.split("-", 1)[0] for name in os.listdir(directory) if name.endswith(".wav")}
    except OSError:
        return
    for name in names:
        if name.endswith(".source") and name[: -len(".source")] not in remaining | {keep_id}:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                continue


def prune_reference_copies() -> None:
    """Drop converted copies of references that no longer exist.

    Called when a profile or take is deleted, so a deleted voice does not
    linger in the cache until a later conversion evicts it.
    """
    _prune(_cache_dir())


def sidecar_reference_wav(path: str, lease: Optional[voice_leases.VoiceFileLease] = None) -> str:
    """``path`` if it is already a 16-bit PCM ``.wav``, else a cached PCM16 mono copy.

    Pass the request's ``lease`` so the copy cannot be evicted before the
    sidecar has read it. Anything that is not an existing local file (an
    unset or remote value the sidecar validates itself) is returned
    unchanged. If the clip cannot be decoded here either, the original path
    is returned so an engine that reads the format natively keeps working;
    the engine then reports its own error.
    """
    if not path or not os.path.isfile(path):
        return path
    # PocketTTS picks its reader by suffix: a PCM WAV named otherwise would
    # need soundfile, which its venv lacks.
    if path.lower().endswith(".wav") and is_plain_pcm_wav(path):
        return path
    try:
        import soundfile as sf

        from services.audio_io import load_audio

        directory = _cache_dir()
        source_id, name = _copy_name(path)
        target = os.path.join(directory, name)
        voice_leases.hold(lease, target)  # before the existence check: no eviction gap
        with _lock:
            _last_used[name] = next(_uses)
        if os.path.isfile(target):
            return target
        wave, sample_rate = load_audio(path)
        mono = wave.float().mean(dim=0).clamp(-1.0, 1.0).numpy()
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, source_id + ".source"), "w", encoding="utf-8",
                  errors="surrogateescape") as handle:
            handle.write(_source_key(path))
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
        _prune(directory, keep=name)
        return target
    except Exception as exc:  # noqa: BLE001 — the engine may still read the original
        from core.scrub import scrub_text

        logger.warning("Could not convert reference %s to PCM WAV: %s",
                       os.path.basename(path), scrub_text(str(exc)))
        return path
