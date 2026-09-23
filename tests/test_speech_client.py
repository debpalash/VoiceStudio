from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_url_join_does_not_duplicate_slashes():
    from speech_client.__main__ import _join_url

    assert _join_url("http://127.0.0.1:3902/", "/v1/status") == (
        "http://127.0.0.1:3902/v1/status"
    )


def test_multipart_matches_openai_audio_contract():
    from speech_client.__main__ import _encode_multipart

    body, content_type = _encode_multipart(
        filename='sample.wav',
        audio=b"RIFF-audio",
        fields={"model": "whisper-1", "response_format": "text"},
        boundary="fixed-boundary",
    )

    assert content_type == "multipart/form-data; boundary=fixed-boundary"
    assert b'name="model"\r\n\r\nwhisper-1' in body
    assert b'name="response_format"\r\n\r\ntext' in body
    assert b'name="file"; filename="sample.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"RIFF-audio" in body
    assert body.endswith(b"--fixed-boundary--\r\n")


def test_json_transcription_response_extracts_insertable_text():
    from speech_client.__main__ import _response_text

    assert _response_text(b'{"text":"hello"}', "application/json") == "hello"
    assert _response_text(b"hello", "text/plain") == "hello"


def test_client_rejects_non_http_url_handlers():
    from urllib import request

    from speech_client.__main__ import SpeechClientError, _open

    with pytest.raises(SpeechClientError, match="must use http"):
        _open(request.Request("file:///etc/passwd"))


@pytest.mark.parametrize(
    "path",
    [
        "/Users/alice/Private/voice.wav",
        r"C:\Users\Alice\Private\voice.wav",
    ],
)
def test_audio_read_errors_hide_parent_directories(monkeypatch, path):
    from pathlib import Path

    from speech_client.__main__ import SpeechClientError, _read_audio

    def denied(_self):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(SpeechClientError) as exc_info:
        _read_audio(path, "audio.wav")

    message = str(exc_info.value)
    assert "voice.wav" in message
    assert "alice" not in message.lower()
    assert "users" not in message.lower()


def test_remote_bearer_rejects_plain_http_before_network():
    from urllib import request

    from speech_client.__main__ import SpeechClientError, _open

    req = request.Request(
        "http://gpu.example/v1/audio/transcriptions",
        headers={"Authorization": "Bearer secret"},
    )
    with pytest.raises(SpeechClientError, match="require https"):
        _open(req)


def test_credentialed_redirects_are_rejected():
    from speech_client.__main__ import SpeechClientError, _RejectCredentialRedirect

    handler = _RejectCredentialRedirect()
    with pytest.raises(SpeechClientError, match="credentialed redirect"):
        handler.redirect_request(None, None, 307, "redirect", {}, "https://other.test")


def test_interrupt_releases_focused_output_session(monkeypatch):
    from speech_client import __main__ as client

    calls = []

    def fake_json(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "POST" and url.endswith("/v1/output/sessions"):
            return {"session_id": 42}
        return {"ok": True}

    monkeypatch.setattr(client, "_read_audio", lambda *_args: (b"audio", "audio.wav"))
    monkeypatch.setattr(client, "_json_request", fake_json)
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "_open", interrupt)
    args = SimpleNamespace(
        audio="ignored.wav",
        stdin_filename="audio.wav",
        model="whisper-1",
        response_format="text",
        language=None,
        insert=True,
        control_url="http://127.0.0.1:3902",
        engine_url="http://127.0.0.1:3900",
    )

    with pytest.raises(KeyboardInterrupt):
        client._transcribe(args)

    assert calls[-1][0] == "DELETE"
    assert calls[-1][1].endswith("/v1/output/sessions/42")


@pytest.mark.parametrize("env,expected", [
    ({}, "http://127.0.0.1:3900"),
    ({"OMNIVOICE_PORT": "3912"}, "http://127.0.0.1:3912"),
    ({"OMNIVOICE_PORT": "nope"}, "http://127.0.0.1:3900"),
    ({"OMNIVOICE_PORT": "3912", "VOICESTUDIO_URL": "http://gpu:4000"}, "http://gpu:4000"),
])
def test_engine_url_follows_the_backend_port(monkeypatch, env, expected):
    """A backend moved with OMNIVOICE_PORT (as Electron does) is found by default."""
    for name in ("OMNIVOICE_PORT", "VOICESTUDIO_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from speech_client.__main__ import _parser

    assert _parser().parse_args(["status"]).engine_url == expected
