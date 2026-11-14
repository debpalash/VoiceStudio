"""Segmented HF downloads must not follow a redirect into a private address.

The downloader validates the *hostname* it starts from and decides whether to
keep the Authorization header per hop, but a hop was previously requested
without any destination policy: a redirect to ``127.0.0.1``/a LAN address would
be fetched and its body written into the model staging tree.
"""
import httpx
import pytest

pytestmark = pytest.mark.asyncio


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested: list[str] = []

    async def head(self, url, headers=None, follow_redirects=False):  # noqa: ARG002 — signature parity
        self.requested.append(url)
        return self._responses.pop(0)


def _response(status: int, location: str | None = None) -> httpx.Response:
    headers = {"location": location} if location else {}
    return httpx.Response(
        status, headers=headers, request=httpx.Request("HEAD", "https://example.test/x")
    )


@pytest.fixture(autouse=True)
def _hermetic_destination_policy(monkeypatch):
    """Stand in for check_public_url so the test never resolves real DNS."""
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    def policy(url: str) -> str:
        host = httpx.URL(url).host or ""
        if host in {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5"}:
            raise UnsafeURLError("refusing to connect to a private network address")
        return url

    monkeypatch.setattr(sd, "check_public_url", policy)


async def test_resolve_refuses_a_redirect_into_a_private_address():
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    client = _FakeClient([_response(302, "http://127.0.0.1:9/secret")])
    with pytest.raises(UnsafeURLError):
        await sd._resolve(client, "https://cdn.example/model.bin", None)
    # The redirect target is never requested.
    assert client.requested == ["https://cdn.example/model.bin"]


async def test_resolve_refuses_a_cloud_metadata_redirect():
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    client = _FakeClient([_response(307, "http://169.254.169.254/latest/meta-data/")])
    with pytest.raises(UnsafeURLError):
        await sd._resolve(client, "https://cdn.example/model.bin", None)
    assert client.requested == ["https://cdn.example/model.bin"]


async def test_resolve_still_follows_a_public_redirect():
    from services import segmented_download as sd
    client = _FakeClient([
        _response(302, "https://cdn2.example/model.bin"),
        _response(200),
    ])
    final, size, accepts = await sd._resolve(client, "https://cdn.example/model.bin", None)
    assert final == "https://cdn2.example/model.bin"
    assert size is None and accepts is False
    assert client.requested == [
        "https://cdn.example/model.bin",
        "https://cdn2.example/model.bin",
    ]


async def test_injected_clients_cannot_follow_private_redirects_implicitly(tmp_path):
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    seen = []
    def response(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1:9/private'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response), follow_redirects=True) as client:
        with pytest.raises(UnsafeURLError):
            await sd.segmented_download('https://huggingface.co/model', str(tmp_path / 'model'), client=client)
    assert seen == ['https://huggingface.co/model']
    assert not (tmp_path / 'model').exists()


async def test_hugging_face_redirect_keeps_public_cdn_download_and_drops_token(tmp_path):
    from services import segmented_download as sd
    seen = []
    def response(request):
        seen.append(request)
        if request.url.host == 'huggingface.co':
            return httpx.Response(302, headers={'Location': 'https://cdn.example/model'})
        return httpx.Response(200, headers={'Content-Length': '4'}, content=b'' if request.method == 'HEAD' else b'data')
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await sd.segmented_download('https://huggingface.co/model', str(tmp_path / 'model'),
                                    client=client, token='hf_test_token')
    assert seen[0].headers['Authorization'] == 'Bearer hf_test_token'
    assert all('Authorization' not in request.headers for request in seen[1:])
    assert (tmp_path / 'model').read_bytes() == b'data'


async def test_shared_unprotected_network_client_is_rejected_before_any_request(tmp_path):
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(UnsafeURLError, match='address-pinned'):
            await sd.segmented_download('https://model.example/file', str(tmp_path / 'model'), client=client)
    assert not (tmp_path / 'model').exists()


@pytest.mark.parametrize('ranged', [False, True], ids=['single-stream', 'range'])
async def test_get_only_private_redirect_is_a_terminal_destination_refusal(tmp_path, ranged):
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    seen = []
    def response(request):
        seen.append(request)
        if request.method == 'HEAD':
            return httpx.Response(200, headers={'Content-Length': '4', 'Accept-Ranges': 'bytes' if ranged else 'none'})
        return httpx.Response(307, headers={'Location': 'http://169.254.169.254/latest/meta-data/'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response), follow_redirects=True) as client:
        with pytest.raises(UnsafeURLError):
            await sd.segmented_download('https://huggingface.co/model', str(tmp_path / 'model'), client=client)
    assert [str(r.url) for r in seen] == ['https://huggingface.co/model'] * 2
    assert not (tmp_path / 'model').exists()


@pytest.mark.parametrize('ranged', [False, True], ids=['single-stream', 'range'])
@pytest.mark.parametrize('valid_digest', [False, True], ids=['bad-digest', 'verified'])
async def test_get_only_public_redirects_preserve_ranges_auth_and_integrity(tmp_path, ranged, valid_digest):
    import hashlib
    from services import segmented_download as sd
    seen = []
    def response(request):
        seen.append(request)
        if request.method == 'HEAD':
            return httpx.Response(200, headers={'Content-Length': '4', 'Accept-Ranges': 'bytes' if ranged else 'none'})
        if request.url.path == '/model':
            return httpx.Response(302, headers={'Location': '/next'})
        if request.url.host == 'huggingface.co':
            return httpx.Response(308, headers={'Location': 'https://cdn.example/final'})
        return httpx.Response(206 if ranged else 200, headers={'Content-Range': 'bytes 0-3/4'}, content=b'data')
    destination = tmp_path / 'model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(response), follow_redirects=True) as client:
        operation = sd.segmented_download('https://huggingface.co/model', str(destination), client=client,
                                          token='hf_test_token', expected_size=4,
                                          expected_etag=hashlib.sha256(b'data' if valid_digest else b'evil').hexdigest())
        if valid_digest:
            await operation
            assert destination.read_bytes() == b'data'
        else:
            with pytest.raises(sd.DownloadIntegrityError):
                await operation
            assert not destination.exists()
    assert [str(r.url) for r in seen] == [
        'https://huggingface.co/model', 'https://huggingface.co/model',
        'https://huggingface.co/next', 'https://cdn.example/final',
    ]
    assert all(r.headers['Authorization'] == 'Bearer hf_test_token' for r in seen[:-1])
    assert 'Authorization' not in seen[-1].headers
    assert all(r.headers.get('Range') == ('bytes=0-3' if ranged else None) for r in seen[1:])


@pytest.mark.parametrize('ranged', [False, True], ids=['single-stream', 'range'])
async def test_cancelled_get_redirect_does_not_start_another_request(tmp_path, ranged):
    from services import segmented_download as sd
    seen, cancelled = [], False
    def response(request):
        nonlocal cancelled
        seen.append(request)
        if request.method == 'HEAD':
            return httpx.Response(200, headers={'Content-Length': '4', 'Accept-Ranges': 'bytes' if ranged else 'none'})
        cancelled = True
        return httpx.Response(302, headers={'Location': 'https://cdn.example/final'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(sd.DownloadCancelled):
            await sd.segmented_download('https://huggingface.co/model', str(tmp_path / 'model'),
                                        client=client, cancel_check=lambda: cancelled)
    assert len(seen) == 2
    assert not (tmp_path / 'model').exists()


@pytest.mark.parametrize(('first', 'second'), [
    ('ordinary', 'unsafe'), ('unsafe', 'ordinary'),
    ('ordinary', 'cancel'), ('cancel', 'ordinary'),
    ('cancel', 'unsafe'), ('unsafe', 'cancel'),
])
async def test_concurrent_terminal_errors_take_precedence_after_siblings_are_drained(first, second):
    import asyncio
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    failures = {
        'ordinary': ValueError('bad range'),
        'unsafe': UnsafeURLError('private destination refused'),
        'cancel': sd.DownloadCancelled(),
    }
    state = {'started': False, 'drained': False}
    async def fail(kind):
        raise failures[kind]
    async def sibling():
        state['started'] = True
        try:
            await asyncio.Event().wait()
        finally:
            state['drained'] = True
    expected = failures['unsafe'] if 'unsafe' in (first, second) else failures['cancel']
    with pytest.raises(type(expected)) as raised:
        await sd._gather_or_cancel([fail(first), fail(second), sibling()])
    assert raised.value is expected
    assert state == {'started': True, 'drained': True}


@pytest.mark.parametrize('redirect_phase',['head','get'])
@pytest.mark.parametrize('ranged',[False,True],ids=['single','range'])
@pytest.mark.parametrize('credential',['header','auth','auth_and_header','cookie_header','cookie_jar'])
async def test_injected_credentials_are_scoped_to_original_download_origin(tmp_path,redirect_phase,ranged,credential):
    import base64
    from services import segmented_download as sd
    seen=[]
    async def response(request):
        seen.append(request)
        if request.url.host=='origin.example' and request.url.path=='/model' and request.method==redirect_phase.upper():
            return httpx.Response(302,headers={'Location':'https://cdn.example/final'})
        return httpx.Response(206 if ranged and request.method=='GET' else 200,
                              headers={'Content-Length':'4','Accept-Ranges':'bytes' if ranged else 'none','Content-Range':'bytes 0-3/4'},
                              content=b'' if request.method=='HEAD' else b'data')
    kwargs={'headers':{'X-Download-Test':'preserved'}}
    expected=None
    if credential in {'header','auth_and_header'}:
        kwargs['headers']['Authorization']='Bearer injected-private-header'
        expected='Bearer injected-private-header'
    if credential in {'auth','auth_and_header'}:
        kwargs['auth']=httpx.BasicAuth('download-user','download-password')
        expected='Basic '+base64.b64encode(b'download-user:download-password').decode()
    if credential=='cookie_header':kwargs['headers']['Cookie']='session=private-cookie'
    if credential=='cookie_jar':kwargs['cookies']={'session':'private-cookie'}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response),**kwargs) as client:
        await sd.segmented_download('https://origin.example/model',str(tmp_path/'model'),client=client)
        # The shared client remains usable with its original credential configuration.
        await client.get('https://origin.example/control')
    origin=[r for r in seen if r.url.host=='origin.example']
    redirected=[r for r in seen if r.url.host=='cdn.example']
    assert origin and redirected
    for request in origin:
        if expected:assert request.headers.get('Authorization')==expected
        else:assert 'private-cookie' in request.headers.get('Cookie','')
    for request in redirected:
        assert 'Authorization' not in request.headers, 'Client authentication leaked across a redirect'
        assert 'Cookie' not in request.headers, 'Client cookies leaked across a redirect'
        assert request.headers['X-Download-Test']=='preserved'
    assert (tmp_path/'model').read_bytes()==b'data'


@pytest.mark.parametrize('ranged',[False,True],ids=['single','range'])
async def test_explicit_hf_token_is_not_replaced_by_injected_default_auth(tmp_path,ranged):
    from services import segmented_download as sd
    seen=[]
    def response(request):
        seen.append(request)
        return httpx.Response(206 if ranged and request.method=='GET' else 200,
                              headers={'Content-Length':'4','Accept-Ranges':'bytes' if ranged else 'none','Content-Range':'bytes 0-3/4'},
                              content=b'' if request.method=='HEAD' else b'data')
    async with httpx.AsyncClient(transport=httpx.MockTransport(response),auth=('injected-user','injected-password')) as client:
        await sd.segmented_download('https://huggingface.co/model',str(tmp_path/'model'),
                                    token='hf_explicit_test_token',client=client)
    assert all(r.headers.get('Authorization')=='Bearer hf_explicit_test_token' for r in seen)


@pytest.mark.parametrize(('origin','target','credentials_allowed'),[
    ('https://origin.example/model','https://origin.example/next',True),
    ('http://origin.example/model','https://origin.example/next',True),
    ('https://origin.example/model','http://origin.example/next',False),
    ('https://origin.example/model','https://origin.example:8443/next',False),
])
async def test_injected_credential_origin_rules_preserve_relative_hops_and_secure_upgrade(tmp_path,origin,target,credentials_allowed):
    from services import segmented_download as sd
    seen=[]
    def response(request):
        seen.append(request)
        if request.method=='HEAD':return httpx.Response(200,headers={'Content-Length':'4'})
        if request.url.path=='/model':return httpx.Response(302,headers={'Location':target})
        return httpx.Response(200,content=b'data')
    async with httpx.AsyncClient(transport=httpx.MockTransport(response),auth=('user','password'),headers={'Cookie':'session=private'}) as client:
        await sd.segmented_download(origin,str(tmp_path/'model'),client=client)
    assert ('Authorization' in seen[-1].headers) is credentials_allowed
    assert ('Cookie' in seen[-1].headers) is credentials_allowed
    assert (tmp_path/'model').read_bytes()==b'data'
