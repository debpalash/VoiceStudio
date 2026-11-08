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
