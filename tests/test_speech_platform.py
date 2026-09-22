"""Public contract for the headless speech platform."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def test_capabilities_are_stable_and_side_effect_free(monkeypatch):
    from api.routers.speech_platform import speech_capabilities

    monkeypatch.delenv("VOICESTUDIO_SPEECH_CONTROL_PORT", raising=False)
    body = speech_capabilities().model_dump(by_alias=True)

    assert body["schema"] == "voicestudio.speech-capabilities"
    assert body["protocol"] == "voicestudio.speech.v1"
    assert body["local_first"] is True
    assert body["endpoints"]["batch_transcription"]["path"] == (
        "/v1/audio/transcriptions"
    )
    assert body["endpoints"]["streaming_transcription"]["path"] == (
        "/v1/audio/transcriptions/stream"
    )
    assert body["stream_input"]["end_control"] == {"type": "input_audio.end"}
    assert body["features"]["native_dictation_control"] is False
    assert "native_dictation_control" not in body["endpoints"]
    assert body["authentication"]["websocket_ticket_endpoint"] == (
        "/api/auth/ws-ticket"
    )
    assert body["authentication"]["websocket_ticket_query_parameter"] == "ws_ticket"


def test_desktop_backend_advertises_rust_control_sidecar(monkeypatch):
    from api.routers.speech_platform import speech_capabilities

    monkeypatch.setenv("VOICESTUDIO_SPEECH_CONTROL_PORT", "4902")
    body = speech_capabilities().model_dump(by_alias=True)

    assert body["features"]["native_dictation_control"] is True
    assert body["endpoints"]["native_dictation_control"]["path"] == (
        "http://127.0.0.1:4902/v1/capabilities"
    )


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ("EOF", True),
        ('{"type":"input_audio.end"}', True),
        ('{"type":"session.update"}', False),
        ("not-json", False),
        (None, False),
    ],
)
def test_stream_end_control_is_versioned_and_legacy_compatible(frame, expected):
    from api.routers.capture_ws import _is_end_control

    assert _is_end_control(frame) is expected


@pytest.mark.usefixtures("asr_model_installed")
def test_versioned_stream_has_session_envelope(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as capture

    async def fake_full(_chunks, **_kwargs):
        return {
            "text": "platform works",
            "segments": [],
            "language": "en",
            "engine": "stub",
        }

    monkeypatch.setattr(capture, "_transcribe_buffer_full", fake_full)
    app = FastAPI()
    app.include_router(capture.router)
    client = TestClient(app, client=("127.0.0.1", 50000))

    with client.websocket_connect(
        "/v1/audio/transcriptions/stream?pcm=1&sr=16000"
    ) as websocket:
        started = websocket.receive_json()
        assert started["type"] == "session.started"
        assert started["protocol"] == "voicestudio.speech.v1"
        assert started["sample_rate"] == 16000
        session_id = started["session_id"]

        websocket.send_bytes(b"\x00" * 5000)
        websocket.send_json({"type": "input_audio.end"})
        final = websocket.receive_json()

    assert final["type"] == "final"
    assert final["final_kind"] == "summary"
    assert final["session_id"] == session_id
    assert final["protocol"] == "voicestudio.speech.v1"
    assert final["text"] == "Platform works."


def test_versioned_stream_rejects_untrusted_browser_origin_before_accept():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from api.routers import capture_ws as capture

    app = FastAPI()
    app.include_router(capture.router)
    client = TestClient(app, client=("127.0.0.1", 50000))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/v1/audio/transcriptions/stream",
            headers={"Origin": "https://malicious.example"},
        ):
            pass

    assert exc_info.value.code == 1008
