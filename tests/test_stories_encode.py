"""Stories audio-encode endpoint (/stories/encode)."""
from __future__ import annotations

import io
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import stories
from services.ffmpeg_utils import find_ffmpeg


def _app():
    app = FastAPI()
    app.include_router(stories.router)
    return app


def _wav_bytes(sec=0.1, sr=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(int(sr * sec))))
    return buf.getvalue()


def test_rejects_unknown_format():
    c = TestClient(_app(), client=("127.0.0.1", 50000))
    r = c.post("/stories/encode", files={"file": ("s.wav", _wav_bytes(), "audio/wav")}, data={"format": "exe"})
    assert r.status_code == 400


def test_501_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(stories, "find_ffmpeg", lambda: None)
    c = TestClient(_app(), client=("127.0.0.1", 50000))
    r = c.post("/stories/encode", files={"file": ("s.wav", _wav_bytes(), "audio/wav")}, data={"format": "mp3"})
    assert r.status_code == 501


def test_encodes_mp3_when_ffmpeg_present():
    if not find_ffmpeg():
        pytest.skip("ffmpeg not available in this environment")
    c = TestClient(_app(), client=("127.0.0.1", 50000))
    r = c.post(
        "/stories/encode",
        files={"file": ("s.wav", _wav_bytes(0.2), "audio/wav")},
        data={"format": "mp3", "bitrate": "96k"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert len(r.content) > 0
