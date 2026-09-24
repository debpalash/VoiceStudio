"""Parametric round-trip + failure-mode tests for ``services.audio_io``.

Covers the four documented torchaudio.save failure modes that produce
silently-corrupt WAVs:

    1. CUDA / MPS device      → tested when the device is available
    2. Non-contiguous tensor  → exercised via ``.t().t()`` and ``[:, ::2]``
    3. Out-of-range values    → tensor with samples in [-3, 3]
    4. Wrong dtype            → float64, int16 inputs

Plus the explicit-encoding defense against torchaudio 2.9+ backend drift
(default encoding should be ``PCM_16`` on disk regardless of which
backend torchaudio picks at import time).

Closes BUG-01 / issue #48.
"""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
import soundfile as sf
import torch

from services.audio_io import _safe_soundfile_write, _safe_torchaudio_save


@pytest.mark.parametrize("writer", ["torch", "soundfile"])
def test_write_recovers_missing_output_directory(tmp_path, writer):
    target = tmp_path / "removed" / "outputs" / "take.wav"
    if writer == "torch":
        _safe_torchaudio_save(str(target), torch.zeros(1, 240), 24000)
    else:
        _safe_soundfile_write(str(target), np.zeros(240, dtype=np.float32), 24000)
    samples, rate = sf.read(target)
    assert len(samples) == 240
    assert rate == 24000


# ── Helpers ────────────────────────────────────────────────────────────────


def _sine_tensor(
    *,
    seconds: float = 1.0,
    sample_rate: int = 24000,
    freq: float = 440.0,
    amplitude: float = 0.5,
    dtype: torch.dtype = torch.float32,
    device: str = "cpu",
    channels: int = 1,
) -> torch.Tensor:
    n = int(seconds * sample_rate)
    t = torch.arange(n, dtype=torch.float32) / sample_rate
    wave = (amplitude * torch.sin(2 * math.pi * freq * t)).to(dtype)
    if channels == 1:
        wave = wave.unsqueeze(0)
    else:
        wave = wave.unsqueeze(0).repeat(channels, 1)
    if device != "cpu":
        wave = wave.to(device)
    return wave


# ── Round-trip across dtype × device × contiguity ─────────────────────────


_DTYPES = [torch.float32, torch.float64, torch.int16]
_DEVICES = ["cpu"]
if torch.backends.mps.is_available():
    _DEVICES.append("mps")
if torch.cuda.is_available():
    _DEVICES.append("cuda")
_CONTIG = [True, False]


@pytest.mark.parametrize("dtype", _DTYPES, ids=lambda d: str(d).replace("torch.", ""))
@pytest.mark.parametrize("device", _DEVICES)
@pytest.mark.parametrize("contiguous", _CONTIG, ids=["contig", "noncontig"])
def test_safe_save_round_trip(tmp_path, dtype, device, contiguous):
    """For every (dtype, device, contiguity) combo, the helper must
    produce a file that ``sf.info`` decodes cleanly with audible samples.
    """
    # MPS only supports float32 / float16 / bfloat16 — float64 + int16 on
    # MPS raise at .to(device) time. Skip those combinations rather than
    # ship a test that can't possibly pass.
    if device == "mps" and dtype in (torch.float64, torch.int16):
        pytest.skip(f"MPS does not support {dtype}")

    # int16 tensors cannot represent 0.5 directly — scale to the int16
    # range so the post-clamp value isn't trivially zero.
    if dtype == torch.int16:
        # Build the source in float32 then cast so the cast captures the
        # expected scaling (helper coerces back to float32 → [-1, 1]).
        # int16 in [-1, 1] is just {-1, 0, 1}, so use a louder sine.
        wave_f32 = _sine_tensor(
            seconds=1.0, sample_rate=24000, freq=440.0, amplitude=0.9,
        )
        wave = (wave_f32 * 32767).to(torch.int16)
    else:
        wave = _sine_tensor(
            seconds=1.0, sample_rate=24000, freq=440.0, amplitude=0.5,
            dtype=dtype,
        )

    if device != "cpu":
        wave = wave.to(device)

    if not contiguous:
        # Produce a guaranteed non-contiguous 2D tensor. Build a (samples,
        # channels) source then transpose → (channels, samples) with
        # non-contiguous strides.
        flat = wave.squeeze(0) if wave.ndim == 2 else wave
        wave = torch.stack([flat, flat], dim=1).t()  # (2, N), non-contiguous
        assert not wave.is_contiguous(), "test setup must build a non-contig tensor"

    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), wave, 24000)

    info = sf.info(str(target))
    assert info.frames == 24000, f"expected 24000 frames, got {info.frames}"
    assert info.samplerate == 24000
    assert info.subtype.startswith("PCM_"), f"subtype drifted: {info.subtype}"

    samples, _ = sf.read(str(target))
    assert samples.size > 0
    assert abs(samples).max() > 0.1, (
        f"samples too quiet: max={abs(samples).max()} — silent-corruption mode"
    )


