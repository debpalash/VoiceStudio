"""Real soundfile I/O and scoped dispatcher regressions for torchaudio 2.9."""

import io
from concurrent.futures import ThreadPoolExecutor
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile
import torch

from services import pyannote_audio_compat as compat


@pytest.mark.parametrize("subtype,bits,encoding", [
    ("PCM_U8", 8, "PCM_U"), ("PCM_16", 16, "PCM_S"),
    ("PCM_24", 24, "PCM_S"), ("PCM_32", 32, "PCM_S"),
    ("FLOAT", 32, "PCM_F"), ("DOUBLE", 64, "PCM_F"),
])
def test_metadata_reports_actual_wav_width(tmp_path, subtype, bits, encoding):
    path = tmp_path / "sample.wav"
    soundfile.write(path, np.zeros((80, 2)), 16000, subtype=subtype)
    info = compat._soundfile_info(path, backend="soundfile")
    assert info == compat.AudioMetaData(16000, 80, 2, bits, encoding)


@pytest.mark.parametrize("container,subtype,encoding,bits", [
    ("FLAC", "PCM_24", "FLAC", 24),
    ("OGG", "VORBIS", "VORBIS", 0),
])
def test_compressed_metadata_and_decode(tmp_path, container, subtype, encoding, bits):
    path = tmp_path / "sample.audio"
    samples = np.sin(np.arange(1600) / 17).astype(np.float32) * 0.25
    soundfile.write(path, samples, 16000, format=container, subtype=subtype)
    info = compat._soundfile_info(path)
    assert (info.sample_rate, info.num_frames, info.num_channels) == (16000, 1600, 1)
    assert (info.encoding, info.bits_per_sample) == (encoding, bits)
    waveform, rate = compat._soundfile_load(path, normalize=False)
    assert rate == 16000 and waveform.shape == (1, 1600)
    assert waveform.dtype == torch.float32 and torch.isfinite(waveform).all()


@pytest.mark.parametrize("channels_first", [True, False])
def test_reads_only_requested_frames(tmp_path, channels_first):
    path = tmp_path / "sample.wav"
    samples = np.arange(200, dtype=np.int16).reshape(100, 2)
    soundfile.write(path, samples, 8000, subtype="PCM_16")
    waveform, rate = compat._soundfile_load(
        path, frame_offset=13, num_frames=17, normalize=False,
        channels_first=channels_first, backend="soundfile",
    )
    expected = samples[13:30].T if channels_first else samples[13:30]
    np.testing.assert_array_equal(waveform.numpy(), expected)
    assert waveform.dtype == torch.int16 and rate == 8000


@pytest.mark.parametrize("subtype,dtype", [
    ("PCM_16", torch.int16), ("PCM_24", torch.int32),
    ("PCM_32", torch.int32), ("PCM_U8", torch.uint8),
    ("FLOAT", torch.float32), ("DOUBLE", torch.float64),
])
def test_normalization_and_native_wav_dtypes(tmp_path, subtype, dtype):
    path = tmp_path / "sample.wav"
    samples = np.array([-0.5, 0.0, 0.5])
    soundfile.write(path, samples, 16000, subtype=subtype)
    native, _ = compat._soundfile_load(path, normalize=False)
    normalized, _ = compat._soundfile_load(path)
    assert native.dtype == dtype and normalized.dtype == torch.float32
    torch.testing.assert_close(normalized, torch.tensor([[-0.5, 0.0, 0.5]]))
    if subtype == "PCM_U8":
        assert native.tolist() == [[64, 128, 192]]


def test_file_like_reads_do_not_close_callers_stream():
    stream = io.BytesIO()
    soundfile.write(stream, np.zeros((160, 2)), 16000, format="WAV", subtype="FLOAT")
    stream.seek(0)
    assert compat._soundfile_info(stream).bits_per_sample == 32
    stream.seek(0)
    waveform, rate = compat._soundfile_load(stream, frame_offset=20, num_frames=30)
    assert not stream.closed and waveform.shape == (2, 30) and rate == 16000


