"""Every sidecar's defensive downmix must pick the channel axis, not axis 0.

Found while reviewing #1328: the PocketTTS sidecar's `arr.mean(axis=0)` assumed
channels-first. For a channels-last `(N, 2)` array `squeeze()` keeps both axes
and the mean runs across **time** — every output sample becomes the mean of two
neighbouring samples. That is not a downmix; it is a destroyed waveform, at half
the expected length, played back as noise.

The same line was in five already-merged sidecars. Unreachable in all of them
today, since every engine returns mono — which is exactly why it could sit there
being wrong: nothing runs it, so nothing reports it, and the first engine or SDK
version to emit stereo gets noise with no error anywhere.

The sidecars run under *different interpreters* (confucius4 and dots.tts each
have their own venv), so they cannot import a shared helper — the duplication is
structural and cannot be refactored away. This test is the mitigation: it holds
all of them to the same behaviour at once, so a sixth copy pasted into a new
sidecar fails here rather than shipping.
"""
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import numpy as np
import pytest

# Three of the five call `.detach()` unconditionally, so every input here is a
# torch tensor; the two that take numpy accept tensors via `np.asarray`.
torch = pytest.importorskip("torch")

_ROOT = Path(__file__).resolve().parent.parent / "backend" / "engines"

#: (sidecar module path, conversion function name). PocketTTS is absent on
#: purpose: it raises on multi-channel input instead of downmixing, which is
#: also correct, and tests/test_pockettts_sidecar.py pins that.
_SIDECARS = [
    ("moss_tts_v15/main.py", "_tensor_to_pcm_b64"),
    ("dots_tts/main.py", "_tensor_to_pcm_b64"),
    ("supertonic3/sidecar.py", "_wav_float_to_pcm_b64"),
    ("confucius4/main.py", "_tensor_to_pcm_b64"),
    ("ming_tts/main.py", "_tensor_to_pcm_b64"),
    ("omnivoice_subprocess/main.py", "_tensor_to_pcm_b64"),
]


def _convert(rel: str, fn_name: str):
    path = _ROOT / rel
    assert path.is_file(), f"sidecar moved or was renamed: {rel}"
    spec = importlib.util.spec_from_file_location(f"sc_{rel.replace('/', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    # Deliberately unguarded. Every sidecar is stdlib-only at import time (torch
    # and the model load lazily on the first synthesize), so an import error
    # here is a real regression in a shipping engine, not a missing optional
    # dependency. Skipping on it would let a syntax error in any of these five
    # pass CI as a green, silently-empty suite (CodeRabbit).
    spec.loader.exec_module(mod)
    fn = getattr(mod, fn_name, None)
    assert fn is not None, f"{rel} no longer defines {fn_name}"
    return fn


def _t(arr):
    """Sidecars expect a torch tensor (they call .detach())."""
    return torch.from_numpy(np.ascontiguousarray(arr, dtype=np.float32))


def _decode(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=np.int16)


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_channels_last_stereo_downmixes_to_the_right_length(rel, fn_name):
    """The bug, stated as length: a (100, 2) input must yield 100 samples.

    Averaging across time yields 2 — the whole render collapsed into a pair of
    samples. Before this fix every one of these returned 2.
    """
    convert = _convert(rel, fn_name)
    _, _, n = convert(_t(np.zeros((100, 2))), 24000)
    assert n == 100, f"{rel} averaged across time, not channels: got {n} samples"


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_channels_last_stereo_preserves_the_waveform(rel, fn_name):
    """...and stated as content: a channel-correct downmix of two identical
    channels is the original signal, unchanged."""
    convert = _convert(rel, fn_name)
    mono = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    stereo = np.stack([mono, mono], axis=-1)          # (100, 2), channels-last
    b64, _, _ = convert(_t(stereo), 24000)
    expected, _, _ = convert(_t(mono), 24000)
    assert _decode(b64).tolist() == _decode(expected).tolist(), (
        f"{rel} did not recover the original waveform from a channels-last input"
    )


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_channels_first_stereo_still_works(rel, fn_name):
    """The case that was already correct. The fix must not trade one layout for
    the other — this is what a naive `axis=-1` would break."""
    convert = _convert(rel, fn_name)
    mono = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    stereo = np.stack([mono, mono], axis=0)           # (2, 100), channels-first
    b64, _, n = convert(_t(stereo), 24000)
    assert n == 100
    expected, _, _ = convert(_t(mono), 24000)
    assert _decode(b64).tolist() == _decode(expected).tolist()


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_the_two_channels_are_actually_averaged(rel, fn_name):
    """Distinct channels, so a downmix that silently dropped one would pass the
    tests above but not this one."""
    convert = _convert(rel, fn_name)
    left = np.full(64, 0.5, dtype=np.float32)
    right = np.full(64, -0.5, dtype=np.float32)
    for stereo in (np.stack([left, right], axis=-1), np.stack([left, right], axis=0)):
        b64, _, n = convert(_t(stereo), 24000)
        assert n == 64
        assert np.abs(_decode(b64)).max() <= 1, (
            f"{rel}: +0.5 and -0.5 must average to silence, not to one channel"
        )


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_ordinary_mono_is_untouched(rel, fn_name):
    """The only shape that actually occurs in production. The guard must stay
    inert for it — including for the (1, N) batch axis `squeeze()` removes."""
    convert = _convert(rel, fn_name)
    mono = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    b64, sr, n = convert(_t(mono), 24000)
    assert (sr, n) == (24000, 5)
    assert _decode(b64).tolist() == [0, 16383, -16383, 32767, -32767]
    assert convert(_t(mono.reshape(1, -1)), 24000)[2] == 5


@pytest.mark.parametrize("rel,fn_name", _SIDECARS)
def test_a_stray_extra_axis_still_reduces_to_mono(rel, fn_name):
    """`squeeze()` only removes size-1 axes, so a (2, 100, 2) array stayed
    multi-dimensional after a single mean and produced a PCM buffer whose length
    did not match the reported sample count — a desynchronized audio frame
    rather than a wrong-sounding one."""
    convert = _convert(rel, fn_name)
    b64, _, n = convert(_t(np.zeros((2, 100, 2))), 24000)
    assert n == 100
    assert len(_decode(b64)) == n, f"{rel}: PCM length disagrees with n_samples"