def test_safe_save_out_of_range_clamped(tmp_path):
    """Values outside [-1, 1] must be clamped, not wrapped/silenced."""
    # Build a tensor with values in [-3, 3] — int16 wrap-around would
    # produce alternating-sign garbage; soundfile-backend default would
    # silently clip-to-zero on some platforms.
    n = 4800
    base = torch.linspace(-3.0, 3.0, n, dtype=torch.float32).unsqueeze(0)

    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), base, 24000)

    samples, _ = sf.read(str(target))
    assert abs(samples).max() <= 1.0 + 1e-3, (
        f"clamp not applied: max={abs(samples).max()}"
    )
    # And clamping should leave a recognizable ramp, not silence.
    assert abs(samples).max() > 0.9, (
        "post-clamp samples should still hit the rails, got "
        f"max={abs(samples).max()}"
    )


def test_safe_save_non_contiguous_via_transpose(tmp_path):
    """The specific #48 reproduction: torch.cat() of slices, then save."""
    # Mimic dub_generate.py:390 pattern: build segments via slicing,
    # cat them, save the result. The cat-of-slices result is often
    # non-contiguous.
    seg_a = torch.linspace(-0.5, 0.5, 12000, dtype=torch.float32).unsqueeze(0)
    seg_b = torch.linspace(0.5, -0.5, 12000, dtype=torch.float32).unsqueeze(0)
    # Stack into a (2, N) then transpose → make non-contiguous.
    stacked = torch.stack([seg_a.squeeze(0), seg_b.squeeze(0)], dim=1)
    full = stacked.t()  # (2, 12000) non-contiguous
    assert not full.is_contiguous()

    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), full, 24000)

    info = sf.info(str(target))
    assert info.frames == 12000
    samples, _ = sf.read(str(target))
    assert abs(samples).max() > 0.1


def test_safe_save_explicit_encoding_persists(tmp_path):
    """Default save → on-disk subtype must be PCM_16, not a backend default."""
    wave = _sine_tensor()
    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), wave, 24000)

    info = sf.info(str(target))
    assert info.subtype == "PCM_16", (
        f"explicit encoding=PCM_S + bits_per_sample=16 was supposed to lock the "
        f"on-disk subtype to PCM_16; got {info.subtype}"
    )


def test_safe_save_float32_pcm_when_bits_per_sample_32(tmp_path):
    """bits_per_sample=32 should produce PCM_F (float WAV) on disk."""
    wave = _sine_tensor()
    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), wave, 24000, bits_per_sample=32)

    info = sf.info(str(target))
    # Some torchaudio backends label this FLOAT, others PCM_F. Accept either.
    assert info.subtype in ("FLOAT", "PCM_F", "PCM_32"), (
        f"bits_per_sample=32 should produce a float/32-bit subtype, got {info.subtype}"
    )


def test_safe_save_format_passthrough_flac(tmp_path):
    """format='flac' must produce a FLAC container, not a WAV."""
    wave = _sine_tensor()
    target = tmp_path / "out.flac"
    try:
        _safe_torchaudio_save(str(target), wave, 24000, format="flac")
    except RuntimeError as e:
        # FLAC codec not present in this torchaudio build → skip cleanly.
        pytest.skip(f"torchaudio build lacks FLAC: {e}")

    info = sf.info(str(target))
    assert info.format == "FLAC"
    assert info.frames == 24000


def test_ogg_save_converts_int16_pcm_to_normalized_float_without_s16_kwargs(monkeypatch):
    from services import audio_io

    samples = torch.tensor([[0, 8192, -8192, 16384]], dtype=torch.int16)
    seen = []

    def save(_buf, tensor, _rate, **kwargs):
        seen.append((tensor.clone(), kwargs))
        if kwargs.get("encoding") == "PCM_S":
            raise RuntimeError("libvorbis does not support s16 format. Supported values are; fltp")

    monkeypatch.setattr(audio_io.torchaudio, "save", save)
    audio_io._safe_torchaudio_save(io.BytesIO(), samples, 24000, format="ogg")
    assert len(seen) == 1
    tensor, kwargs = seen[0]
    assert kwargs == {"format": "ogg"}
    assert tensor.dtype == torch.float32
    assert torch.allclose(tensor, torch.tensor([[0.0, 0.25, -0.25, 0.5]]))


