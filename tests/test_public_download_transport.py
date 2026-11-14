"""Wire-level contracts for the lockfile's HTTPX/httpcore integration."""
import asyncio
import socket
import ssl

import httpcore
import httpx
import pytest

from core.url_safety import UnsafeURLError
from services import public_download_transport as transport_module

PUBLIC_IP = '93.184.216.34'


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, journal):
        self.journal, self.pending, self.closed = journal, bytearray(), False

    async def write(self, buffer, timeout=None):
        self.journal.append(('write', buffer))
        if buffer.startswith(b'\x05'):
            self.pending.extend(b'\x05\x00' if len(buffer) == 3 else b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        elif buffer.startswith(b'CONNECT '):
            self.pending.extend(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        elif buffer.startswith(b'HEAD '):
            self.pending.extend(b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n')
        elif buffer.startswith(b'GET '):
            self.pending.extend(b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndata')

    async def read(self, max_bytes, timeout=None):
        data = bytes(self.pending[:max_bytes])
        del self.pending[:max_bytes]
        return data

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.journal.append(('tls', server_hostname, ssl_context.verify_mode, ssl_context.check_hostname))
        return self

    async def aclose(self):
        self.closed = True

    def get_extra_info(self, info):
        return False if info == 'is_readable' else None


class Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, journal):
        self.journal = journal

    async def connect_tcp(self, host, port, **kwargs):
        self.journal.append(('connect', host, port))
        return Stream(self.journal)

    async def sleep(self, seconds):
        pass


@pytest.fixture(autouse=True)
def clean_proxy_env(monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv('NO_PROXY', '*')
    monkeypatch.delenv('OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS', raising=False)


def _factory(journal):
    def create(**kwargs):
        owner = httpx.AsyncClient(**kwargs)
        for adapter in [owner._transport, *owner._mounts.values()]:
            if adapter is not None:
                adapter._pool._network_backend = Backend(journal)
        return owner
    return create


@pytest.mark.parametrize('proxy', [None, 'http', 'https', 'socks5'])
def test_numeric_wire_target_retains_host_tls_and_proxy_identity(monkeypatch, proxy):
    if proxy:
        monkeypatch.setenv('NO_PROXY', '')
        for name in ('HTTP_PROXY', 'HTTPS_PROXY'):
            monkeypatch.setenv(name, f'{proxy}://proxy.test:8080')
    journal = []
    resolved = []
    async def addresses(url, timeout):
        resolved.append(url.raw_host)
        return [PUBLIC_IP]
    monkeypatch.setattr(transport_module, '_addresses', addresses)

    async def run():
        pinned = transport_module.PublicDownloadTransport(client_factory=_factory(journal))
        async with httpx.AsyncClient(transport=pinned) as client:
            response = await client.get('https://münich.example/model?revision=pinned')
            assert response.content == b'data'
            assert response.url.host == 'münich.example'
    asyncio.run(run())

    assert resolved == [b'xn--mnich-kva.example']
    assert ('connect', 'proxy.test' if proxy else PUBLIC_IP, 8080 if proxy else 443) in journal
    names = [event[1] for event in journal if event[0] == 'tls']
    assert names == (['proxy.test', 'xn--mnich-kva.example'] if proxy == 'https' else ['xn--mnich-kva.example'])
    assert all(event[2:] == (ssl.CERT_REQUIRED, True) for event in journal if event[0] == 'tls')
    wires = b''.join(event[1] for event in journal if event[0] == 'write')
    assert b'Host: xn--mnich-kva.example\r\n' in wires
    if proxy in ('http', 'https'):
        assert f'CONNECT {PUBLIC_IP}:443 HTTP/1.1'.encode() in wires
    if proxy == 'socks5':
        assert socket.inet_aton(PUBLIC_IP) in wires


def test_no_proxy_matches_original_hostname_and_pools_never_cross_logical_origins(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.test:8080')
    monkeypatch.setenv('NO_PROXY', 'a.test,b.test')
    journal = []
    lookups = []
    async def addresses(url, timeout):
        lookups.append(url.host)
        return [PUBLIC_IP]
    monkeypatch.setattr(transport_module, '_addresses', addresses)
    async def run():
        pinned = transport_module.PublicDownloadTransport(client_factory=_factory(journal))
        async with httpx.AsyncClient(transport=pinned) as client:
            for host in ('a.test', 'a.test', 'b.test'):
                assert (await client.get(f'https://{host}/model')).content == b'data'
            assert len(pinned._origins) == 2
    asyncio.run(run())
    assert lookups == ['a.test', 'a.test', 'b.test']
    assert [event[1] for event in journal if event[0] == 'tls'] == ['a.test', 'b.test']
    assert [event for event in journal if event[0] == 'connect'] == [('connect', PUBLIC_IP, 443)] * 2


@pytest.mark.parametrize('connection_error', [httpcore.ConnectTimeout, httpcore.ProxyError])
def test_a_healthy_address_is_reused_only_while_fresh_dns_still_validates_it(monkeypatch, connection_error):
    dead = '2606:4700:4700::1111'
    changed = '1.1.1.1'
    journal = []
    answers = [[dead, PUBLIC_IP], [dead, PUBLIC_IP], [changed]]
    async def addresses(url, timeout):
        return answers.pop(0)
    monkeypatch.setattr(transport_module, '_addresses', addresses)

    class FailFirstAddress(Backend):
        async def connect_tcp(self, host, port, **kwargs):
            if host == dead:
                journal.append(('connect', host, port))
                raise connection_error('controlled unavailable address')
            return await super().connect_tcp(host, port, **kwargs)

    def factory(**kwargs):
        owner = httpx.AsyncClient(**kwargs)
        owner._transport._pool._network_backend = FailFirstAddress(journal)
        return owner

    async def run():
        pinned = transport_module.PublicDownloadTransport(client_factory=factory)
        async with httpx.AsyncClient(transport=pinned) as client:
            for _ in range(3):
                assert (await client.get('https://origin.test/model')).content == b'data'
    asyncio.run(run())
    assert not answers  # Every request still performs destination validation.
    assert [event[1] for event in journal if event[0] == 'connect'] == [dead, PUBLIC_IP, changed]


def test_plain_http_proxy_receives_a_numeric_absolute_target_and_original_host(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.test:8080')
    monkeypatch.setenv('NO_PROXY', '')
    journal = []
    async def addresses(url, timeout):
        return [PUBLIC_IP]
    monkeypatch.setattr(transport_module, '_addresses', addresses)
    async def run():
        pinned = transport_module.PublicDownloadTransport(client_factory=_factory(journal))
        async with httpx.AsyncClient(transport=pinned) as client:
            assert (await client.get('http://origin.test/model')).content == b'data'
    asyncio.run(run())
    wires = b''.join(event[1] for event in journal if event[0] == 'write')
    assert f'GET http://{PUBLIC_IP}/model HTTP/1.1'.encode() in wires
    assert b'Host: origin.test\r\n' in wires
    assert not any(event[0] == 'tls' for event in journal)


def test_private_answers_and_unpinned_backend_dials_fail_closed(monkeypatch):
    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    async def run():
        with pytest.raises(UnsafeURLError):
            await transport_module._addresses(httpx.URL('https://rebind.test/model'), 2)
        journal = []
        backend = transport_module._TLSIdentityBackend(Backend(journal), 'rebind.test')
        for host in ('rebind.test', '127.0.0.1', '::1'):
            with pytest.raises(UnsafeURLError):
                await backend.connect_tcp(host, 443)
        assert journal == []
    asyncio.run(run())


def test_private_mirror_requires_and_honours_explicit_opt_in(monkeypatch):
    async def run():
        url = httpx.URL('http://127.0.0.1:3900/model')
        with pytest.raises(UnsafeURLError):
            await transport_module._addresses(url, 2)
        monkeypatch.setenv('OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS', '1')
        assert await transport_module._addresses(url, 2) == ['127.0.0.1']
    asyncio.run(run())
