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


def _http_tool(monkeypatch, mode, *, headers=None, query='', broken_context=False, target_key=None, target_host='192.0.2.5', backend_url='https://configured-backend.example', post=False, backend_fixture=None, caller_host='192.0.2.4', tool_name=None, protect_transport=False):
    import mcp_server
    backend, seen = backend_fixture if backend_fixture is not None else _backend(target_key, protected_posts=post)
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
    if protect_transport:
        from main import BearerKeyMiddleware, NetworkAccessMiddleware
        from core.browser_guard import BrowserGuardMiddleware
        app.state.network_share = backend.state.network_share
        app.add_middleware(NetworkAccessMiddleware)
        app.add_middleware(BearerKeyMiddleware)
        app.add_middleware(BrowserGuardMiddleware)
    all_headers = {'Accept': 'application/json, text/event-stream', **(headers or {})}
    url = '/mcp/' + query
    with TestClient(app, base_url='http://127.0.0.1:3900', client=(caller_host, 50000)) as client:
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
        arguments = _profile_tool_arguments(tool_name) if tool_name else ({'name': 'Fixture', 'ref_audio_base64': base64.b64encode(b'RIFF\x00\x00\x00\x00WAVEfmt ').decode()} if post else {})
        result = _decode(client.post(url, headers=all_headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': tool_name or ('clone_voice' if post else 'check_health'), 'arguments': arguments},
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



def _profile_tool_arguments(tool):
    if tool == 'design_voice':
        return {'name': 'Fixture', 'description': 'a female voice'}
    import io
    import wave
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x01\x00' * 4000)
    return {'name': 'Fixture', 'ref_text': 'Known reference',
            'ref_audio_base64': base64.b64encode(audio.getvalue()).decode()}


@pytest.fixture
def real_profile_backend(monkeypatch, tmp_path):
    """Actual consumer gate, profile persistence and SDK; no model/network IO."""
    from api.routers import describe_voice, profiles
    from core import db
    from main import BearerKeyMiddleware, NetworkAccessMiddleware
    from services import ffmpeg_utils, model_manager

    monkeypatch.setattr(profiles, 'VOICES_DIR', str(tmp_path / 'voices'))
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'profiles.db'))
    monkeypatch.setattr(profiles.event_bus, 'emit', lambda *_: None)
    monkeypatch.setattr(ffmpeg_utils, 'require_audio_stream', lambda _path: None)
    monkeypatch.setattr(model_manager, 'get_model_status', lambda: {'loaded': False})
    db.init_db()
    app = FastAPI()
    app.state.network_share = SimpleNamespace(pin=None)
    app.include_router(profiles.router)
    app.include_router(describe_voice.router)
    app.add_middleware(NetworkAccessMiddleware)
    app.add_middleware(BearerKeyMiddleware)
    seen = []

    @app.middleware('http')
    async def observe_identity(request, call_next):
        response = await call_next(request)
        from core.auth import principal_for
        seen.append({'path': request.url.path, 'status': response.status_code,
                     'peer': request.client.host if request.client else None,
                     'principal': principal_for(request).kind.value})
        return response

    return app, seen


def _profile_rows():
    from core.db import db_conn
    with db_conn() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM voice_profiles')]


def _tool_body(result):
    return json.loads(result['content'][0]['text'])


@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'},
                                  {'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1'}])
def test_real_profile_tools_do_not_borrow_loopback_consumer_access(monkeypatch, real_profile_backend, tool, headers):
    result, seen = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                             post=True, tool_name=tool, headers=headers)
    assert 'profile_id' not in _tool_body(result), result
    assert _profile_rows() == []
    assert seen[0]['status'] in (401, 403)