def test_safe_save_in_memory_buffer():
    """io.BytesIO destination must produce a valid WAV the consumer can decode."""
    wave = _sine_tensor()
    buf = io.BytesIO()
    _safe_torchaudio_save(buf, wave, 24000)
    buf.seek(0)

    info = sf.info(buf)
    assert info.frames == 24000
    buf.seek(0)
    samples, _ = sf.read(buf)
    assert abs(samples).max() > 0.1


def test_safe_save_rejects_empty_tensor(tmp_path):
    """Empty input must raise — never silently produce a 0-frame WAV."""
    with pytest.raises(ValueError, match="empty"):
        _safe_torchaudio_save(str(tmp_path / "out.wav"), torch.empty(0), 24000)


def test_safe_save_rejects_non_tensor(tmp_path):
    """numpy array passed by mistake must fail loudly, not write garbage."""
    with pytest.raises(TypeError):
        _safe_torchaudio_save(  # type: ignore[arg-type]
            str(tmp_path / "out.wav"),
            np.zeros(24000, dtype=np.float32),
            24000,
        )


def test_safe_save_handles_1d_tensor(tmp_path):
    """Mono 1D tensor must be auto-unsqueezed to (1, N)."""
    wave = torch.linspace(-0.5, 0.5, 12000, dtype=torch.float32)
    assert wave.ndim == 1

    target = tmp_path / "out.wav"
    _safe_torchaudio_save(str(target), wave, 24000)

    info = sf.info(str(target))
    assert info.channels == 1
    assert info.frames == 12000


def test_safe_save_rejects_3d_tensor(tmp_path):
    """3D tensor is a programming error — refuse rather than guess shape."""
    with pytest.raises(ValueError, match="1D or 2D"):
        _safe_torchaudio_save(
            str(tmp_path / "out.wav"),
            torch.zeros(1, 1, 1000),
            24000,
        )


# ── _safe_soundfile_write ──────────────────────────────────────────────────


def test_safe_soundfile_write_round_trip(tmp_path):
    n = 24000
    t = np.arange(n, dtype=np.float32) / 24000
    samples = (0.4 * np.sin(2 * math.pi * 440 * t)).astype(np.float32)

    target = tmp_path / "sf.wav"
    _safe_soundfile_write(str(target), samples, 24000)

    info = sf.info(str(target))
    assert info.frames == n
    assert info.subtype == "PCM_16"
    decoded, _ = sf.read(str(target))
    assert abs(decoded).max() > 0.1


def test_safe_soundfile_write_non_contiguous_array(tmp_path):
    """Non-contig numpy slice must still produce a valid WAV."""
    n = 24000
    base = np.arange(n * 2, dtype=np.float32) / (n * 2) * 0.5
    samples = base[::2]  # non-contiguous view
    assert not samples.flags["C_CONTIGUOUS"]

    target = tmp_path / "sf.wav"
    _safe_soundfile_write(str(target), samples, 24000)

    info = sf.info(str(target))
    assert info.frames == len(samples)
    decoded, _ = sf.read(str(target))
    assert abs(decoded).max() > 0.1


def test_safe_soundfile_write_out_of_range_clamped(tmp_path):
    samples = np.linspace(-3.0, 3.0, 4800, dtype=np.float32)
    target = tmp_path / "sf.wav"
    _safe_soundfile_write(str(target), samples, 24000)

    decoded, _ = sf.read(str(target))
    assert abs(decoded).max() <= 1.0 + 1e-3
    assert abs(decoded).max() > 0.9


