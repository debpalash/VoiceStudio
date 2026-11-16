"""Private, content-addressed TTS checkpoints; never a published dub track.

Assembly, cancellation and source replacement cannot remove these valid takes.
Only exact synthesis inputs reuse them. A natural take can also be fitted into
strict slots, without asking the engine to synthesize it again.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import torch

from services.audio_io import atomic_save_wav, load_audio


class SpeechCheckpoints:
    def __init__(self, job_dir: str, engine: str, sample_rate: int, context: dict, marker=None):
        self.root = Path(job_dir) / '.speech-checkpoints'
        self.engine = engine
        self.sample_rate = sample_rate
        self.context = context
        self.marker = marker or (lambda audio: audio)
        self.key_proof = None
        self._reference_hashes = {}
        self.reused = 0
        self.saved = 0

    def _path(self, prepared: dict) -> Path | None:
        proof = self.key_proof(prepared) if self.key_proof else None
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

    def write(self, prepared: dict, audio):
        if not self.usable(audio):
            raise ValueError('The speech engine returned empty or silent audio')
        audio = self.marker(audio)
        path = self._path(prepared)
        if path is None:
            return audio
        # Never recreate a deleted project's directory while a render winds down.
        if not self.root.parent.is_dir():
            raise FileNotFoundError('The dubbing project was removed')
        self.root.mkdir(exist_ok=True)
        atomic_save_wav(str(path), audio, self.sample_rate)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix='.checksum')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(checksum)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path.with_suffix('.sha256'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.saved += 1
        return audio
