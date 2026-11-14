"""Exercise caller tiers through the installed SDK's HTTP and stdio transports."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

pytest.importorskip('mcp')

KEY = 'test-mcp-operator-key'
ORIGIN = 'http://127.0.0.1:3900'
POST_PATHS = [('inprocess', '127.0.0.1'), ('override', '192.0.2.5'),
              ('override', '127.0.0.1'), ('standalone', '192.0.2.5'), ('standalone', '127.0.0.1')]


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    monkeypatch.setenv('OMNIVOICE_API_KEY', KEY)
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '1')
    for name in ('OMNIVOICE_API_URL', 'OMNIVOICE_TRUSTED_NETWORKS', 'OMNIVOICE_ALLOWED_ORIGINS', 'OMNIVOICE_MCP_ALLOWED_HOSTS'):
        monkeypatch.delenv(name, raising=False)
    from services.admin_sessions import admin_session_store
    admin_session_store.clear()
    yield
    admin_session_store.clear()


def _backend(target_key=None, protected_posts=False):
    from api.dependencies import check_admin

    backend = FastAPI()
    seen = []
    if protected_posts:
        from main import BearerKeyMiddleware
        backend.add_middleware(BearerKeyMiddleware)

    @backend.post('/profiles')
    async def create_profile(request: Request):
        with patch.dict(os.environ, {'OMNIVOICE_API_KEY': target_key} if target_key else {}):
            check_admin(request)
        seen.append({**dict(request.headers), 'test_query': dict(request.query_params)})
        return {'id': 'fixture-profile', 'name': 'Fixture', 'kind': 'clone'}

    @backend.get('/health')
    async def admin_route(request: Request):
        seen.append(dict(request.headers))
        with patch.dict(os.environ, {'OMNIVOICE_API_KEY': target_key} if target_key else {}):
            check_admin(request)
        return {'admin_reached': True}

    return backend, seen


def _decode(response):
    assert response.status_code == 200, response.text
    if response.headers.get('content-type', '').startswith('text/event-stream'):
        return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
    return response.json()


def _http_tool(monkeypatch, mode, *, headers=None, query='', broken_context=False, target_key=None, target_host='192.0.2.5', backend_url='https://configured-backend.example', post=False):
    import mcp_server
    backend, seen = _backend(target_key, protected_posts=post)
    if mode != 'inprocess':
        if mode == 'override':
            monkeypatch.setenv('OMNIVOICE_API_URL', backend_url)
        # Keep the real HTTP client, headers and authorization gate. Replace
        # only the wire; no test may contact an external backend.
        original = httpx.AsyncClient
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
            **{**kwargs, 'transport': httpx.ASGITransport(app=backend, client=(target_host, 50000))}
        ))
    server = mcp_server.create_mcp_server(app=None if mode == 'standalone' else backend)
    transport = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount('/mcp', transport)
    all_headers = {'Accept': 'application/json, text/event-stream', **(headers or {})}
    url = '/mcp/' + query
    with TestClient(app, base_url='http://127.0.0.1:3900', client=('192.0.2.4', 50000)) as client:
        response = client.post(url, headers=all_headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2024-11-05', 'capabilities': {},
                'clientInfo': {'name': 'identity-regression', 'version': '1'},
            },
        })
        assert 'serverInfo' in _decode(response)['result']
        all_headers['Mcp-Session-Id'] = response.headers['mcp-session-id']
        assert client.post(url, headers=all_headers, json={
            'jsonrpc': '2.0', 'method': 'notifications/initialized',
        }).status_code == 202
        if broken_context:
            class FailedTransport:
                @property
                def request(self):
                    raise RuntimeError('request context lookup failed')
            monkeypatch.setattr(server, 'get_context', lambda: SimpleNamespace(request_context=FailedTransport()))
        arguments = {'name': 'Fixture', 'ref_audio_base64': base64.b64encode(b'RIFF\x00\x00\x00\x00WAVEfmt ').decode()} if post else {}
        result = _decode(client.post(url, headers=all_headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'clone_voice' if post else 'check_health', 'arguments': arguments},
        }))['result']
    return result, seen


@pytest.mark.parametrize('mode,target_host', POST_PATHS)
@pytest.mark.parametrize('credential', ['master', 'bearer_session', 'query', 'session_cookie', 'legacy_cookie'])
def test_actual_post_tool_preserves_valid_explicit_or_csrf_proved_credentials(monkeypatch, mode, target_host, credential):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    proof = {'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'}
    headers, query = {
        'master': ({'Authorization': f'Bearer {KEY}'}, ''),
        'bearer_session': ({'Authorization': f'Bearer {token}'}, ''),
        'query': ({'Cookie': 'ov_session=expired'}, f'?api_key={KEY}'),
        'session_cookie': ({**proof, 'Cookie': f'ov_session={token}; unrelated=private'}, ''),
        'legacy_cookie': ({**proof, 'Cookie': f'ov_key={KEY}; unrelated=private'}, ''),
    }[credential]
    monkeypatch.setenv('OMNIVOICE_ALLOWED_ORIGINS', ORIGIN)
    result, seen = _http_tool(monkeypatch, mode, headers=headers, query=query, target_host=target_host, post=True)
    assert not result.get('isError'), result
    assert len(seen) == 1
    assert 'fixture-profile' in result['content'][0]['text']
    if mode != 'inprocess':
        assert seen[0].get('origin') == headers.get('Origin')
        assert 'unrelated' not in seen[0].get('cookie', '')
        if credential == 'query':
            assert seen[0]['test_query'] == {'api_key': KEY}


@pytest.mark.parametrize('mode,target_host', POST_PATHS)
@pytest.mark.parametrize('credential', ['session_cookie', 'legacy_cookie'])
@pytest.mark.parametrize('proof', ['missing_origin', 'missing_csrf', 'wrong_csrf', 'foreign_origin'])
def test_actual_post_tool_never_promotes_untrusted_ambient_cookies(monkeypatch, mode, target_host, credential, proof):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    headers = {'Cookie': f'ov_session={token}' if credential == 'session_cookie' else f'ov_key={KEY}'}
    if proof != 'missing_origin':
        headers['Origin'] = 'http://foreign-ui.test:3900' if proof == 'foreign_origin' else ORIGIN
    if proof != 'missing_csrf':
        headers['X-VoiceStudio-CSRF'] = 'wrong' if proof == 'wrong_csrf' else '1'
    # Permit the foreign Origin through the real MCP transport so the test
    # reaches the backend's independent origin decision rather than crediting
    # an earlier SDK rejection for it.
    monkeypatch.setenv('OMNIVOICE_MCP_ALLOWED_HOSTS', 'foreign-ui.test:*')
    monkeypatch.setenv('OMNIVOICE_ALLOWED_ORIGINS', ORIGIN)
    result, seen = _http_tool(monkeypatch, mode, headers=headers, target_host=target_host, post=True)
    assert result.get('isError') or 'error' in result['content'][0]['text'], result
    assert seen == []


@pytest.mark.parametrize('mode,target_host', POST_PATHS)
@pytest.mark.parametrize('higher_priority', ['wrong_header', 'session_in_query'])
def test_actual_post_preserves_precedence_and_query_master_only_semantics(monkeypatch, mode, target_host, higher_priority):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    headers = {'Cookie': f'ov_key={KEY}', 'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'}
    query = f'?api_key={token}'
    if higher_priority == 'wrong_header':
        headers['Authorization'] = 'Bearer wrong'
        query = f'?api_key={KEY}'
    monkeypatch.setenv('OMNIVOICE_ALLOWED_ORIGINS', ORIGIN)
    result, seen = _http_tool(monkeypatch, mode, headers=headers, query=query, target_host=target_host, post=True)
    assert result.get('isError') or 'error' in result['content'][0]['text'], result
    assert seen == []


@pytest.mark.parametrize('mode', ['inprocess', 'override', 'standalone'])
@pytest.mark.parametrize('credential', ['none', 'pin', 'wrong'])
def test_http_callers_cannot_borrow_loopback_or_server_master_key(monkeypatch, mode, credential):
    headers = {'pin': {'X-OmniVoice-Pin': '123456'},
               'wrong': {'Authorization': 'Bearer wrong-key'}, 'none': {}}[credential]
    result, _ = _http_tool(monkeypatch, mode, headers=headers)
    assert result.get('isError'), result


@pytest.mark.parametrize('mode', ['inprocess', 'override', 'standalone'])
def test_context_failure_never_becomes_a_privileged_stdio_call(monkeypatch, mode):
    result, _ = _http_tool(monkeypatch, mode, broken_context=True)
    assert result.get('isError'), result


@pytest.mark.parametrize('mode', ['override', 'standalone'])
def test_a_key_for_the_mcp_host_cannot_borrow_another_loopback_backends_authority(monkeypatch, mode):
    result, _ = _http_tool(monkeypatch, mode, headers={'Authorization': f'Bearer {KEY}'},
                           target_key='different-backend-key', target_host='127.0.0.1')
    assert result.get('isError'), result


@pytest.mark.parametrize('mode', ['inprocess', 'override', 'standalone'])
def test_wrong_explicit_authorization_never_falls_back_to_a_valid_cookie(monkeypatch, mode):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    result, _ = _http_tool(monkeypatch, mode, headers={
        'Authorization': 'Bearer wrong-key', 'Cookie': f'ov_session={token}',
    })
    assert result.get('isError'), result


@pytest.mark.parametrize('headers', [
    {'Authorization': f'Bearer {KEY}'},
    {'Cookie': f'ov_key={KEY}'},
    {'X-OmniVoice-Pin': '123456'},
])
def test_credentials_never_cross_to_a_remote_plaintext_backend(monkeypatch, headers):
    result, seen = _http_tool(monkeypatch, 'override', headers=headers,
                              backend_url='http://192.0.2.5:3900')
    assert result.get('isError'), result
    assert seen == []


def test_query_master_key_never_crosses_to_a_remote_plaintext_backend(monkeypatch):
    result, seen = _http_tool(monkeypatch, 'override', query=f'?api_key={KEY}',
                              backend_url='http://192.0.2.5:3900')
    assert result.get('isError'), result
    assert seen == []


@pytest.mark.parametrize('credential', ['master', 'session', 'legacy', 'query'])
def test_inprocess_http_admin_credentials_retain_access(monkeypatch, credential):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    headers, query = {
        'master': ({'Authorization': f'Bearer {KEY}'}, ''),
        'session': ({'Cookie': f'ov_session={token}', 'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'}, ''),
        'legacy': ({'Cookie': f'ov_key={KEY}', 'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'}, ''),
        'query': ({}, f'?api_key={KEY}'),
    }[credential]
    result, _ = _http_tool(monkeypatch, 'inprocess', headers=headers, query=query)
    assert not result.get('isError'), result


@pytest.mark.parametrize('mode', ['override', 'standalone'])
@pytest.mark.parametrize('credential', ['bearer', 'query', 'session', 'legacy'])
def test_http_backend_receives_the_callers_own_credential(monkeypatch, mode, credential):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    headers, query = {
        'bearer': ({'Authorization': f'Bearer {token}'}, ''),
        'query': ({}, f'?api_key={KEY}'),
        'session': ({'Cookie': f'ov_session={token}; unrelated=private'}, ''),
        'legacy': ({'Cookie': f'ov_key={KEY}; unrelated=private'}, ''),
    }[credential]
    result, seen = _http_tool(monkeypatch, mode, headers=headers, query=query)
    assert not result.get('isError'), result
    assert seen
    if credential == 'bearer':
        assert seen[0]['authorization'] == f'Bearer {token}'
    elif credential == 'session':
        assert token in seen[0].get('cookie', '')
        assert 'authorization' not in seen[0]
    assert 'unrelated' not in seen[0].get('cookie', '')


@pytest.mark.parametrize('mode', ['inprocess', 'http'])
@pytest.mark.parametrize('post', [False, True])
def test_real_stdio_session_retains_local_operator_access(monkeypatch, mode, post):
    import mcp_server
    from mcp.shared.memory import create_connected_server_and_client_session
    backend, seen = _backend(protected_posts=post)
    if mode == 'http':
        monkeypatch.setenv('OMNIVOICE_API_URL', 'https://configured-backend.example')
        original = httpx.AsyncClient
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
            **{**kwargs, 'transport': httpx.ASGITransport(app=backend, client=('192.0.2.5', 50000))}
        ))
    server = mcp_server.create_mcp_server(app=backend)

    async def run():
        async with create_connected_server_and_client_session(server, read_timeout_seconds=timedelta(seconds=5)) as session:
            arguments = {'name': 'Fixture', 'ref_audio_base64': base64.b64encode(b'RIFF\x00\x00\x00\x00WAVEfmt ').decode()} if post else {}
            return await session.call_tool('clone_voice' if post else 'check_health', arguments)

    result = asyncio.run(run())
    assert not result.isError, result
    assert 'x-omnivoice-delegated' not in seen[0]
    if mode == 'http':
        assert seen[0]['authorization'] == f'Bearer {KEY}'
