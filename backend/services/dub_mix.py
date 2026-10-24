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
    peak = 1.0
    if prevent_clipping:
        for off in range(0, len(samples), chunk_samples):
            chunk = np.array(samples[off:off + chunk_samples], dtype=np.float32, copy=True)
            np.nan_to_num(chunk, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
            peak = max(peak, float(np.max(np.abs(chunk))))
    for off in range(0, len(samples), chunk_samples):
        chunk = np.array(samples[off:off + chunk_samples], dtype=np.float32, copy=True)
        np.nan_to_num(chunk, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        if peak > 1.0:
            chunk /= peak
        np.clip(chunk, -1.0, 1.0, out=chunk)
        yield (chunk * 32767.0).astype("<i2", copy=False).tobytes()