@pytest.mark.parametrize("container,subtype", [
    ("WAV", "GSM610"), ("WAV", "G721_32"), ("AU", "G723_24"),
])
@pytest.mark.parametrize("num_frames", [-1, 0, 17])
@pytest.mark.parametrize("file_like", [False, True])
def test_nonseekable_audio_reads_from_start(tmp_path, container, subtype, num_frames, file_like):
    path = tmp_path / "nonseekable.audio"
    samples = (np.sin(np.arange(3200) / 17) * 0.25).astype(np.float32)
    soundfile.write(path, samples, 8000, format=container, subtype=subtype)
    with soundfile.SoundFile(path) as source:
        assert not source.seekable()
    expected, expected_rate = soundfile.read(
        path, frames=num_frames, dtype="float32", always_2d=True,
    )
    source = io.BytesIO(path.read_bytes()) if file_like else path
    waveform, rate = compat._soundfile_load(source, num_frames=num_frames)
    np.testing.assert_array_equal(waveform.numpy(), expected.T)
    assert rate == expected_rate == 8000
    if file_like:
        assert not source.closed


@pytest.mark.parametrize("frame_offset", [1, 6400])
def test_nonseekable_wav_rejects_nonzero_offset(tmp_path, frame_offset):
    path = tmp_path / "nonseekable.wav"
    soundfile.write(path, np.zeros(3200), 8000, subtype="GSM610")
    with pytest.raises(RuntimeError, match="seekable"):
        compat._soundfile_load(path, frame_offset=frame_offset, num_frames=17)


@pytest.mark.parametrize("offset,frames,expected", [
    (0, -1, 80), (70, 30, 10), (80, -1, 0), (0, 0, 0),
    (81, -1, 0), (81, 20, 0), (81, 0, 0),
])
def test_frame_limits(tmp_path, offset, frames, expected):
    path = tmp_path / "sample.wav"
    soundfile.write(path, np.zeros(80), 16000)
    waveform, _ = compat._soundfile_load(path, frame_offset=offset, num_frames=frames)
    assert waveform.shape == (1, expected)


@pytest.mark.parametrize("parameters", [
    {"frame_offset": -1}, {"frame_offset": 1.5}, {"num_frames": -2},
    {"num_frames": 0.5}, {"backend": "unsupported"},
])
def test_rejects_invalid_requests_before_opening_source(parameters):
    with pytest.raises(ValueError):
        compat._soundfile_load("nonexistent.wav", **parameters)


def test_unreadable_input_is_not_silently_returned_as_empty_audio(tmp_path):
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not audio")
    with pytest.raises(RuntimeError):
        compat._soundfile_load(path)


def test_complete_older_stack_is_not_modified(monkeypatch):
    original = SimpleNamespace(
        AudioMetaData=object(), info=object(), list_audio_backends=object(), load=object(),
    )
    before = vars(original).copy()
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    assert compat.ensure_pyannote_audio_compat() is False
    assert vars(original) == before


@pytest.mark.parametrize("existing", ["AudioMetaData", "info", "list_audio_backends"])
def test_partial_older_api_is_preserved(monkeypatch, existing):
    preserved = object()
    original = SimpleNamespace(load=lambda *args, **kwargs: "original")
    setattr(original, existing, preserved)
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    assert compat.ensure_pyannote_audio_compat() is True
    assert getattr(original, existing) is preserved
    assert all(hasattr(original, name) for name in ("AudioMetaData", "info", "list_audio_backends"))
    assert original.load("source") == "original"


def test_concurrent_initializers_install_one_wrapper(monkeypatch):
    original = SimpleNamespace(load=lambda *args, **kwargs: "original")
    original_load = original.load
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda index: compat.ensure_pyannote_audio_compat(), range(32)))
    assert sum(results) == 1
    assert original.load.__wrapped__ is original_load
    assert original.load("source") == "original"


def test_initialization_failure_does_not_leave_partial_replacements(monkeypatch):
    original = SimpleNamespace(load=None)
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    with pytest.raises(RuntimeError, match="loader is unavailable"):
        compat.ensure_pyannote_audio_compat()
    assert vars(original) == {"load": None}


def test_default_and_other_backends_keep_exact_original_arguments(monkeypatch):
    calls = []

    def original_load(*args, **kwargs):
        calls.append((args, kwargs))
        return "original"

    original = SimpleNamespace(load=original_load)
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    assert compat.ensure_pyannote_audio_compat() is True
    assert original.load("source", frame_offset=12) == "original"
    assert original.load("source", backend="ffmpeg") == "original"
    assert calls == [(("source",), {"frame_offset": 12}), (("source",), {"backend": "ffmpeg"})]
    wrapped = original.load
    assert compat.ensure_pyannote_audio_compat() is False
    assert original.load is wrapped
    assert original.list_audio_backends() == ["soundfile"]


