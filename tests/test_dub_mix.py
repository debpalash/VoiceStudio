"""Overlapping voices retain their waveform when the sum exceeds PCM range."""
import wave

import numpy as np
import pytest


def test_overlapping_loud_voices_keep_one_gain_across_wav_chunks(tmp_path):
    """A later 1.6 peak must attenuate the entire mix, not flatten its peaks."""
    from services.dub_mix import pcm16_chunks

    samples = np.memmap(tmp_path / "mix", mode="w+", dtype=np.float32, shape=(8,))
    voice = np.array([.2, -.2, .4, -.4, .8, -.8, .6, -.6], dtype=np.float32)
    samples[:] = voice
    samples[:] += voice
    original = samples.copy()
    output = tmp_path / "dub.wav"
    with wave.open(str(output), "wb") as writer:
        writer.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        for chunk in pcm16_chunks(samples, 3, prevent_clipping=True):
            writer.writeframes(chunk)
    with wave.open(str(output), "rb") as reader:
        actual = np.frombuffer(reader.readframes(8), dtype="<i2") / 32767
    np.testing.assert_allclose(actual, original / 1.6, atol=1 / 32767)
    np.testing.assert_array_equal(samples, original)
    samples._mmap.close()


@pytest.mark.parametrize("protect", [False, True])
def test_safe_mix_is_not_amplified(protect):
    """Protection leaves ordinary levels and silence byte-identical."""
    from services.dub_mix import pcm16_chunks

    samples = np.array([0, .25, -.8, 1, -1], dtype=np.float32)
    result = b"".join(pcm16_chunks(samples, 2, prevent_clipping=protect))
    assert result == (samples * 32767).astype("<i2").tobytes()


def test_legacy_clipping_and_nonfinite_sanitization_are_preserved():
    """Other timing modes retain the existing writer behavior."""
    from services.dub_mix import pcm16_chunks

    samples = np.array([2, -2, np.nan, np.inf, -np.inf], dtype=np.float32)
    result = b"".join(pcm16_chunks(samples, 2))
    assert result == np.array([32767, -32767, 0, 32767, -32767], dtype="<i2").tobytes()
