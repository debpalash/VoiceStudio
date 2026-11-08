"""A malformed request is a 422 — never a 500, and never an echo of the body.

FastAPI's default validation handler runs ``jsonable_encoder(exc.errors())``,
and for a body-level failure ``errors()[i]["input"]`` is the RAW REQUEST BODY.
``jsonable_encoder`` decodes ``bytes`` as UTF-8, so posting *any* binary body
to a JSON-body route — e.g. a multipart audio upload aimed at ``/tools/probe``
or ``/design/describe``, which is one wrong path away for MCP / OpenAI-compat
clients — raised ``UnicodeDecodeError`` **inside the error handler**:

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80 in position 154

The client got a 500 for a merely malformed request, and the escaping
exception dumped the whole body into omnivoice.log (a 145 KB WAV wrote ~500 KB
of log — user audio on disk, in the file we invite people to paste into bug
reports).

Fail-before/pass-after: without ``main.validation_exception_handler`` the
binary cases 500, and the oversized-string case mirrors the whole body back.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from main import _VALIDATION_INPUT_MAX, _safe_validation_input, app as real_app
from main import validation_exception_handler
from fastapi.exceptions import RequestValidationError


class _Body(BaseModel):
    path: str


@pytest.fixture
def client():
    """A throwaway app wired to the same handler main.py registers."""
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.post("/echo")
    def echo(body: _Body):  # pragma: no cover - never reached by these tests
        return {"ok": body.path}

    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


# ── The regression ─────────────────────────────────────────────────────────
def test_binary_multipart_to_json_route_is_422_not_500(client):
    r = client.post("/echo", files={"f": ("a.bin", b"\x80\x81\x82\xff" * 64)})

    assert r.status_code == 422, r.text
    # The body never comes back — only its size.
    assert "bytes of binary data" in r.text
    assert "\x80" not in r.text


def test_response_never_mirrors_a_large_body(client):
    big = "x" * 50_000
    r = client.post("/echo", content=big, headers={"Content-Type": "application/json"})

    assert r.status_code in (400, 422)
    assert len(r.content) < 2_000, "a malformed request must not echo its own body"


def test_valid_request_still_reaches_the_route(client):
    r = client.post("/echo", json={"path": "/tmp/x"})
    assert r.status_code == 200
    assert r.json() == {"ok": "/tmp/x"}


def test_ordinary_validation_error_keeps_fastapis_shape(client):
    """Clients and the frontend parse `detail[].loc/msg/type` — unchanged."""
    r = client.post("/echo", json={"wrong": 1})

    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["body", "path"]
    assert err["type"] == "missing"
    assert err["msg"]


def test_handler_is_registered_on_the_real_app():
    """The throwaway app above proves the handler works; this proves main.py
    actually installs it (the bug was purely a missing registration)."""
    assert RequestValidationError in real_app.exception_handlers


@pytest.mark.parametrize("field", [
    "max_audio_only_rate", "audio_rate_cap", "video_slow_cap", "gap_guard_s",
    "min_audio_rate",
])
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
def test_nonfinite_smart_fit_input_is_422_before_engine_resolution(monkeypatch, field, token):
    """Overflowing JSON numbers and Python's accepted NaN tokens must not 500."""
    from api.routers import dub_generate as dg

    resolve = AsyncMock(side_effect=AssertionError("invalid request reached the engine"))
    monkeypatch.setattr(dg, "resolve_generation_backend", resolve)
    app = FastAPI()
    app.include_router(dg.router)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    body = (
        '{"segments":[{"start":0,"end":1,"text":"synthetic fixture"}],'
        '"timing_strategy":"smart_fit","fit_options":{"' + field + '":' + token + '}}'
    )
    with TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False) as client:
        response = client.post(
            "/dub/generate/test-job", content=body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422, response.text
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "fit_options", field]
    assert error["type"] == "finite_number"
    resolve.assert_not_called()


# ── The sanitizer, as a unit ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "value",
    [b"\x80\x81", bytearray(b"\xff\xfe"), memoryview(b"\x00\x80")],
)
def test_binary_inputs_are_described_never_decoded(value):
    out = _safe_validation_input(value)
    assert isinstance(out, str)
    assert out == f"<{len(bytes(value))} bytes of binary data>"


def test_long_strings_are_truncated_with_a_count():
    out = _safe_validation_input("y" * (_VALIDATION_INPUT_MAX + 500))
    assert out.startswith("y" * 10)
    assert "+500 chars" in out
    assert len(out) < _VALIDATION_INPUT_MAX + 40


@pytest.mark.parametrize("value", [None, 3, 1.5, True, {"a": 1}, ["a"], "short"])
def test_ordinary_values_pass_through_untouched(value):
    assert _safe_validation_input(value) == value
