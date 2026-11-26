"""Private, content-addressed TTS checkpoints; never a published dub track.

Assembly, cancellation and source replacement cannot remove these valid takes.
Only exact synthesis inputs reuse them. A natural take can also be fitted into
strict slots, without asking the engine to synthesize it again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

import torch

from services.audio_io import atomic_save_wav, load_audio


logger = logging.getLogger("omnivoice.dub")

#: Per-job bound, matching the interrupted-render journal's default.
MAX_BYTES = 1 << 30


class SpeechCheckpoints:
    def __init__(self, job_dir: str, engine: str, sample_rate: int, context: dict):
        self.root = Path(job_dir) / '.speech-checkpoints'
        self.engine = engine
        self.sample_rate = sample_rate
        self.context = context
        self.key_proof = None
        self._reference_hashes = {}
        self.reused = 0
        self.saved = 0

    def _path(self, prepared: dict) -> Path | None:
        try:
            proof = self.key_proof(prepared) if self.key_proof else None
        except Exception as error:
            logger.debug("speech checkpoint identity unavailable: %s", type(error).__name__)
            return None
        if self.key_proof and proof is None:
            return None
        args = dict(prepared)
        args.pop('cache_ref', None)  # an engine-memory policy, not the voice
        reference = args.get('ref_audio')
        if reference:
            digest = hashlib.sha256()
            try:
                stat = os.stat(reference)
                signature = (str(reference), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                cached = self._reference_hashes.get(signature)
                if cached is None:
                    with open(reference, 'rb') as source:
                        for block in iter(lambda: source.read(1024 * 1024), b''):
                            digest.update(block)
                    cached = digest.hexdigest()
                    self._reference_hashes[signature] = cached
                args['ref_audio'] = cached
            except OSError:
                args['ref_audio'] = {'unreadable': str(reference)}
        payload = dict(version=2, model_proof=proof, engine=self.engine, rate=self.sample_rate,
                       context=self.context, args=args)
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return self.root / (key + '.wav')

    def prune(self, max_bytes: int = MAX_BYTES) -> int:
        """Evict the oldest takes once the job's checkpoints exceed ``max_bytes``.

        Every edited line leaves its previous take behind, so without a bound
        a long, often re-rendered job grows on disk forever.
        """
        try:
            takes = []
            for entry in os.scandir(self.root):
                if entry.name.endswith('.wav') and entry.is_file(follow_symlinks=False):
                    info = entry.stat(follow_symlinks=False)
                    takes.append((info.st_mtime_ns, info.st_size, Path(entry.path)))
        except OSError:
            return 0
        total = sum(size for _, size, _ in takes)
        removed = 0
        for _, size, path in sorted(takes):
            if total <= max_bytes:
                break
            for stale in (path.with_suffix('.sha256'), path):
                try:
                    stale.unlink()
                except OSError:
                    pass
            total -= size
            removed += 1
        return removed

    @staticmethod
    def usable(audio) -> bool:
        return bool(audio.numel() and torch.isfinite(audio).all() and torch.any(audio.abs() > 1e-6))

    def read(self, prepared: dict):
        # Prefer an unconstrained take when switching timing strategies.
        candidates = [dict(prepared, duration=None)] if prepared.get('duration') is not None else []
        candidates.append(prepared)
        for candidate in candidates:
            path = self._path(candidate)
            if path is None or not path.is_file():
                continue
            try:
                expected = path.with_suffix(".sha256").read_text().strip()
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    continue
                audio, rate = load_audio(str(path))
                if rate == self.sample_rate and self.usable(audio):
                    self.reused += 1
                    return audio
            except Exception:
                # A corrupt checkpoint is a cache miss, never exported silence.
                continue
        return None

    def path_for(self, prepared: dict):
        """Identity of a take, proven now; ``False`` when it cannot be kept."""
        return self._path(prepared) or False

    def write(self, prepared: dict, audio):
        return self.write_at(self.path_for(prepared), audio)

    def write_at(self, path, audio):
        """Keep already-marked speech under an identity from ``path_for``."""
        if not self.usable(audio):
            raise ValueError('The speech engine returned empty or silent audio')
        if not path:
            return audio
        # A cache failure must not discard usable speech. Never recreate a
        # withdrawn project's parent directory while rendering winds down.
        temporary = None
        try:
            if not self.root.parent.is_dir():
                return audio
            self.root.mkdir(exist_ok=True)
            atomic_save_wav(str(path), audio, self.sample_rate)
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            fd, temporary = tempfile.mkstemp(dir=self.root, suffix='.checksum')
            with os.fdopen(fd, 'w') as stream:
                stream.write(checksum)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path.with_suffix('.sha256'))
            self.saved += 1
        except OSError as error:
            logger.warning("speech checkpoint unavailable, keeping rendered audio: %s", type(error).__name__)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return audio
