import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile
import torch


def _install_on(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "torchaudio", fake)
    from services.pyannote_audio_compat import ensure_pyannote_audio_compat

    return ensure_pyannote_audio_compat()


def _ensure_installed():
    from services.pyannote_audio_compat import ensure_pyannote_audio_compat

    return ensure_pyannote_audio_compat()


def test_complete_legacy_surface_is_left_untouched(monkeypatch):
    fake = SimpleNamespace(
        AudioMetaData=object(),
        info=lambda *args: None,
        list_audio_backends=lambda: ["legacy"],
        load=lambda *args, **kwargs: None,
        sentinel=object(),
    )
    before = vars(fake).copy()

    assert _install_on(monkeypatch, fake) is False
    assert vars(fake) == before
    assert fake.load is before["load"]


def test_new_surface_installs_once_and_delegates_other_backends(monkeypatch):
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls)

    fake = SimpleNamespace(load=original)
    assert _install_on(monkeypatch, fake) is True
    installed_load = fake.load
    assert _ensure_installed() is False
    assert fake.load is installed_load
    assert fake.list_audio_backends() == ["soundfile"]

    assert fake.load("one.wav", 4, normalize=False) == 1
    assert fake.load("two.wav", backend="ffmpeg", num_frames=7) == 2
    assert calls == [
        (("one.wav", 4), {"normalize": False}),
        (("two.wav",), {"backend": "ffmpeg", "num_frames": 7}),
    ]


def test_pcm16_metadata_and_positional_soundfile_route(monkeypatch, tmp_path):
    samples = np.array(
        [[-32000, 1200], [-1000, 2000], [0, 3000], [1000, 4000], [31000, 5000]],
        dtype=np.int16,
    )
    path = tmp_path / "stereo.wav"
    soundfile.write(path, samples, 22050, subtype="PCM_16")
    fake = SimpleNamespace(load=lambda *args, **kwargs: pytest.fail("original loader used"))
    assert _install_on(monkeypatch, fake)

    metadata = fake.info(path)
    assert metadata == (22050, len(samples), 2, 16, "PCM_S")
    waveform, rate = fake.load(path, 1, 3, False, True, None, 4096, "soundfile")
    assert rate == 22050
    assert waveform.dtype == torch.int16
    assert torch.equal(waveform, torch.from_numpy(samples[1:4]).transpose(0, 1))


