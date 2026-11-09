"""MCP tools must pass on the backend's explanation of a failed request.

`transcribe` against a TTS-only install answered an agent with only
"Client error '403 Forbidden' for url 'http://127.0.0.1/transcribe'" — the
backend had refused with a typed `model_licence_required` error telling the
user which licence to accept, but `raise_for_status()` dropped the body (and
the in-process transport's URL has no port, which made it read like a
misrouted call). Every tool that goes through the shared request helpers had
the same blind spot, so these tests cover the helpers, not just `transcribe`.
"""
import asyncio
import base64
import json

import httpx
import pytest

pytest.importorskip("mcp")

LICENCE_MESSAGE = (
    "Licence not accepted for csukuangfj/sherpa-onnx-whisper-tiny. "
    "Review and accept it in Model Catalogue."
)
LICENCE_DETAIL = {
    "code": "model_licence_required",
    "message": LICENCE_MESSAGE,
    "models": [{"repo_id": "csukuangfj/sherpa-onnx-whisper-tiny"}],
}
AUDIO = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEfmt ").decode()


@pytest.fixture
def failing_backend(monkeypatch):
    """An MCP server whose backend refuses every call, each in another error shape."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, PlainTextResponse
    import mcp_server

    backend = FastAPI()

    @backend.post("/transcribe")
    async def transcribe():
        """Refuse like a TTS-only install: a typed licence error object."""
        return JSONResponse({"detail": LICENCE_DETAIL}, status_code=403)

    @backend.post("/generate")
    async def generate():
        """Refuse with a plain-string detail."""
        return JSONResponse({"detail": "Voice profile 'gone' not found"}, status_code=404)

    @backend.post("/profiles")
    async def create_profile():
        """Refuse cloning with the same typed licence error."""
        return JSONResponse({"detail": LICENCE_DETAIL}, status_code=403)

    @backend.get("/profiles")
    async def list_profiles():
        """Refuse with a FastAPI validation-error list."""
        return JSONResponse(
            {"detail": [{"loc": ["query", "limit"], "msg": "Input should be a valid integer"}]},
            status_code=422,
        )

    @backend.get("/health")
    async def health():
        """Fail like a broken proxy: no JSON body at all."""
        return PlainTextResponse("upstream proxy exploded", status_code=502)

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    return mcp_server.create_mcp_server(app=backend)


def _tool_error(server, tool, **arguments) -> str:
    """The ToolError message a failing tool call raises."""
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as raised:
        asyncio.run(server.call_tool(tool, arguments))
    return str(raised.value)


def _result(server, tool, **arguments) -> dict:
    """A tool call's JSON result."""
    reply = asyncio.run(server.call_tool(tool, arguments))
    blocks = reply[0] if isinstance(reply, tuple) else reply
    return json.loads(blocks[0].text)


def test_transcribe_reports_the_licence_the_user_must_accept(failing_backend):
    """The agent learns which licence to accept, not just "403"."""
    message = _tool_error(failing_backend, "transcribe", audio_base64=AUDIO)
    assert LICENCE_MESSAGE in message
    assert "HTTP 403" in message and "/transcribe" in message


def test_generate_speech_reports_the_backend_reason(failing_backend):
    """A plain-string detail reaches the agent verbatim."""
    message = _tool_error(failing_backend, "generate_speech", text="hi", profile_id="gone")
    assert "Voice profile 'gone' not found" in message


def test_validation_error_lists_are_readable(failing_backend):
    """A validation-error list becomes its readable messages."""
    message = _tool_error(failing_backend, "list_voices")
    assert "Input should be a valid integer" in message


def test_body_without_detail_keeps_the_httpx_message(failing_backend):
    """With no detail to add, httpx's own message is kept."""
    message = _tool_error(failing_backend, "check_health")
    assert "502 Bad Gateway" in message


def test_clone_voice_error_is_the_message_not_a_dict_repr(failing_backend):
    """clone_voice's error JSON carries the typed error's message."""
    result = _result(failing_backend, "clone_voice", name="me", ref_audio_base64=AUDIO)
    assert result == {"error": LICENCE_MESSAGE}


def test_helper_raises_the_same_exception_type_with_the_response():
    """Existing `except httpx.HTTPStatusError` handlers keep working."""
    import httpx
    import mcp_server

    request = httpx.Request("POST", "http://127.0.0.1/transcribe")
    response = httpx.Response(403, json={"detail": LICENCE_DETAIL}, request=request)
    with pytest.raises(httpx.HTTPStatusError) as raised:
        mcp_server._raise_for_backend_status(response)
    assert raised.value.response is response
    assert LICENCE_MESSAGE in str(raised.value)

    ok = httpx.Response(200, json={"text": "hello"}, request=request)
    mcp_server._raise_for_backend_status(ok)  # success passes through untouched