def test_safe_soundfile_write_rejects_empty(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        _safe_soundfile_write(
            str(tmp_path / "sf.wav"),
            np.array([], dtype=np.float32),
            24000,
        )


def test_safe_soundfile_write_accepts_int16(tmp_path):
    """int16 input must round-trip without forced float conversion."""
    samples = (np.linspace(-0.5, 0.5, 24000) * 32767).astype(np.int16)
    target = tmp_path / "sf.wav"
    _safe_soundfile_write(str(target), samples, 24000)

    info = sf.info(str(target))
    assert info.frames == 24000


def test_safe_soundfile_write_2d_stereo(tmp_path):
    """soundfile expects (samples, channels) for 2D — verify we don't transpose."""
    n = 12000
    t = np.arange(n, dtype=np.float32) / 24000
    mono = (0.4 * np.sin(2 * math.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([mono, mono], axis=-1)  # (n, 2)

    target = tmp_path / "sf.wav"
    _safe_soundfile_write(str(target), stereo, 24000)

    info = sf.info(str(target))
    assert info.channels == 2
    assert info.frames == n


# ── Smoke check that atomic_save_wav still works with the new pipe ─────────


def test_atomic_save_wav_delegates_to_safe_helper(tmp_path):
    """atomic_save_wav must inherit the safety guarantees of the helper.

    Specifically: an out-of-range, non-contiguous, GPU-or-CPU input must
    still produce a valid PCM_16 WAV at the target path.
    """
    from services.audio_io import atomic_save_wav

    seg_a = torch.linspace(-3.0, 3.0, 12000, dtype=torch.float64).unsqueeze(0)
    seg_b = torch.linspace(2.0, -2.0, 12000, dtype=torch.float64).unsqueeze(0)
    stacked = torch.stack([seg_a.squeeze(0), seg_b.squeeze(0)], dim=1).t()
    assert not stacked.is_contiguous()
    assert stacked.dtype == torch.float64

    target = tmp_path / "atomic.wav"
    atomic_save_wav(str(target), stacked, 24000)

    info = sf.info(str(target))
    assert info.subtype == "PCM_16"
    assert info.frames == 12000
    decoded, _ = sf.read(str(target))
    assert abs(decoded).max() <= 1.0 + 1e-3
    assert abs(decoded).max() > 0.5


# ── torchaudio 2.9 + no TorchCodec (#1931 follow-up) ───────────────────────
#
# torchaudio >= 2.9 routes save() through TorchCodec, which needs FFmpeg
# *shared libraries* on the system. Where those are absent every write raises
# ImportError. #1931 guarded set_audio_backend() against that torchaudio but
# left save() unprotected; arm64 CUDA hosts reach it unavoidably, since torch
# 2.8.0 publishes no aarch64 wheel. Without the soundfile fallback these three
# tests raise instead of producing a file.


def _torchcodec_missing(*_a, **_kw):
    raise ImportError(
        "TorchCodec is required for save_with_torchcodec. "
        "Please install torchcodec to use this function."
    )


def test_safe_save_falls_back_to_soundfile_when_torchcodec_missing(
    tmp_path, monkeypatch
):
    import torchaudio

    monkeypatch.setattr(torchaudio, "save", _torchcodec_missing)

    target = tmp_path / "fallback.wav"
    _safe_torchaudio_save(str(target), _sine_tensor(), 24000)

    info = sf.info(str(target))
    assert info.subtype == "PCM_16"
    assert info.frames == 24000
    decoded, sr = sf.read(str(target))
    assert sr == 24000
    assert abs(decoded).max() > 0.1


def test_safe_save_buffer_falls_back_when_torchcodec_missing(monkeypatch):
    """The OpenAI-compatible /v1/audio/speech path writes to a BytesIO."""
    import torchaudio

    monkeypatch.setattr(torchaudio, "save", _torchcodec_missing)

    buf = io.BytesIO()
    _safe_torchaudio_save(buf, _sine_tensor(), 24000, format="wav")

    assert buf.getvalue()[:4] == b"RIFF"
    buf.seek(0)
    decoded, sr = sf.read(buf)
    assert sr == 24000
    assert len(decoded) == 24000


def test_safe_save_flac_buffer_fallback_names_the_format(monkeypatch):
    """soundfile cannot infer a container from a file-like object, so the
    fallback must pass ``format=`` explicitly — otherwise this raises."""
    import torchaudio

    monkeypatch.setattr(torchaudio, "save", _torchcodec_missing)

    buf = io.BytesIO()
    _safe_torchaudio_save(buf, _sine_tensor(), 24000, format="flac")

    assert buf.getvalue()[:4] == b"fLaC"
    buf.seek(0)
    decoded, sr = sf.read(buf)
    assert sr == 24000
    assert len(decoded) == 24000


@pytest.mark.parametrize("message, fallback", [
    ("Could not load libtorchcodec. Missing FFmpeg shared libraries", True),
    ("unrelated encoder failure", False),
])
def test_save_native_loader_failure_only_uses_fallback(tmp_path, monkeypatch, message, fallback):
    import torchaudio
    def fail(*args, **kwargs):
        raise RuntimeError(message)
    monkeypatch.setattr(torchaudio, "save", fail)
    target = tmp_path / "codec.wav"
    if fallback:
        _safe_torchaudio_save(str(target), _sine_tensor(), 24000)
        assert sf.info(target).frames == 24000
    else:
        with pytest.raises(RuntimeError, match="unrelated encoder failure"):
            _safe_torchaudio_save(str(target), _sine_tensor(), 24000)
