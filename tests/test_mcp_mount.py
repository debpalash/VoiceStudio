"""MCP server mount + tool surface (Wave 2.2).

The build/tool-surface checks need only the FastMCP server (no `main`, so no
torch — these run locally). The mount-on-main check imports `main` and is
validated in CI (local torch/Triton segfault on main-importing tests).
"""
import asyncio
import base64
import json
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

mcp_pkg = pytest.importorskip("mcp")  # skip cleanly if the optional dep is absent


def test_server_builds_with_expected_tools():
    from mcp_server import create_mcp_server

    server = create_mcp_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    # v1 surface: speak, clone, transcribe, and the read-only listers.
    assert {"generate_speech", "clone_voice", "transcribe", "list_voices", "list_personalities",
            "list_languages", "check_health"} <= names


def test_list_voices_returns_valid_json(monkeypatch):
    """Guard the tool's promised JSON format against Python repr output.

    Return a nonempty profile list from a stub API, then parse the real MCP
    reply and compare all values, including booleans, null and nested data.
    """
    from fastapi import FastAPI
    from mcp_server import create_mcp_server

    profiles = [{
        "id": "voice-1",
        "name": "Voice \u2713",
        "kind": "clone",
        "personality": None,
        "ready": True,
        "enabled": False,
        "metadata": {"count": 1},
    }]
    app = FastAPI()

    @app.get("/profiles")
    async def list_profiles():
        """Supply profiles in-process so the test needs no running backend."""
        return profiles

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    server = create_mcp_server(app=app)
    result = asyncio.run(server.call_tool("list_voices", {}))
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text) == profiles


@pytest.fixture
def json_response_server(monkeypatch):
    """Build real MCP servers backed by a configurable in-process stub API.

    This isolates serialization tests from network calls and model inference.
    """
    from fastapi import FastAPI
    from mcp_server import create_mcp_server

    def build(path, payload, method="GET"):
        """Connect an MCP server to a stub endpoint serving the chosen payload."""
        app = FastAPI()

        @app.api_route(path, methods=[method])
        async def response():
            """Return controlled API data to isolate MCP response serialization."""
            return payload

        monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
        return create_mcp_server(app=app)

    return build


@pytest.mark.parametrize("tool,path,payload", [
    ("list_personalities", "/personalities", [
        {"name": "Narrator", "instruct": "Read calmly", "enabled": True},
    ]),
    ("check_health", "/health", {
        "ready": True, "enabled": False, "device": None,
    }),
])
def test_metadata_tools_return_valid_json(json_response_server, tool, path, payload):
    """Keep personality and health responses parseable by JSON clients.

    Feed each tool a stub API response and verify its JSON text decodes to
    the original list or dictionary without changing booleans or nulls.
    """
    server = json_response_server(path, payload)
    result = asyncio.run(server.call_tool(tool, {}))
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text) == payload


def test_transcribe_returns_valid_json(json_response_server):
    """Preserve transcript content when serializing the tool's JSON reply.

    Stub the upload endpoint, call the real tool with placeholder audio,
    and round-trip quotes, a newline, Unicode and metadata without ASR.
    """
    payload = {"text": "She said, \"hello\".\nVoice \u2713", "language": None,
               "duration": 1.25}
    server = json_response_server("/transcribe", payload, method="POST")
    # The stub accepts the upload; no ASR engine reads these placeholder bytes.
    audio = base64.b64encode(b"RIFFxxxxWAVE").decode("ascii")
    result = asyncio.run(server.call_tool("transcribe", {"audio_base64": audio}))
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text) == payload


def test_voice_resource_returns_valid_json(json_response_server):
    """Ensure clients can decode the requested voice's resource as JSON.

    Supply two profiles and read one by URI, checking that selection and
    serialization preserve its Unicode, null and nested metadata.
    """
    profile = {"id": "voice-1", "name": "Voice \u2713", "personality": None,
               "metadata": {"ready": True}}
    server = json_response_server("/profiles", [{"id": "other"}, profile])
    contents = list(asyncio.run(server.read_resource("voice://voice-1")))
    assert json.loads(contents[0].content) == profile


def test_recent_history_returns_valid_json(json_response_server):
    """Keep history parseable while preserving its existing 20-item limit.

    Supply 25 entries through the stub API and verify the resource decodes
    to exactly the first 20 entries in the same order.
    """
    history = [{"id": str(i), "text": "Voice \u2713", "profile_id": None}
               for i in range(25)]
    server = json_response_server("/history", history)
    contents = list(asyncio.run(server.read_resource("history://recent")))
    assert json.loads(contents[0].content) == history[:20]