def test_native_unsigned_and_float_wav_types(monkeypatch, tmp_path):
    unsigned = np.array([0, 64, 128, 192, 255], dtype=np.uint8)
    u8_path = tmp_path / "unsigned.wav"
    with wave.open(str(u8_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(8000)
        output.writeframes(unsigned.tobytes())

    floating = np.array([[-0.75], [0.0], [0.5]], dtype=np.float32)
    float_path = tmp_path / "floating.wav"
    soundfile.write(float_path, floating, 16000, subtype="FLOAT")

    fake = SimpleNamespace(load=lambda *args, **kwargs: None)
    assert _install_on(monkeypatch, fake)
    decoded_u8, _ = fake.load(u8_path, normalize=False, backend="soundfile")
    decoded_float, _ = fake.load(float_path, normalize=False, backend="soundfile")

    assert decoded_u8.dtype == torch.uint8
    assert torch.equal(decoded_u8, torch.from_numpy(unsigned).reshape(1, -1))
    assert decoded_float.dtype == torch.float32
    assert torch.equal(decoded_float, torch.from_numpy(floating).transpose(0, 1))


def test_normalized_pcm_is_float_and_preserves_signal(monkeypatch, tmp_path):
    samples = np.array([[-16384], [0], [16384]], dtype=np.int16)
    path = tmp_path / "levels.wav"
    soundfile.write(path, samples, 12000, subtype="PCM_16")
    fake = SimpleNamespace(load=lambda *args, **kwargs: None)
    assert _install_on(monkeypatch, fake)

    waveform, _ = fake.load(path, backend="soundfile")
    assert waveform.dtype == torch.float32
    torch.testing.assert_close(waveform, torch.tensor([[-0.5, 0.0, 0.5]]), atol=1e-6, rtol=0)


def test_invalid_offset_wins_over_missing_file(monkeypatch, tmp_path):
    fake = SimpleNamespace(load=lambda *args, **kwargs: None)
    assert _install_on(monkeypatch, fake)

    with pytest.raises(ValueError, match="frame_offset"):
        fake.load(tmp_path / "absent.wav", frame_offset=-1, backend="soundfile")


def test_offset_beyond_end_keeps_channel_axis(monkeypatch, tmp_path):
    path = tmp_path / "short.wav"
    soundfile.write(path, np.array([[1], [2]], dtype=np.int16), 8000, subtype="PCM_16")
    fake = SimpleNamespace(load=lambda *args, **kwargs: None)
    assert _install_on(monkeypatch, fake)

    waveform, _ = fake.load(path, frame_offset=99, channels_first=False, backend="soundfile")
    assert waveform.shape == (0, 1)


def _torchcodec_missing(*args, **kwargs):
    raise ImportError(
        "TorchCodec is required for load_with_torchcodec. Please install torchcodec to use this function."
    )


def test_default_load_falls_back_to_soundfile_without_torchcodec(monkeypatch, tmp_path):
    """torchaudio >= 2.9 decodes only through TorchCodec, which is not installed.

    Every caller of ``torchaudio.load`` (tests, transformers, our own code)
    must still get audio, not ``ImportError``.
    """
    samples = np.array([[0.25, -0.5], [0.5, 0.0], [-0.25, 0.75]], dtype=np.float32)
    path = tmp_path / "default.wav"
    soundfile.write(path, samples, 16000, subtype="FLOAT")
    fake = SimpleNamespace(load=_torchcodec_missing)
    assert _install_on(monkeypatch, fake)

    waveform, rate = fake.load(path)
    assert rate == 16000
    assert torch.allclose(waveform, torch.from_numpy(samples).transpose(0, 1))


def test_fallback_rewinds_a_file_like_source(monkeypatch):
    import io

    buffer = io.BytesIO()
    soundfile.write(buffer, np.zeros((8, 1), dtype=np.float32), 8000, format="WAV")
    buffer.seek(0)

    def consumes_then_fails(source, *args, **kwargs):
        source.read(12)
        _torchcodec_missing()

    fake = SimpleNamespace(load=consumes_then_fails)
    assert _install_on(monkeypatch, fake)
    waveform, rate = fake.load(buffer)
    assert (rate, tuple(waveform.shape)) == (8000, (1, 8))


@pytest.mark.parametrize(
    "error",
    [ImportError("No module named 'numpy.core'"), RuntimeError("corrupt header")],
)
def test_real_decode_errors_still_propagate(monkeypatch, error):
    def fails(*args, **kwargs):
        raise error

    fake = SimpleNamespace(load=fails)
    assert _install_on(monkeypatch, fake)
    with pytest.raises(type(error), match=str(error.args[0])[:10]):
        fake.load("x.wav")


def test_explicit_non_soundfile_backend_is_not_rerouted(monkeypatch):
    fake = SimpleNamespace(load=_torchcodec_missing)
    assert _install_on(monkeypatch, fake)
    with pytest.raises(ImportError, match="TorchCodec"):
        fake.load("x.wav", backend="ffmpeg")


def test_undecodable_input_reraises_the_torchcodec_error(monkeypatch, tmp_path):
    """M4A/AAC: soundfile cannot decode it either, so the caller's own
    TorchCodec handling (services.audio_io falls back to FFmpeg) must still run."""
    path = tmp_path / "clip.m4a"
    path.write_bytes(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 64)
    fake = SimpleNamespace(load=_torchcodec_missing)
    assert _install_on(monkeypatch, fake)
    with pytest.raises(ImportError, match="TorchCodec"):
        fake.load(path)


def test_audio_io_reaches_ffmpeg_for_m4a_with_the_shim_installed(monkeypatch, tmp_path):
    import services.audio_io as audio_io

    fake = SimpleNamespace(load=_torchcodec_missing)
    assert _install_on(monkeypatch, fake)
    monkeypatch.setattr(audio_io, "torchaudio", fake)
    path = tmp_path / "clip.m4a"
    path.write_bytes(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 64)
    reached = []

    def no_ffmpeg():
        reached.append(True)
        return None

    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", no_ffmpeg)
    import services.pyannote_audio_compat as compat

    tried = []
    real_soundfile_load = compat._soundfile_load

    def spy(*args, **kwargs):
        tried.append(args[0] if args else kwargs.get("uri"))
        return real_soundfile_load(*args, **kwargs)

    monkeypatch.setattr(compat, "_soundfile_load", spy)
    with pytest.raises(RuntimeError):
        audio_io.load_audio(str(path))
    assert tried, "the shim never attempted its soundfile decode"
    assert reached, "audio_io stopped before its FFmpeg fallback"