@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
@pytest.mark.parametrize('auth', ['master', 'pin_header', 'pin_query', 'pin_cookie', 'trusted', 'bare_server', 'loopback'])
def test_real_profile_tools_preserve_consumer_creation_policy(monkeypatch, real_profile_backend, tool, auth):
    app, _ = real_profile_backend
    headers, query, caller_host = {}, '', '192.0.2.4'
    if auth == 'master':
        headers = {'Authorization': f'Bearer {KEY}'}
    elif auth.startswith('pin_'):
        monkeypatch.delenv('OMNIVOICE_API_KEY')
        app.state.network_share.pin = '123456'
        if auth == 'pin_header':
            headers = {'X-OmniVoice-Pin': '123456'}
        elif auth == 'pin_query':
            query = '?pin=123456'
        else:
            headers = {'Cookie': 'ov_pin=123456'}
    elif auth == 'trusted':
        monkeypatch.setenv('OMNIVOICE_TRUSTED_NETWORKS', '192.0.2.0/24')
    elif auth == 'bare_server':
        monkeypatch.delenv('OMNIVOICE_API_KEY')
    else:
        caller_host = '127.0.0.1'
    result, seen = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                             post=True, tool_name=tool, headers=headers, query=query, caller_host=caller_host)
    assert 'profile_id' in _tool_body(result), result
    assert len(_profile_rows()) == 1
    assert _profile_rows()[0]['kind'] == ('design' if tool == 'design_voice' else 'clone')
    assert all(item['peer'] == caller_host for item in seen)


@pytest.mark.parametrize('csrf', [False, True])
def test_real_profile_cookie_mutations_keep_csrf_proof(monkeypatch, real_profile_backend, csrf):
    from services.admin_sessions import admin_session_store
    token = admin_session_store.issue(KEY).token
    headers = {'Cookie': f'ov_session={token}'}
    if csrf:
        headers.update({'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'})
    result, _ = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                           post=True, tool_name='clone_voice', headers=headers)
    assert ('profile_id' in _tool_body(result)) is csrf
    assert len(_profile_rows()) == int(csrf)



@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
def test_real_profile_remote_desktop_has_no_implicit_consumer_access(monkeypatch, real_profile_backend, tool):
    # Both production outer gates are inert here. The request really reaches
    # the SDK, then must face the same consumer policy as direct API traffic.
    monkeypatch.delenv('OMNIVOICE_API_KEY')
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '0')
    result, seen = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                             post=True, tool_name=tool, protect_transport=True)
    assert 'profile_id' not in _tool_body(result), result
    assert _profile_rows() == []
    assert seen[0]['status'] == 403


@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
def test_real_profile_pin_consumers_work_in_desktop_mode(monkeypatch, real_profile_backend, tool):
    monkeypatch.delenv('OMNIVOICE_API_KEY')
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '0')
    real_profile_backend[0].state.network_share.pin = '123456'
    result, _ = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                          post=True, tool_name=tool, protect_transport=True,
                          headers={'X-OmniVoice-Pin': '123456'})
    assert 'profile_id' in _tool_body(result), result
    assert len(_profile_rows()) == 1


def test_real_profile_unresolved_sdk_context_cannot_become_local(monkeypatch, real_profile_backend):
    result, _ = _http_tool(monkeypatch, 'inprocess', backend_fixture=real_profile_backend,
                          post=True, tool_name='clone_voice', broken_context=True)
    assert 'profile_id' not in _tool_body(result), result
    assert _profile_rows() == []


@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
def test_real_profile_stdio_retains_local_consumer_access(monkeypatch, real_profile_backend, tool):
    import mcp_server
    from mcp.shared.memory import create_connected_server_and_client_session
    monkeypatch.delenv('OMNIVOICE_API_KEY')
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '0')
    backend, seen = real_profile_backend
    server = mcp_server.create_mcp_server(app=backend)

    async def run():
        async with create_connected_server_and_client_session(server, read_timeout_seconds=timedelta(seconds=5)) as session:
            return await session.call_tool(tool, _profile_tool_arguments(tool))

    result = asyncio.run(run())
    assert not result.isError, result
    assert 'profile_id' in json.loads(result.content[0].text)
    assert len(_profile_rows()) == 1
    assert all(item['peer'] == '127.0.0.1' for item in seen)


@pytest.mark.parametrize('credential', [False, True])
def test_desktop_http_loopback_admin_requires_explicit_proof(monkeypatch, credential):
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '0')
    headers = {'Authorization': f'Bearer {KEY}'} if credential else {}
    result, _ = _http_tool(monkeypatch, 'inprocess', caller_host='127.0.0.1', headers=headers)
    assert bool(result.get('isError')) is not credential


@pytest.mark.parametrize('mode', ['override', 'standalone'])
@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
def test_real_profile_http_loopback_backend_does_not_lend_its_consumer_identity(monkeypatch, real_profile_backend, mode, tool):
    result, _ = _http_tool(monkeypatch, mode, backend_fixture=real_profile_backend,
                          target_host='127.0.0.1', post=True, tool_name=tool)
    assert 'profile_id' not in _tool_body(result), result
    assert _profile_rows() == []