@pytest.mark.parametrize("profile_id", ["missing", 'missing"quote'])
def test_missing_voice_resource_returns_valid_json(json_response_server, profile_id):
    """Prevent quoted voice IDs from breaking the JSON error response.

    Return no profiles, then read ordinary and quoted missing IDs and
    verify each error parses and preserves the requested ID in its message.
    """
    server = json_response_server("/profiles", [])
    contents = list(asyncio.run(server.read_resource(f"voice://{profile_id}")))
    assert json.loads(contents[0].content) == {
        "error": f"Voice profile {profile_id} not found",
    }


def test_streamable_app_serves_at_root_for_submounting():
    from mcp_server import create_mcp_server

    server = create_mcp_server()
    app = server.streamable_http_app()
    # streamable_http_path was set to "/" so a mount at "/mcp" lands at "/mcp"
    # (not the double-prefixed "/mcp/mcp").
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/" in paths
    assert server.session_manager is not None


def _mount_paths(app) -> set[str]:
    from starlette.routing import Mount
    return {r.path for r in app.routes if isinstance(r, Mount)}


def test_main_mounts_mcp_route(monkeypatch):
    """Importing main wires the /mcp mount.

    Inspect app.routes rather than driving a TestClient — running the app
    lifespan starts the FastMCP session manager, which binds asyncio queues
    to the test's event loop and contaminates later lifespan-running tests
    ("bound to a different event loop"). The mount happens at import time.

    Reload main with the disable flag cleared so this is independent of any
    earlier test that reloaded main (e.g. with OMNIVOICE_MCP_DISABLE set).
    """
    monkeypatch.delenv("OMNIVOICE_MCP_DISABLE", raising=False)
    import importlib
    import main as _main
    importlib.reload(_main)
    assert "/mcp" in _mount_paths(_main.app)
    assert _main.app.state.mcp_transport_security is not None


def test_mcp_disable_env_skips_mount(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MCP_DISABLE", "1")
    import importlib
    import main as _main
    importlib.reload(_main)
    try:
        assert "/mcp" not in _mount_paths(_main.app)
    finally:
        # Restore the default app so other tests see /mcp mounted again.
        monkeypatch.delenv("OMNIVOICE_MCP_DISABLE", raising=False)
        importlib.reload(_main)


# ── clone_voice input helpers (#1195 review) ────────────────────────────────
# Pure helpers, no MCP SDK needed: agents commonly prepend data URIs, and the
# stored ref clip's extension must match the actual container.

def test_decode_ref_audio_strips_data_uri_prefix():
    import base64 as b64
    from mcp_server import _decode_ref_audio
    body = b64.b64encode(b"RIFFxxxxWAVE").decode()
    assert _decode_ref_audio(f"data:audio/wav;base64,{body}") == b"RIFFxxxxWAVE"
    assert _decode_ref_audio(body) == b"RIFFxxxxWAVE"


def test_decode_ref_audio_rejects_garbage_without_raising():
    from mcp_server import _decode_ref_audio
    assert _decode_ref_audio("not!!valid@@base64") is None
    assert _decode_ref_audio("data:audio/wav;base64,%%%") is None


@pytest.mark.parametrize("raw,ext", [
    (b"RIFFxxxxWAVEfmt ", ".wav"),
    (b"fLaC\x00\x00\x00\x22", ".flac"),
    (b"ID3\x04rest-of-mp3", ".mp3"),
    (b"\xff\xfb\x90\x00mp3-frame", ".mp3"),
    (b"OggS\x00vorbis", ".ogg"),
    (b"\x00\x00\x00 ftypM4A ", ".m4a"),
    (b"???unknown-container", ".wav"),  # documented default
])
def test_sniff_audio_ext_matches_magic_bytes(raw, ext):
    from mcp_server import _sniff_audio_ext
    assert _sniff_audio_ext(raw) == ext


def test_mcp_allowed_hosts_env_extends_allowlist(monkeypatch):
    """OMNIVOICE_MCP_ALLOWED_HOSTS must extend the transport-security allowlist."""
    from mcp_server import create_mcp_server

    monkeypatch.setenv("OMNIVOICE_MCP_ALLOWED_HOSTS", "host.containers.internal:*,10.0.0.1:*")
    server = create_mcp_server()
    allowed = server.settings.transport_security.allowed_hosts
    assert "host.containers.internal:*" in allowed
    assert "10.0.0.1:*" in allowed
    origins = server.settings.transport_security.allowed_origins
    assert "http://host.containers.internal:*" in origins
    assert "https://host.containers.internal:*" in origins
