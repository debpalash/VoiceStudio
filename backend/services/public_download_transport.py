"""Destination-pinned HTTP transport for user-started model downloads.

The request keeps its logical Host and TLS identity but uses a checked numeric
address for the wire, including HTTP CONNECT and SOCKS targets. Each logical
origin owns its pools: two CDN names sharing an IP must not share a TLS session.
No process-wide DNS/socket policy is changed.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore
import httpx

from core.url_safety import (
    PRIVATE_DESTINATION_DETAIL,
    UnsafeURLError,
    is_public_address,
    private_url_imports_allowed,
)


async def _addresses(url: httpx.URL, timeout: float | None) -> list[str]:
    if url.scheme not in {'http', 'https'} or not url.host:
        raise UnsafeURLError('Model download URL must use HTTP or HTTPS')
    host = url.raw_host.decode('ascii')
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            answers = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host, url.port or (443 if url.scheme == 'https' else 80), type=socket.SOCK_STREAM,
                ), timeout=timeout,
            )
        except TimeoutError as error:
            raise httpx.ConnectTimeout('Model download DNS lookup timed out') from error
        except OSError as error:
            raise httpx.ConnectError('Model download hostname could not be resolved') from error
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise httpx.ConnectError('Model download hostname has no addresses')
    if not private_url_imports_allowed() and any(not is_public_address(ip) for ip in addresses):
        raise UnsafeURLError(PRIVATE_DESTINATION_DETAIL)
    return addresses


class _TLSIdentityStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream, hostname: str, proxy_hostname: str | None = None):
        self._stream, self._hostname, self._proxy_hostname = stream, hostname, proxy_hostname

    async def read(self, max_bytes, timeout=None):
        return await self._stream.read(max_bytes, timeout=timeout)

    async def write(self, buffer, timeout=None):
        await self._stream.write(buffer, timeout=timeout)

    async def aclose(self):
        await self._stream.aclose()

    def get_extra_info(self, info):
        return self._stream.get_extra_info(info)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # httpcore 1.0.9's CONNECT tunnel ignores the sni_hostname extension.
        # Restore destination certificate/SNI identity here too. An HTTPS
        # proxy's first TLS layer must retain the proxy's own identity.
        hostname = self._proxy_hostname or self._hostname
        stream = await self._stream.start_tls(
            ssl_context=ssl_context, server_hostname=hostname, timeout=timeout,
        )
        return _TLSIdentityStream(stream, self._hostname)


class _TLSIdentityBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend, hostname: str, proxy_url=None):
        self._backend, self._hostname, self._proxy_url = backend, hostname, proxy_url

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        proxy_host = self._proxy_url.host.decode('ascii') if self._proxy_url else None
        if host != proxy_host:
            # A library/API change must fail closed instead of silently
            # handing a hostname back to a second DNS resolution.
            try:
                ipaddress.ip_address(host)
            except ValueError as error:
                raise UnsafeURLError('Model download connection was not address-pinned') from error
            if not private_url_imports_allowed() and not is_public_address(host):
                raise UnsafeURLError(PRIVATE_DESTINATION_DETAIL)
        stream = await self._backend.connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
        )
        return _TLSIdentityStream(
            stream, self._hostname,
            proxy_hostname=(proxy_host if self._proxy_url and self._proxy_url.scheme == b'https' else None),
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeURLError('Model downloads cannot use Unix sockets')

    async def sleep(self, seconds):
        await self._backend.sleep(seconds)


class PublicDownloadTransport(httpx.AsyncBaseTransport):
    """Pin every request, including requests that might reuse an existing pool.

    HTTPX's original-URL proxy selector preserves system proxies and NO_PROXY.
    The two private adapters below are confined to this module and covered by
    wire/TLS tests against the lockfile's HTTPX/httpcore versions. Missing
    adapters are fatal, never a reason to send an unpinned request.
    """
    def __init__(self, *, client_factory=None):
        self._client_factory = client_factory or httpx.AsyncClient
        self._origins = {}
        self._preferred = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.url.scheme, request.url.raw_host, request.url.port)
        if key not in self._origins:
            self._origins[key] = self._client_factory(follow_redirects=False)
        owner = self._origins[key]
        # Select using the original hostname, before rewriting it to an IP.
        transport = owner._transport_for_url(request.url)
        pool = getattr(transport, '_pool', None)
        if not isinstance(pool, httpcore.AsyncConnectionPool):
            raise UnsafeURLError('Model download transport cannot enforce address pinning')
        if not isinstance(pool._network_backend, _TLSIdentityBackend):
            pool._network_backend = _TLSIdentityBackend(
                pool._network_backend, request.url.raw_host.decode('ascii'),
                getattr(pool, '_proxy_url', None),
            )
        addresses = await _addresses(request.url, request.extensions.get('timeout', {}).get('connect'))
        # Avoid paying a dead first-family timeout on every range and let the
        # healthy connection pool be reused. Fresh DNS/policy validation still
        # runs first; an old address never survives a changed answer set.
        preferred = self._preferred.get(key)
        if preferred in addresses:
            addresses = [preferred, *(address for address in addresses if address != preferred)]
        for index, address in enumerate(addresses):
            pinned = httpx.Request(
                request.method, request.url.copy_with(host=address),
                headers=request.headers, stream=request.stream,
                extensions={**request.extensions, 'sni_hostname': request.url.raw_host.decode('ascii')},
            )
            try:
                response = await transport.handle_async_request(pinned)
                self._preferred[key] = address
                return response
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError):
                if index == len(addresses) - 1:
                    raise
        raise AssertionError('no pinned address attempted')

    async def aclose(self):
        owners, self._origins = self._origins, {}
        self._preferred.clear()
        await asyncio.gather(*(owner.aclose() for owner in owners.values()))
