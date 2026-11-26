"""The selective gzip middleware: compresses big JSON, leaves media alone, and
honours Accept-Encoding q-values (``gzip;q=0`` means the client refuses gzip)."""
import zlib

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from main import _SelectiveGZipMiddleware

_BIG = {"models": ["x" * 64] * 64}  # well over the 1024-byte minimum


def _client():
    async def big(_request):
        return JSONResponse(_BIG)

    async def media(_request):
        return Response(b"RIFF" + b"\0" * 4096, media_type="audio/wav")

    app = Starlette(
        routes=[
            Route("/big", big),
            Route("/audio/a.wav", media),
            Route("/voice_audio/a.wav", media),
            Route("/demo_audio/a.wav", media),
        ]
    )
    app.add_middleware(_SelectiveGZipMiddleware, minimum_size=1024)
    return TestClient(app)


def _raw(client, path, accept):
    with client.stream("GET", path, headers={"Accept-Encoding": accept}) as res:
        return res.headers.get("content-encoding"), b"".join(res.iter_raw())


def test_large_json_is_valid_gzip():
    encoding, raw = _raw(_client(), "/big", "gzip")
    assert encoding == "gzip"
    assert zlib.decompress(raw, 31).startswith(b'{"models"')
    assert _client().get("/big", headers={"Accept-Encoding": "gzip"}).json() == _BIG


@pytest.mark.parametrize("path", ["/audio/a.wav", "/voice_audio/a.wav", "/demo_audio/a.wav"])
def test_media_mounts_are_never_compressed(path):
    encoding, raw = _raw(_client(), path, "gzip")
    assert encoding is None
    assert raw.startswith(b"RIFF")


@pytest.mark.parametrize(
    "header", ["gzip;q=0", "gzip; q=0.0, identity", "identity", "*;q=0", "br, gzip;q=0, *;q=1"]
)
def test_a_client_refusing_gzip_gets_identity(header):
    encoding, raw = _raw(_client(), "/big", header)
    assert encoding is None
    assert raw.startswith(b'{"models"')


@pytest.mark.parametrize("header", ["gzip", "gzip;q=0.5", "br, gzip;q=1"])
def test_a_client_accepting_gzip_gets_it(header):
    encoding, _raw_body = _raw(_client(), "/big", header)
    assert encoding == "gzip"
