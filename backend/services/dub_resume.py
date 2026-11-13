"""Crash-resume journal for dub renders.

A dub render stages its fresh speech in a private directory and only promotes it
to the shared per-segment cache (behind previews and partial regeneration) when
the track is published. That keeps a cancelled, failed or rejected render from
leaving audio that later counts as up to date, but it also means a crash or
restart mid-dub loses every segment that finished.

This journal keeps a *recovery copy* of each finished segment under the job's
own directory (``<job dir>/.resume/<track lang>/``). It is not the shared cache:
nothing here is read by previews, export or partial regeneration, and nothing
here is ever fingerprinted as accepted. The next render of the same job may
reuse an entry only when the entry's **input key** equals the key of the segment
it is about to synthesize, so reuse only skips re-synthesis of identical inputs.
Reused audio still travels the normal path: staged, then promoted with its
fingerprint when the track is published.

Every failure here is soft. Callers treat an exception, a miss or an invalid file
as "render normally"; nothing in this module can fail a dub.

Pure file I/O, no torch, so it is unit-testable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from typing import Callable, Optional

from core.durable_io import flush_dir, flush_file

logger = logging.getLogger("omnivoice.dub")

JOURNAL_DIRNAME = ".resume"
KEY_VERSION = 1
# One journal never holds more than this; past it new segments are simply not
# journaled (the render is unaffected, it just cannot resume those).
DEFAULT_MAX_BYTES = 1 << 30
# Entries untouched this long are dropped at the next render of the job.
DEFAULT_MAX_AGE_S = 7 * 24 * 3600

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_HASH_CHUNK = 1 << 20
_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(root: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(root, threading.RLock())


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_reference_file(path: Optional[str], memo: Optional[dict] = None) -> Optional[str]:
    """Content hash of a voice reference clip (``None`` when absent/unreadable).

    A reference path alone does not identify the audio: a profile can be
    re-recorded in place. Memoised by (path, size, mtime) so a clip shared by
    many segments is read once per render.
    """
    if not path:
        return None
    try:
        stat = os.stat(path)
        memo_key = (path, stat.st_size, stat.st_mtime_ns)
        if memo is not None and memo_key in memo:
            return memo[memo_key]
        digest = _sha256_file(path)
    except OSError:
        return None
    if memo is not None:
        memo[memo_key] = digest
    return digest


def build_key(parts: dict) -> str:
    """Stable digest of everything that decides a segment's synthesized audio."""
    blob = json.dumps({"v": KEY_VERSION, **parts}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DubResumeJournal:
    """Recovery copies of finished segments for one (job, track language)."""

    def __init__(
        self,
        root: str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_age_s = max_age_s
        self._lock = _lock_for(root)
        self._bytes = 0

    # -- names -----------------------------------------------------------
    def _base(self, seg_id: str) -> str:
        return os.path.join(self.root, "seg_" + _SAFE_NAME_RE.sub("_", str(seg_id)))

    def _wav(self, seg_id: str) -> str:
        return self._base(seg_id) + ".wav"

    def _meta(self, seg_id: str) -> str:
        return self._base(seg_id) + ".json"

    # -- housekeeping ----------------------------------------------------
    def prune(self, keep_ids: set[str]) -> None:
        """Drop entries for segments no longer in the job, stale entries and
        torn files, then total what remains for the size bound."""
        with self._lock:
            self._bytes = 0
            if not os.path.isdir(self.root):
                return
            keep_bases = {self._base(sid) for sid in keep_ids}
            cutoff = time.time() - self.max_age_s
            # Pass 1: unknown segments, stale entries, tmp files.
            for name in os.listdir(self.root):
                path = os.path.join(self.root, name)
                stem, ext = os.path.splitext(name)
                try:
                    if (
                        ext not in (".wav", ".json")
                        or os.path.join(self.root, stem) not in keep_bases
                        or os.path.getmtime(path) < cutoff
                    ):
                        os.unlink(path)
                except OSError:
                    continue
            # Pass 2: a record without audio (or audio without a record) is
            # a torn write nothing may trust; total what is left.
            for name in os.listdir(self.root):
                path = os.path.join(self.root, name)
                stem, ext = os.path.splitext(name)
                partner = os.path.join(self.root, stem + (".json" if ext == ".wav" else ".wav"))
                try:
                    if not os.path.exists(partner):
                        os.unlink(path)
                    elif ext == ".wav":
                        self._bytes += os.path.getsize(path)
                except OSError:
                    continue

    def discard(self) -> None:
        """Remove the whole journal (render published, or job withdrawn)."""
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self._bytes = 0
            parent = os.path.dirname(self.root)
            try:
                os.rmdir(parent)  # only when no other track language still has one
            except OSError:
                pass

    # -- lookup ----------------------------------------------------------
    def _read_meta(self, seg_id: str) -> Optional[dict]:
        try:
            with open(self._meta(seg_id), "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) else None

    def has(self, seg_id: str, key: str) -> bool:
        """Cheap check (no audio read): an entry exists for exactly this key."""
        with self._lock:
            meta = self._read_meta(seg_id)
            return bool(
                meta
                and meta.get("key") == key
                and meta.get("seg_id") == str(seg_id)
                and os.path.exists(self._wav(seg_id))
            )

    def recover(
        self,
        seg_id: str,
        key: str,
        sample_rate: int,
        validate: Callable[[str], bool],
    ) -> Optional[str]:
        """Path of a verified recovery copy for this exact key, else ``None``.

        An entry that exists but fails any check (other inputs, other sample
        rate, content hash mismatch, ``validate`` rejecting the payload) is
        deleted so it is re-rendered, and the reason is logged.
        """
        with self._lock:
            meta = self._read_meta(seg_id)
            if meta is None:
                return None
            wav = self._wav(seg_id)
            reason = None
            if meta.get("seg_id") != str(seg_id) or meta.get("key") != key:
                reason = "inputs changed"
            elif int(meta.get("sample_rate") or 0) != int(sample_rate):
                reason = "sample rate changed"
            elif not os.path.exists(wav):
                reason = "audio missing"
            else:
                try:
                    if _sha256_file(wav) != meta.get("sha256"):
                        reason = "audio does not match its record"
                    elif not validate(wav):
                        reason = "audio truncated or unreadable"
                except Exception as err:  # noqa: BLE001
                    reason = f"audio unreadable ({type(err).__name__})"
            if reason is None:
                return wav
            logger.info("dub resume: discarding recovered segment %s: %s", seg_id, reason)
            self.drop(seg_id)
            return None

    def drop(self, seg_id: str) -> None:
        """Forget one entry (it failed a check, or is being replaced)."""
        for path in (self._wav(seg_id), self._meta(seg_id)):
            try:
                size = os.path.getsize(path) if path.endswith(".wav") else 0
                os.unlink(path)
                self._bytes = max(0, self._bytes - size)
            except OSError:
                pass

    # -- write -----------------------------------------------------------
    def record(self, seg_id: str, key: str, source_wav: str, sample_rate: int) -> bool:
        """Keep a recovery copy of a finished, staged segment.

        The copy is a separate file (never a link into the staging directory,
        whose files are later renamed into the shared cache). The audio lands
        first and the record that vouches for it last, with the audio's content
        hash, so a crash between the two leaves a file nothing trusts, and two
        renders of the same job racing on one name cannot pair one render's
        record with the other's audio.
        """
        with self._lock:
            try:
                size = os.path.getsize(source_wav)
                if self._bytes + size > self.max_bytes:
                    logger.info("dub resume: journal full, segment %s not kept", seg_id)
                    return False
                os.makedirs(self.root, exist_ok=True)
                wav = self._wav(seg_id)
                token = uuid.uuid4().hex[:8]
                tmp_wav = f"{wav}.{token}.tmp"
                shutil.copyfile(source_wav, tmp_wav)
                flush_file(tmp_wav)
                digest = _sha256_file(tmp_wav)
                self.drop(seg_id)  # a replaced entry stops counting first
                os.replace(tmp_wav, wav)
                tmp_meta = f"{self._meta(seg_id)}.{token}.tmp"
                with open(tmp_meta, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "seg_id": str(seg_id),
                            "key": key,
                            "sample_rate": int(sample_rate),
                            "sha256": digest,
                            "bytes": size,
                        },
                        handle,
                    )
                flush_file(tmp_meta)
                os.replace(tmp_meta, self._meta(seg_id))
                flush_dir(self.root)
                self._bytes += size
                return True
            except Exception as err:  # noqa: BLE001
                logger.info("dub resume: segment %s not journaled: %s", seg_id, err)
                for leftover in (locals().get("tmp_wav"), locals().get("tmp_meta")):
                    if leftover:
                        try:
                            os.unlink(leftover)
                        except OSError:
                            pass
                return False


def copy_into_stage(recovered_wav: str, staged_path: str) -> None:
    """Place a recovery copy at its staged path without sharing its inode."""
    tmp = f"{staged_path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        shutil.copyfile(recovered_wav, tmp)
        os.replace(tmp, staged_path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
