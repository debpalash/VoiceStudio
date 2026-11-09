"""Local CLI requests keep headers framed and endpoint checks DNS-free."""
from email import policy
from email.parser import BytesParser
import socket

import pytest


def test_multipart_filename_cannot_add_headers_and_fields_are_not_rewritten():
    from speech_client.__main__ import _encode_multipart

    audio = b"RIFF-synthetic-audio"
    fields = {"model": "whisper-1", "prompt": "First line\r\nSecond line"}
    body, content_type = _encode_multipart(
        filename='clip.wav"\r\nX-Injected: yes', audio=audio,
        fields=fields, boundary="synthetic-boundary",
    )
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    parts = list(message.iter_parts())
    assert len(parts) == 3
    assert not any(part.get("X-Injected") for part in parts)
    assert parts[-1].get_payload(decode=True) == audio
    assert parts[1].get_payload(decode=True) == fields["prompt"].encode()
    assert "\r" not in parts[-1].get_filename()
    assert "\n" not in parts[-1].get_filename()


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "127.1"])
def test_numeric_loopback_aliases_are_local_without_dns(monkeypatch, host):
    from speech_client import __main__ as client

    original = socket.getaddrinfo
    calls = []

    def numeric_only(*args, **kwargs):
        assert kwargs.get("flags", 0) & socket.AI_NUMERICHOST
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", numeric_only)
    assert client._is_loopback_host(host)
    assert calls == [host]


@pytest.mark.parametrize("host", ["8.8.8.8", "gpu.example", "localhost.evil.example"])
def test_remote_addresses_never_gain_loopback_credentials(monkeypatch, host):
    from speech_client import __main__ as client

    def no_dns(*args, **kwargs):
        assert kwargs.get("flags", 0) & socket.AI_NUMERICHOST
        raise socket.gaierror("synthetic nonnumeric host")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    assert not client._is_loopback_host(host)



@pytest.mark.parametrize(
    ("url", "proxied"),
    [
        ("http://127.1:3900/v1/x", False),
        ("http://localhost:3900/v1/x", False),
        # e.g. the target of a redirect from an uncredentialed loopback request
        ("http://gpu.example/v1/x", True),
    ],
)
def test_only_loopback_requests_bypass_environment_proxies(monkeypatch, url, proxied):
    from urllib import error, request
    from speech_client import __main__ as client

    monkeypatch.setenv("http_proxy", "http://proxy.example:8080")
    monkeypatch.setenv("no_proxy", "")
    seen = []

    class _Record(request.HTTPHandler):
        def http_open(self, req):
            seen.append(req.has_proxy())
            raise error.URLError("stop before network")

    opener = request.build_opener(client._LoopbackDirectProxyHandler(), _Record())
    with pytest.raises(error.URLError):
        opener.open(request.Request(url))
    assert seen == [proxied]
