"""Exercise desktop-exported client identities through the actual MCP transport."""
import json
from contextlib import asynccontextmanager

import pytest

pytest.importorskip('mcp')


@pytest.mark.parametrize('client_id', ['claude-code', 'cursor'])
def test_client_setup_initializes_lists_tools_and_preserves_voice_binding(monkeypatch, tmp_path, client_id):
    import httpx
    import mcp_server
    from services import mcp_bindings
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles
    from starlette.testclient import TestClient

    resolved = []
    monkeypatch.setattr(mcp_bindings, 'resolve_voice', lambda client, profile: (
        resolved.append(client) or {'profile_id': 'test-profile'}
    ))
    monkeypatch.setattr(mcp_bindings, 'touch_last_seen', lambda client: None)
    from urllib.parse import parse_qs
    monkeypatch.delenv('OMNIVOICE_API_URL', raising=False)
    def generate(request):
        # The mounted server calls its own app in-process as a loopback
        # caller: no bind host/port involved.
        assert request.url.host == '127.0.0.1'
        assert request.url.path == '/generate'
        assert parse_qs(request.content.decode())['profile_id'] == ['test-profile']
        return httpx.Response(200, content=b'test-audio', headers={'X-Audio-Id': 'test'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        **{**kwargs, 'transport': httpx.MockTransport(generate)}
    ))
    monkeypatch.setenv('OMNIVOICE_MCP_OUTPUT_MODE', 'resources')
    @asynccontextmanager
    async def lifespan(app):
        async with app.state.mcp_session_manager.run():
            yield
    # The production mount path plus an SPA catch-all at "/", as Docker and
    # source builds serve it: the exported "/mcp" URL must not fall through.
    spa = tmp_path / 'dist'
    spa.mkdir()
    (spa / 'index.html').write_text('<!doctype html>')
    app = Starlette(lifespan=lifespan)
    assert mcp_server.mount_mcp(app)
    app.router.routes.append(Mount('/', app=StaticFiles(directory=spa, html=True)))
    headers = {'Accept': 'application/json, text/event-stream', 'X-OmniVoice-Client-Id': client_id}
    def result(response):
        assert response.status_code == 200, response.text
        if response.headers.get('content-type', '').startswith('text/event-stream'):
            return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
        return response.json()
    with TestClient(app, base_url='http://127.0.0.1:3912', follow_redirects=False) as client:
        initialized = client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2024-11-05', 'capabilities': {},
                'clientInfo': {'name': client_id, 'version': 'test'},
            },
        })
        assert 'serverInfo' in result(initialized)['result']
        headers['Mcp-Session-Id'] = initialized.headers['mcp-session-id']
        assert client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'method': 'notifications/initialized',
        }).status_code == 202
        tools = result(client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list',
        }))
        assert 'generate_speech' in {t['name'] for t in tools['result']['tools']}
        speech = next(t for t in tools['result']['tools'] if t['name'] == 'generate_speech')
        assert speech['inputSchema']['properties']['format']['default'] == 'wav'
        called = result(client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'generate_speech', 'arguments': {'text': 'Hello'}},
        }))
        assert not called['result'].get('isError'), called
        assert resolved == [client_id]


def test_standalone_server_calls_the_backend_on_its_real_port(monkeypatch):
    """Without a mounted app the tools go over HTTP to OMNIVOICE_PORT, not :3900."""
    import asyncio
    import httpx
    import mcp_server

    seen = []

    def health(request):
        seen.append(request.url)
        return httpx.Response(200, json={'status': 'ok'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        **{**kwargs, 'transport': httpx.MockTransport(health)}
    ))
    monkeypatch.setenv('OMNIVOICE_PORT', '3912')
    for name in ('OMNIVOICE_API_URL', 'OMNIVOICE_BIND_HOST'):
        monkeypatch.delenv(name, raising=False)
    server = mcp_server.create_mcp_server()
    asyncio.run(server.call_tool('check_health', {}))
    assert [(u.host, u.port, u.path) for u in seen] == [('127.0.0.1', 3912, '/health')]


def test_standalone_server_authenticates_to_a_keyed_https_backend(monkeypatch):
    import asyncio
    import httpx
    import mcp_server

    seen = []

    def health(request):
        seen.append((str(request.url), request.headers.get('authorization')))
        return httpx.Response(200, json={'status': 'ok'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        **{**kwargs, 'transport': httpx.MockTransport(health)}
    ))
    monkeypatch.setenv('OMNIVOICE_API_URL', 'https://gpu.example/voicestudio')
    monkeypatch.setenv('OMNIVOICE_API_KEY', 'k' * 40)
    server = mcp_server.create_mcp_server()
    asyncio.run(server.call_tool('check_health', {}))
    assert seen == [('https://gpu.example/voicestudio/health', 'Bearer ' + 'k' * 40)]


@pytest.mark.parametrize('backend_status', [200, 503])
def test_remote_compressed_speech_checks_backend_encoder_not_mcp_host(
    monkeypatch, backend_status
):
    import asyncio
    import httpx
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError
    from services import ffmpeg_utils

    seen = []

    def backend(request):
        seen.append((request.url.path, request.headers.get('authorization')))
        if request.url.path.endswith('/generate'):
            return httpx.Response(200, content=b'RIFFwav', headers={'X-Audio-Id': 'ab12cd34'})
        return httpx.Response(backend_status, content=b'OggS', headers={'Content-Type': 'audio/ogg'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        **{**kwargs, 'transport': httpx.MockTransport(backend)}
    ))
    monkeypatch.setattr(ffmpeg_utils, 'find_ffmpeg', lambda: None)
    monkeypatch.delenv('OMNIVOICE_MCP_BASE_PATH', raising=False)
    monkeypatch.setenv('OMNIVOICE_MCP_OUTPUT_MODE', 'files')
    monkeypatch.setenv('OMNIVOICE_API_URL', 'https://gpu.example/voicestudio')
    monkeypatch.setenv('OMNIVOICE_API_KEY', 'k' * 40)
    server = mcp_server.create_mcp_server()
    if backend_status == 503:
        with pytest.raises(ToolError, match='503'):
            asyncio.run(server.call_tool('generate_speech', {'text': 'Hello', 'format': 'opus'}))
    else:
        result = asyncio.run(server.call_tool('generate_speech', {'text': 'Hello', 'format': 'opus'}))
        assert json.loads(result[0][0].text)['audio_url'] == (
            'https://gpu.example/voicestudio/audio/ab12cd34.opus'
        )
    assert seen == [
        ('/voicestudio/generate', 'Bearer ' + 'k' * 40),
        ('/voicestudio/audio/ab12cd34.opus', 'Bearer ' + 'k' * 40),
    ]
