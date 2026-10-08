from __future__ import annotations

import pytest


def test_url_join_does_not_duplicate_slashes():
    from speech_client.__main__ import _join_url

    assert _join_url("http://127.0.0.1:3900/", "/v1/audio/transcriptions") == (
        "http://127.0.0.1:3900/v1/audio/transcriptions"
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


@pytest.mark.parametrize(
    "argv",
    [["start"], ["stop"], ["toggle"], ["status"], ["transcribe", "x.wav", "--insert"]],
)
def test_retired_native_control_fails_clearly_without_network(monkeypatch, capsys, argv):
    """These targeted the retired Tauri control server on :3902 — now the
    Electron dev server's port. They must explain instead of calling it."""
    from speech_client import __main__ as client

    def no_network(*_args, **_kwargs):
        raise AssertionError("retired commands must not reach the network")

    monkeypatch.setattr(client, "_open", no_network)
    monkeypatch.setattr(client, "_json_request", no_network)
    monkeypatch.setattr(client, "_read_audio", no_network)

    assert client.main(argv) == 2
    assert "native dictation control is not available" in capsys.readouterr().err


def test_capabilities_reads_the_backend_discovery_document(monkeypatch, capsys):
    from speech_client import __main__ as client

    calls = []
    monkeypatch.setattr(
        client, "_json_request", lambda method, url, payload=None: calls.append((method, url)) or {"ok": 1}
    )
    assert client.main(["--engine-url", "http://127.0.0.1:3912", "capabilities"]) == 0
    assert calls == [("GET", "http://127.0.0.1:3912/.well-known/voicestudio-speech")]


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

    assert _parser().parse_args(["capabilities"]).engine_url == expected
