"""Generated audio remains readable without the optional TorchCodec runtime."""
import io

import numpy as np
import pytest
import soundfile as sf
import torch
import torchaudio


@pytest.mark.parametrize("error", [ImportError("TorchCodec missing"), RuntimeError("Could not load libtorchcodec")])
@pytest.mark.parametrize("buffer", [False, True])
def test_load_audio_fallback_preserves_samples_channels_and_rate(tmp_path, monkeypatch, error, buffer):
    from services.audio_io import load_audio

    samples = np.array([[0.25, -0.5], [0.5, -0.25]], dtype=np.float32)
    target = io.BytesIO() if buffer else tmp_path / "segment.wav"
    sf.write(target, samples, 24000, format="WAV", subtype="FLOAT")
    if buffer:
        target.seek(0)

    def unavailable(source):
        if buffer:
            source.read(8)  # A decoder may consume the header before failing.
        raise error

    monkeypatch.setattr(torchaudio, "load", unavailable)
    wave, rate = load_audio(target)
    assert rate == 24000
    assert wave.dtype == torch.float32
    torch.testing.assert_close(wave, torch.from_numpy(samples.T))


def test_unrelated_decoder_errors_are_not_hidden(monkeypatch):
    from services.audio_io import load_audio

    def corrupt(_source):
        raise RuntimeError("corrupt audio")

    monkeypatch.setattr(torchaudio, "load", corrupt)
    with pytest.raises(RuntimeError, match="corrupt audio"):
        load_audio("bad.wav")


def test_stream_without_seekable_method_uses_primary_decoder(monkeypatch):
    from services.audio_io import load_audio
    class Stream:
        def tell(self):
            return 0
    expected = (torch.zeros(1, 4), 24000)
    monkeypatch.setattr(torchaudio, "load", lambda source: expected)
    assert load_audio(Stream()) is expected


@pytest.mark.parametrize("extension", ["m4a", "aac", "mp3", "opus"])
@pytest.mark.parametrize("buffer", [False, True])
def test_compressed_audio_without_torchcodec(tmp_path, monkeypatch, extension, buffer):
    import subprocess
    from services.audio_io import load_audio
    from services.ffmpeg_utils import find_ffmpeg
    ffmpeg = find_ffmpeg()
    assert ffmpeg, "the maintained runtime bundles ffmpeg"
    source = tmp_path / "source.wav"
    signal = np.sin(np.arange(12000) * 0.05).astype(np.float32) * 0.2
    sf.write(source, np.column_stack((signal, -signal)), 24000)
    encoded = tmp_path / ("encoded." + extension)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), str(encoded)], check=True, capture_output=True)
    def missing(source):
        if hasattr(source, "read"):
            source.read(8)
        raise ImportError("TorchCodec missing")
    monkeypatch.setattr(torchaudio, "load", missing)
    wave, rate = load_audio(io.BytesIO(encoded.read_bytes()) if buffer else encoded)
    assert wave.shape[0] == 2
    assert wave.shape[1] >= rate * 0.4
    assert rate in ({24000, 48000} if extension == "opus" else {24000})
    assert wave.dtype == torch.float32
    assert wave.abs().max() > 0.1


def test_ffmpeg_decode_transport_does_not_buffer_the_entire_wav(monkeypatch):
    import subprocess
    import tracemalloc
    from types import SimpleNamespace
    from services import audio_io, ffmpeg_utils

    monkeypatch.setattr(torchaudio, "load", lambda _: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")
    def read(source, **kwargs):
        if source == "compressed.m4a":
            raise RuntimeError("unsupported container")
        return np.zeros((4, 2), dtype=np.float32), 24000
    monkeypatch.setattr(sf, "read", read)
    chunk = b"x" * 65536
    def decode(*args, **kwargs):
        target = kwargs.get("stdout")
        if target is None or target == subprocess.PIPE:
            return SimpleNamespace(stdout=chunk * 160, returncode=0)
        for _ in range(160):
            target.write(chunk)
        return SimpleNamespace(stdout=None, returncode=0)
    monkeypatch.setattr(subprocess, "run", decode)
    tracemalloc.start()
    try:
        audio_io.load_audio("compressed.m4a")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 ** 2, "transport buffered a full 10 MiB decoded WAV"


@pytest.mark.parametrize("budget", ["decoded_size", "free_space"])
def test_compressed_decode_rejects_output_over_budget(tmp_path, monkeypatch, budget):
    import subprocess
    from types import SimpleNamespace
    from services import audio_io
    from services.ffmpeg_utils import find_ffmpeg
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((12000, 2), dtype=np.float32), 24000)
    encoded = tmp_path / "encoded.m4a"
    subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), str(encoded)], check=True, capture_output=True)
    monkeypatch.setattr(torchaudio, "load", lambda _: (_ for _ in ()).throw(ImportError()))
    if budget == "decoded_size":
        monkeypatch.setattr(audio_io, "MAX_DECODED_AUDIO_BYTES", 4096, raising=False)
    else:
        monkeypatch.setattr(audio_io.shutil, "disk_usage", lambda _: SimpleNamespace(free=64 * 1024 ** 2 + 4096))
    with pytest.raises(ValueError, match="decoding size limit"):
        audio_io.load_audio(encoded)
