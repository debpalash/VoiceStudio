"""#2039 — PyTorch Whisper read uploads with soundfile, which cannot open
MP4/M4A (AAC). /transcribe and the MCP tool both accept .m4a, so every such
upload failed with "Format not recognised" before ASR ran."""
import sys

import numpy as np
import pytest

M4A_BYTES = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64  # soundfile refuses this


def _recording_pipe(seen):
    def fake_pipe(inputs, **_kwargs):
        seen.update(inputs)
        return {"text": "hello", "chunks": []}

    return fake_pipe


def test_an_m4a_upload_reaches_the_pipeline_through_the_ffmpeg_fallback(tmp_path, monkeypatch):
    from services import asr_backend as ab

    clip = tmp_path / "memo.m4a"
    clip.write_bytes(M4A_BYTES)
    decoded = np.zeros(16000, dtype=np.float32)
    decoded_paths = []

    def fake_ffmpeg_decode(path):
        decoded_paths.append(path)
        return decoded

    monkeypatch.setattr(ab, "_decode_audio_16k_mono", fake_ffmpeg_decode)
    seen = {}
    result = ab.PyTorchWhisperBackend(asr_pipe=_recording_pipe(seen)).transcribe(str(clip))

    assert result["text"] == "hello"
    assert decoded_paths == [str(clip)]
    assert seen["sampling_rate"] == 16000
    assert np.array_equal(seen["array"], decoded)


def test_readable_audio_keeps_its_native_rate(tmp_path, monkeypatch):
    """No linear resample for WAV/FLAC: the pipeline's own resampler is
    band-limited, a plain interpolation from 48 kHz would alias."""
    import soundfile as sf
    from services import asr_backend as ab

    clip = tmp_path / "take.wav"
    stereo = np.zeros((4800, 2), dtype=np.float32)
    sf.write(str(clip), stereo, 48000)

    def must_not_decode(_path):
        raise AssertionError("soundfile-readable audio must not go through ffmpeg")

    monkeypatch.setattr(ab, "_decode_audio_16k_mono", must_not_decode)
    seen = {}
    ab.PyTorchWhisperBackend(asr_pipe=_recording_pipe(seen)).transcribe(str(clip))

    assert seen["sampling_rate"] == 48000
    assert seen["array"].ndim == 1 and len(seen["array"]) == 4800


@pytest.mark.usefixtures("asr_model_installed")
def test_the_transcribe_route_accepts_an_m4a_upload(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import capture

    ab = sys.modules["services.asr_backend"]  # the module the route imports from
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **_kw: None)
    monkeypatch.setattr(ab, "_decode_audio_16k_mono", lambda _path: np.zeros(16000, dtype=np.float32))
    seen = {}
    backend = ab.PyTorchWhisperBackend(asr_pipe=_recording_pipe(seen))
    monkeypatch.setattr(ab, "get_capture_asr_backend", lambda *a, **k: backend)
    app = FastAPI()
    app.include_router(capture.router)

    r = TestClient(app, client=("127.0.0.1", 50000)).post(
        "/transcribe", files={"audio": ("memo.m4a", M4A_BYTES, "audio/mp4")}
    )

    assert r.status_code == 200, r.text
    assert seen["sampling_rate"] == 16000


def test_the_mcp_tool_names_m4a_bytes_as_m4a():
    import mcp_server

    assert mcp_server._sniff_audio_ext(M4A_BYTES) == ".m4a"
