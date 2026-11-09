"""Bounded-memory PCM encoding for an assembled dub track."""
from __future__ import annotations

import numpy as np


def pcm16_chunks(samples, chunk_samples: int, *, prevent_clipping: bool = False):
    """Encode mono samples in bounded chunks, optionally preserving mix peaks.

    A single attenuation factor for the whole track preserves waveform shape
    and avoids volume jumps between chunks. Safe mixes are never amplified.
    The extra read pass keeps working memory bounded for disk-backed tracks.
    """
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")

    def blocks():
        for start in range(0, len(samples), chunk_samples):
            block = np.array(samples[start:start + chunk_samples], dtype=np.float32)
            yield np.nan_to_num(block, nan=0.0, posinf=1.0, neginf=-1.0)

    gain = 1.0
    if prevent_clipping:
        loudest = max((float(np.abs(block).max()) for block in blocks() if block.size), default=0.0)
        if loudest > 1.0:
            gain = 1.0 / loudest
    for block in blocks():
        yield (np.clip(block * gain, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