def test_redirect_is_a_failure_not_empty_audio():
    """The clients don't follow redirects, so a proxy's 3xx (to HTTPS or a
    login page) must fail the call the way raise_for_status() always did —
    not hand an empty or HTML body to generate_speech as WAV audio."""
    import httpx
    import mcp_server

    request = httpx.Request("POST", "http://proxy.example/generate")
    redirect = httpx.Response(
        302, headers={"Location": "https://proxy.example/login"}, request=request
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        mcp_server._raise_for_backend_status(redirect)
    assert raised.value.response is redirect


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("Plain reason", "Plain reason"),
        ({"code": "x", "message": "Readable"}, "Readable"),
        ({"code": "x", "message": {"nested": 1}}, '{"code": "x", "message": {"nested": 1}}'),
        ({"code": "x", "message": ""}, '{"code": "x", "message": ""}'),
        ([{"msg": "First"}, {"msg": 2}, "bare"], 'First; {"msg": 2}; bare'),
        ({"count": 3}, '{"count": 3}'),
    ],
)
def test_detail_shapes_render_as_text_or_json_never_a_repr(detail, expected):
    """Non-string messages come out as JSON, not Python reprs like ``{'nested': 1}``."""
    import mcp_server

    request = httpx.Request("GET", "http://127.0.0.1/x")
    response = httpx.Response(400, json={"detail": detail}, request=request)
    assert mcp_server._backend_error_detail(response) == expected


def test_missing_or_empty_detail_is_none():
    """No detail to add means the caller keeps httpx's own message."""
    import mcp_server

    request = httpx.Request("GET", "http://127.0.0.1/x")
    for body in ({"detail": ""}, {"detail": None}, {"other": 1}, ["detail"]):
        assert mcp_server._backend_error_detail(
            httpx.Response(400, json=body, request=request)
        ) is None


class _CountingStream(httpx.AsyncByteStream):
    """An async byte stream that records how many chunks were pulled from it."""

    def __init__(self, chunks):
        """Serve ``chunks`` in order."""
        self.chunks, self.pulled = chunks, 0

    async def __aiter__(self):
        """Yield each chunk, counting as it goes."""
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk

    async def aclose(self):
        """Nothing to release."""


def _streamed(status, chunks, content_type="application/json"):
    """A streamed httpx response, as ``client.stream()`` hands it over."""
    import httpx

    stream = _CountingStream(chunks)
    request = httpx.Request("GET", "http://127.0.0.1/audio/a1.opus")
    return httpx.Response(
        status, headers={"content-type": content_type}, stream=stream, request=request
    ), stream


def test_streamed_error_keeps_a_small_json_detail():
    """A streamed 4xx still surfaces the backend's detail."""
    import httpx
    import mcp_server

    body = json.dumps({"detail": "Encoder unavailable"}).encode()
    response, _stream = _streamed(503, [body[:10], body[10:]])
    buffered = asyncio.run(mcp_server._buffered_error_response(response))
    with pytest.raises(httpx.HTTPStatusError, match="Encoder unavailable"):
        mcp_server._raise_for_backend_status(buffered)


def test_streamed_error_body_is_read_only_up_to_the_cap():
    """A huge error page is cut off at the cap and dropped, not buffered whole."""
    import httpx
    import mcp_server

    chunk = b"x" * 16 * 1024
    response, stream = _streamed(502, [chunk] * 640, content_type="text/html")  # 10 MiB
    buffered = asyncio.run(mcp_server._buffered_error_response(response))
    assert buffered.content == b""
    assert stream.pulled * len(chunk) <= mcp_server._MAX_ERROR_BODY_BYTES + len(chunk)
    with pytest.raises(httpx.HTTPStatusError, match="502 Bad Gateway"):
        mcp_server._raise_for_backend_status(buffered)