@pytest.mark.parametrize("positional", [False, True])
def test_explicit_soundfile_backend_bypasses_torchcodec(monkeypatch, tmp_path, positional):
    def broken_codec(*args, **kwargs):
        raise AssertionError("TorchCodec must not load for soundfile")

    original = SimpleNamespace(load=broken_codec)
    monkeypatch.setitem(sys.modules, "torchaudio", original)
    compat.ensure_pyannote_audio_compat()
    path = tmp_path / "sample.wav"
    soundfile.write(path, np.zeros(80), 16000)
    if positional:
        waveform, rate = original.load(path, 10, 20, True, True, None, 4096, "soundfile")
    else:
        waveform, rate = original.load(path, frame_offset=10, num_frames=20, backend="soundfile")
    assert waveform.shape == (1, 20) and rate == 16000


def test_real_pyannote_audio_import_read_and_crop(tmp_path):
    compat.ensure_pyannote_audio_compat()
    from pyannote.audio.core.io import Audio, get_torchaudio_info
    from pyannote.core import Segment

    path = tmp_path / "sample.wav"
    samples = np.column_stack((np.full(16000, 0.25), np.full(16000, -0.125)))
    soundfile.write(path, samples, 16000, subtype="FLOAT")
    assert get_torchaudio_info({"audio": str(path)}).bits_per_sample == 32
    audio = Audio(sample_rate=16000, mono="downmix")
    whole, rate = audio(str(path))
    crop, crop_rate = audio.crop(str(path), Segment(0.25, 0.75))
    assert rate == crop_rate == 16000 and crop.shape == (1, 8000)
    torch.testing.assert_close(crop, whole[:, 4000:12000])
    stream = io.BytesIO(path.read_bytes())
    streamed, _ = audio.crop(stream, Segment(0.25, 0.75))
    torch.testing.assert_close(streamed, crop)
    assert not stream.closed


@pytest.mark.parametrize("file_like", [False, True])
def test_real_pyannote_reads_and_crops_nonseekable_wav(tmp_path, file_like):
    compat.ensure_pyannote_audio_compat()
    from pyannote.audio.core.io import Audio
    from pyannote.core import Segment

    path = tmp_path / "nonseekable.wav"
    samples = (np.sin(np.arange(3200) / 17) * 0.25).astype(np.float32)
    soundfile.write(path, samples, 8000, subtype="GSM610")
    source = io.BytesIO(path.read_bytes()) if file_like else path
    expected, _ = soundfile.read(path, dtype="float32", always_2d=True)
    audio = Audio(sample_rate=8000)
    whole, rate = audio(source)
    crop, crop_rate = audio.crop(source, Segment(0, 0.2))
    assert rate == crop_rate == 8000
    torch.testing.assert_close(whole, torch.from_numpy(expected.T))
    torch.testing.assert_close(crop, whole[:, :1600])
    if file_like:
        assert not source.closed and source.tell() == 0


@pytest.mark.parametrize("container,subtype", [
    ("WAV", "GSM610"), ("WAV", "G721_32"), ("AU", "G723_24"),
])
@pytest.mark.parametrize("file_like", [False, True])
def test_real_pyannote_nonzero_crop_preserves_fallback_contract(tmp_path, container, subtype, file_like):
    compat.ensure_pyannote_audio_compat()
    from pyannote.audio.core.io import Audio
    from pyannote.core import Segment

    path = tmp_path / "nonseekable.audio"
    samples = (np.sin(np.arange(3200) / 17) * 0.25).astype(np.float32)
    soundfile.write(path, samples, 8000, format=container, subtype=subtype)
    expected, _ = soundfile.read(path, dtype="float32", always_2d=True)
    audio = Audio(sample_rate=8000)
    if file_like:
        stream = io.BytesIO(path.read_bytes())
        with pytest.raises(RuntimeError, match="seek-and-read in file-like object"):
            audio.crop(stream, Segment(0.1, 0.3))
        assert not stream.closed
        return
    audio_file = {"audio": path}
    with pytest.warns(UserWarning, match="loading the whole file instead"):
        crop, rate = audio.crop(audio_file, Segment(0.1, 0.3))
    assert rate == 8000
    torch.testing.assert_close(crop, torch.from_numpy(expected[800:2400].T))
    torch.testing.assert_close(audio_file["waveform"], torch.from_numpy(expected.T))
    assert audio_file["sample_rate"] == 8000
