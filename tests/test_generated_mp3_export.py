"""GET /audio/<id>.mp3: the Clone take, encoded as MP3 for export."""
import asyncio
import io
import math
import os
import struct
import wave

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest


def _sample_wav():
    samples = [int(8000 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(12000)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _client(monkeypatch, outputs):
    from api.routers import generation
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(outputs))
    app = FastAPI()
    app.include_router(generation.router)
    return TestClient(app)


def test_mp3_url_serves_the_render_as_mp3(monkeypatch, tmp_path):
    from services.ffmpeg_utils import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    (tmp_path / "ab12cd34.wav").write_bytes(_sample_wav())
    response = _client(monkeypatch, tmp_path).get("/audio/ab12cd34.mp3")
    if response.status_code == 503 and "libmp3lame" in response.json()["detail"]:
        pytest.skip("ffmpeg build lacks libmp3lame")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    # An ID3 tag or a bare MPEG frame sync, never the WAV bytes.
    assert response.content[:3] == b"ID3" or response.content[0] == 0xFF
    assert not response.content.startswith(b"RIFF")


def test_mp3_url_missing_render_is_a_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/audio/ab12cd34.mp3").status_code == 404
    assert client.get("/audio/not-a-render.mp3").status_code == 404


def test_mp3_url_encoder_failure_is_service_unavailable(monkeypatch, tmp_path):
    from services import audio_io

    (tmp_path / "ab12cd34.wav").write_bytes(_sample_wav())

    async def timeout(_wav):
        raise asyncio.TimeoutError

    monkeypatch.setattr(audio_io, "encode_mp3", timeout)
    response = _client(monkeypatch, tmp_path).get("/audio/ab12cd34.mp3")
    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]


def test_mp3_url_does_not_follow_render_symlink_outside_outputs(monkeypatch, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    private = tmp_path / "private.wav"
    private.write_bytes(_sample_wav())
    try:
        (outputs / "ab12cd34.wav").symlink_to(private)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert _client(monkeypatch, outputs).get("/audio/ab12cd34.mp3").status_code == 404


@pytest.mark.parametrize("encoder", ["encode_mp3", "encode_ogg_opus"])
def test_encoder_failure_detail_hides_local_paths(monkeypatch, tmp_path, encoder):
    from services import audio_io, ffmpeg_utils

    home = os.path.expanduser("~")
    src = os.path.join(home, "private-project", "ab12cd34.wav")

    async def failing_ffmpeg(cmd, timeout):
        return 1, b"", f"{src}: Invalid data found when processing input\n".encode()

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", failing_ffmpeg)
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(getattr(audio_io, encoder)(src))
    message = str(failure.value)
    assert "ab12cd34.wav: Invalid data" in message
    assert home not in message and "private-project" not in message


@pytest.mark.parametrize("path,media_type", [
    ("/audio/{audio_id}.mp3", "audio/mpeg"),
    ("/audio/{audio_id}.ogg", "audio/ogg"),
    ("/audio/{audio_id}.opus", "audio/ogg"),
])
def test_encoded_audio_routes_document_binary_audio(monkeypatch, tmp_path, path, media_type):
    schema = _client(monkeypatch, tmp_path).app.openapi()
    content = schema["paths"][path]["get"]["responses"]["200"]["content"]
    assert list(content) == [media_type]
    assert content[media_type]["schema"]["format"] == "binary"
