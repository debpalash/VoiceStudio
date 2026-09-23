"""Phone-line audio: 8 kHz resampling and G.711 μ-law, in numpy.

``audioop`` (the stdlib G.711 codec) is gone in Python 3.13, so the codec is a
vectorized port of the reference Sun ``g711.c`` that ``audioop`` itself used —
bit-identical to ``audioop.lin2ulaw`` / ``ulaw2lin`` (pinned exhaustively in
the tests while a Python with ``audioop`` is available).
"""
from __future__ import annotations

import io

import numpy as np

PHONE_SAMPLE_RATE = 8000
#: One 20 ms frame of 8 kHz μ-law = 160 bytes (one byte per sample).
FRAME_MS = 20
FRAME_BYTES = PHONE_SAMPLE_RATE * FRAME_MS // 1000
#: μ-law byte for digital silence.
ULAW_SILENCE = 0xFF

_BIAS = 0x21  # 33, applied to the 14-bit magnitude (Sun g711.c)
_CLIP = 8159
_SEG_END = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF], dtype=np.int32)


def float_to_pcm16(samples) -> np.ndarray:
    """[-1, 1] floats (any shape; first channel kept) → int16 mono."""
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[0]
    arr = np.nan_to_num(arr.reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    return np.clip(np.round(arr * 32767.0), -32768, 32767).astype(np.int16)


def lin2ulaw(pcm16) -> bytes:
    """int16 PCM → G.711 μ-law bytes (identical to ``audioop.lin2ulaw(x, 2)``)."""
    pcm = np.asarray(pcm16, dtype=np.int16).astype(np.int32) >> 2  # 14-bit
    negative = pcm < 0
    mag = np.where(negative, -pcm, pcm)
    mag = np.minimum(mag, _CLIP) + _BIAS
    mask = np.where(negative, 0x7F, 0xFF)
    seg = np.searchsorted(_SEG_END, mag, side="left")
    shifted = mag >> np.minimum(seg + 1, 31)
    uval = np.where(seg >= 8, 0x7F, (seg << 4) | (shifted & 0xF))
    return (uval ^ mask).astype(np.uint8).tobytes()


def ulaw2lin(data: bytes) -> np.ndarray:
    """G.711 μ-law bytes → int16 PCM (identical to ``audioop.ulaw2lin(x, 2)``)."""
    u = (~np.frombuffer(data, dtype=np.uint8)).astype(np.int32) & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t = t << ((u & 0x70) >> 4)
    return np.where(u & 0x80, 0x84 - t, t - 0x84).astype(np.int16)


def resample_to_phone(samples, sample_rate: int) -> np.ndarray:
    """Mono float audio at ``sample_rate`` → float32 at 8 kHz.

    torchaudio's windowed-sinc resampler low-passes before decimating, so
    content above the 4 kHz phone Nyquist is filtered rather than aliased
    into the band a caller hears.
    """
    import torch
    import torchaudio

    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[0]
    arr = arr.reshape(-1)
    if sample_rate == PHONE_SAMPLE_RATE or arr.size == 0:
        return arr.copy()
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    out = torchaudio.functional.resample(
        torch.from_numpy(np.ascontiguousarray(arr)), int(sample_rate), PHONE_SAMPLE_RATE
    )
    return out.numpy().astype(np.float32, copy=False)


def to_phone_ulaw(samples, sample_rate: int) -> bytes:
    """Any-rate float audio → 8 kHz mono μ-law bytes."""
    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().float().numpy()
    return lin2ulaw(float_to_pcm16(resample_to_phone(samples, sample_rate)))


def iter_frames(ulaw: bytes, frame_bytes: int = FRAME_BYTES):
    """Split μ-law audio into fixed 20 ms frames; the tail is silence-padded
    so every frame the line receives is exactly ``frame_bytes`` long."""
    for start in range(0, len(ulaw), frame_bytes):
        frame = ulaw[start:start + frame_bytes]
        if len(frame) < frame_bytes:
            frame = frame + bytes([ULAW_SILENCE]) * (frame_bytes - len(frame))
        yield frame


def ulaw_to_wav(ulaw: bytes) -> bytes:
    """Decode μ-law to a PCM16 8 kHz WAV any browser can play.

    Used by the local preview: the audio goes through the exact encode →
    decode round trip a phone call applies, so the user hears line quality.
    (Browsers do not reliably play μ-law WAVs, hence decoding here.)
    """
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, ulaw2lin(ulaw), PHONE_SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()