def test_passed_on_detail_hides_secrets_and_home_paths():
    """The agent may be a hosted model: an echoed token or home path in the
    backend's detail must not reach it, and a huge body is capped."""
    import httpx
    import mcp_server

    token = "hf_" + "A" * 34
    request = httpx.Request("POST", "http://127.0.0.1/profiles")
    response = httpx.Response(
        400,
        json={"detail": f"Cannot read /Users/alice/clips/ref.wav with token {token}"},
        request=request,
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        mcp_server._raise_for_backend_status(response)
    message = str(raised.value)
    assert "Cannot read ~/clips/ref.wav" in message
    assert "alice" not in message and token not in message

    huge = httpx.Response(500, text="x" * 50_000, request=request)
    capped = mcp_server._public_error_text(huge.text)
    assert len(capped) <= mcp_server._ERROR_DETAIL_MAX_CHARS + 1


def test_clone_voice_plain_text_error_is_scrubbed(monkeypatch):
    """The raw-body fallback (no JSON detail) is scrubbed the same way."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    import mcp_server

    backend = FastAPI()

    @backend.post("/profiles")
    async def create_profile():
        """Fail with a plain-text body that quotes a home path."""
        return PlainTextResponse("disk full at /home/bob/omnivoice_data", status_code=507)

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    server = mcp_server.create_mcp_server(app=backend)
    result = _result(server, "clone_voice", name="me", ref_audio_base64=AUDIO)
    assert result == {"error": "disk full at ~/omnivoice_data"}


HOME_PATHS = {
    "windows": (r"C:\Users\alice\clips\ref.wav", r"~\clips\ref.wav"),
    "macos": ("/Users/alice/clips/ref.wav", "~/clips/ref.wav"),
    "linux": ("/home/alice/clips/ref.wav", "~/clips/ref.wav"),
}
STRUCTURED_SHAPES = {
    "nested message": lambda p: {"code": "x", "message": {"path": p}},
    "validation msg": lambda p: [{"msg": {"path": p}}],
    "dict key": lambda p: {"code": "x", "message": {p: "missing"}},
}


@pytest.mark.parametrize("platform", sorted(HOME_PATHS))
@pytest.mark.parametrize("shape", sorted(STRUCTURED_SHAPES))
def test_structured_detail_hides_home_paths_on_every_platform(platform, shape):
    """Strings inside a non-string detail are scrubbed before JSON encoding.

    json.dumps doubles backslashes, so a Windows home path scrubbed only after
    encoding slipped through while macOS and Linux paths were caught."""
    import mcp_server

    raw, scrubbed = HOME_PATHS[platform]
    request = httpx.Request("GET", "http://127.0.0.1/x")
    response = httpx.Response(
        400, json={"detail": STRUCTURED_SHAPES[shape](raw)}, request=request
    )
    text = mcp_server._backend_error_detail(response)
    assert text is not None
    assert "alice" not in text
    assert json.dumps(scrubbed)[1:-1] in text


@pytest.mark.parametrize("platform", sorted(HOME_PATHS))
def test_plain_text_json_fallback_hides_home_paths(platform, monkeypatch):
    """A JSON body without ``detail`` (the raw-text fallback) is scrubbed the same way."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    import mcp_server

    raw, scrubbed = HOME_PATHS[platform]
    backend = FastAPI()

    @backend.post("/profiles")
    async def create_profile():
        """Fail with a JSON body that has no ``detail`` key but quotes a home path."""
        return JSONResponse({"error": f"cannot read {raw}"}, status_code=500)

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    server = mcp_server.create_mcp_server(app=backend)
    error = _result(server, "clone_voice", name="me", ref_audio_base64=AUDIO)["error"]
    assert "alice" not in error
    assert json.loads(error) == {"error": f"cannot read {scrubbed}"}


def test_streamed_redirect_raises_without_reading_the_body():
    """A streamed 3xx fails at once on the original response: no body read
    (a slow one would stall the call) and its ``Location`` header is kept."""
    import mcp_server

    response, stream = _streamed(302, [b"x" * 1024] * 4, content_type="text/html")
    response.headers["Location"] = "https://proxy.example/login"
    with pytest.raises(httpx.HTTPStatusError) as raised:
        asyncio.run(mcp_server._raise_for_streamed_status(response))
    assert stream.pulled == 0
    assert raised.value.response is response
    assert raised.value.response.headers["Location"] == "https://proxy.example/login"


def test_streamed_success_passes_and_streamed_error_keeps_its_detail():
    """A streamed 2xx passes untouched; a streamed 4xx/5xx still carries its detail."""
    import mcp_server

    ok, ok_stream = _streamed(200, [b"OggS"])
    asyncio.run(mcp_server._raise_for_streamed_status(ok))
    assert ok_stream.pulled == 0

    body = json.dumps({"detail": "Encoder unavailable"}).encode()
    failed, _stream = _streamed(503, [body])
    with pytest.raises(httpx.HTTPStatusError, match="Encoder unavailable"):
        asyncio.run(mcp_server._raise_for_streamed_status(failed))
