"""Segmented HF downloads must not follow a redirect into a private address.

The downloader validates the *hostname* it starts from and decides whether to
keep the Authorization header per hop, but a hop was previously requested
without any destination policy: a redirect to ``127.0.0.1``/a LAN address would
be fetched and its body written into the model staging tree.
"""
import httpx
import pytest

from core.url_safety import UnsafeURLError
from services import segmented_download as sd

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
    def policy(url: str) -> str:
        host = httpx.URL(url).host or ""
        if host in {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5"}:
            raise UnsafeURLError("refusing to connect to a private network address")
        return url

    monkeypatch.setattr(sd, "check_public_url", policy)


async def test_resolve_refuses_a_redirect_into_a_private_address():
    client = _FakeClient([_response(302, "http://127.0.0.1:9/secret")])
    with pytest.raises(UnsafeURLError):
        await sd._resolve(client, "https://cdn.example/model.bin", None)
    # The redirect target is never requested.
    assert client.requested == ["https://cdn.example/model.bin"]


async def test_resolve_refuses_a_cloud_metadata_redirect():
    client = _FakeClient([_response(307, "http://169.254.169.254/latest/meta-data/")])
    with pytest.raises(UnsafeURLError):
        await sd._resolve(client, "https://cdn.example/model.bin", None)
    assert client.requested == ["https://cdn.example/model.bin"]


async def test_resolve_still_follows_a_public_redirect():
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
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(UnsafeURLError, match='address-pinned'):
            await sd.segmented_download('https://model.example/file', str(tmp_path / 'model'), client=client)
    assert not (tmp_path / 'model').exists()