@pytest.mark.parametrize('mode', ['override', 'standalone'])
@pytest.mark.parametrize('credential', ['master', 'pin', 'cookie', 'bare_server'])
@pytest.mark.parametrize('target_host', ['127.0.0.1', '192.0.2.5'])
def test_real_profile_http_backend_preserves_valid_consumers(monkeypatch, real_profile_backend, mode, credential, target_host):
    monkeypatch.setenv('OMNIVOICE_TRUSTED_NETWORKS', '192.0.2.5/32')
    headers = {}
    if credential == 'master':
        headers = {'Authorization': f'Bearer {KEY}'}
    elif credential == 'pin':
        monkeypatch.delenv('OMNIVOICE_API_KEY')
        real_profile_backend[0].state.network_share.pin = '123456'
        headers = {'X-OmniVoice-Pin': '123456'}
    elif credential == 'cookie':
        from services.admin_sessions import admin_session_store
        token = admin_session_store.issue(KEY).token
        monkeypatch.setenv('OMNIVOICE_ALLOWED_ORIGINS', ORIGIN)
        headers = {'Cookie': f'ov_session={token}', 'Origin': ORIGIN, 'X-VoiceStudio-CSRF': '1'}
    else:
        monkeypatch.delenv('OMNIVOICE_API_KEY')
    result, seen = _http_tool(monkeypatch, mode, backend_fixture=real_profile_backend,
                             target_host=target_host, post=True, tool_name='clone_voice', headers=headers)
    assert 'profile_id' in _tool_body(result), result
    assert len(_profile_rows()) == 1
    expected = {'master': 'api_key', 'pin': 'pin', 'cookie': 'admin_session', 'bare_server': 'anonymous'}
    assert seen[0]['principal'] == expected[credential]



@pytest.mark.parametrize('mode', ['override', 'standalone'])
@pytest.mark.parametrize('tool', ['clone_voice', 'design_voice'])
@pytest.mark.parametrize('broken_context', [False, True])
def test_real_profile_wire_backend_cannot_lend_trusted_network_identity(monkeypatch, real_profile_backend, mode, tool, broken_context):
    # Only the bridge's socket peer is trusted, not the original HTTP caller.
    monkeypatch.setenv('OMNIVOICE_TRUSTED_NETWORKS', '192.0.2.5/32')
    result, seen = _http_tool(monkeypatch, mode, backend_fixture=real_profile_backend,
                             target_host='192.0.2.5', caller_host='192.0.2.4',
                             post=True, tool_name=tool, broken_context=broken_context)
    assert 'profile_id' not in _tool_body(result), result
    assert _profile_rows() == []
    assert seen[0]['status'] in (401, 403)


def test_real_profile_direct_unmarked_trusted_network_keeps_consumption(monkeypatch, real_profile_backend):
    monkeypatch.setenv('OMNIVOICE_TRUSTED_NETWORKS', '192.0.2.5/32')
    backend, seen = real_profile_backend

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend, client=('192.0.2.5', 50000)), base_url=ORIGIN) as client:
            return await client.post('/profiles', data={'name': 'Fixture', 'kind': 'design', 'vd_states': '{}'})

    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    assert len(_profile_rows()) == 1
    assert seen[0]['principal'] == 'trusted_network'


@pytest.mark.parametrize('mode', ['override', 'standalone'])
@pytest.mark.parametrize('target_host', ['127.0.0.1', '192.0.2.5'])
@pytest.mark.parametrize('with_pin', [False, True])
def test_real_profile_wire_master_key_does_not_replace_independent_pin(monkeypatch, real_profile_backend, mode, target_host, with_pin):
    monkeypatch.setenv('OMNIVOICE_TRUSTED_NETWORKS', '192.0.2.5/32')
    real_profile_backend[0].state.network_share.pin = '123456'
    headers = {'Authorization': f'Bearer {KEY}'}
    if with_pin:
        headers['X-OmniVoice-Pin'] = '123456'
    result, seen = _http_tool(monkeypatch, mode, backend_fixture=real_profile_backend,
                             target_host=target_host, post=True, tool_name='clone_voice', headers=headers)
    assert ('profile_id' in _tool_body(result)) is with_pin, result
    assert len(_profile_rows()) == int(with_pin)
    assert seen[0]['status'] == (200 if with_pin else 401)
