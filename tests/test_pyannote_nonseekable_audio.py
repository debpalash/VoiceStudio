"""Real sequential-only codecs and pyannote's path-only crop fallback."""

import importlib
import io

import numpy as np
import pytest
import soundfile
import torch

@pytest.fixture
def compat():
    return importlib.import_module("services.pyannote_audio_compat")


@pytest.mark.parametrize("container,subtype", [
    ("WAV", "GSM610"), ("WAV", "G721_32"), ("AU", "G723_24"),
])
@pytest.mark.parametrize("num_frames", [-1, 0, 17])
@pytest.mark.parametrize("file_like", [False, True])
def test_nonseekable_audio_reads_from_start(compat, tmp_path, container, subtype, num_frames, file_like):
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
def test_nonseekable_wav_rejects_nonzero_offset(compat, tmp_path, frame_offset):
    path = tmp_path / "nonseekable.wav"
    soundfile.write(path, np.zeros(3200), 8000, subtype="GSM610")
    with pytest.raises(RuntimeError, match="seekable"):
        compat._soundfile_load(path, frame_offset=frame_offset, num_frames=17)


@pytest.mark.parametrize("file_like", [False, True])
def test_real_pyannote_reads_and_crops_nonseekable_wav(compat, tmp_path, file_like):
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
def test_real_pyannote_nonzero_crop_preserves_fallback_contract(compat, tmp_path, container, subtype, file_like):
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
