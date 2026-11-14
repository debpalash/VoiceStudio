"""Actual httpx/socket regression: a checked hostname changes to loopback.

All wire traffic is confined to the local fixture. The allowed public origin
is mapped to that fixture at socket.connect; the second public address is
deliberately unreachable, so a safe pinned connection cannot reach the private
response or cause external traffic.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
import socket
import ssl
import threading

import httpx
import pytest

ORIGIN_IP = '1.1.1.1'
TARGET_IP = '93.184.216.34'


def _tls_server_context():
    # main injects truststore for outbound clients. Its retained stdlib class
    # must serve the fixture's inbound TLS; never disable client verification.
    from truststore._ssl_constants import _original_SSLContext
    return _original_SSLContext(ssl.PROTOCOL_TLS_SERVER)


def test_tls_fixture_retains_stdlib_server_context_after_client_truststore_injection(monkeypatch):
    import truststore
    monkeypatch.setattr(ssl, 'SSLContext', truststore.SSLContext)
    context = _tls_server_context()
    # Real server-side wrapping without a bind/handshake: the client-only
    # truststore wrapper wrongly asks this unconnected socket for peer certs.
    with socket.socket() as listener:
        with context.wrap_socket(listener, server_side=True, do_handshake_on_connect=False) as wrapped:
            assert wrapped.server_side is True
    assert ssl.SSLContext is truststore.SSLContext  # Client TLS policy stays active.


@pytest.mark.parametrize('phase', ['redirect_head', 'body', 'range', 'get_redirect_body', 'get_redirect_range'])
def test_dns_rebinding_never_dials_or_publishes_a_private_response(monkeypatch, tmp_path, phase):
    from core.url_safety import UnsafeURLError
    from services.segmented_download import segmented_download

    state = {'lookups': 0, 'head_seen': False, 'private_body_requests': 0, 'private_dials': 0}
    content = b'private metadata must not become model bytes'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            if self.path == '/entry' and not phase.startswith('get_redirect_'):
                self.send_response(302)
                self.send_header('Location', f'http://rebind.test:{self.server.server_port}/model')
                self.send_header('Content-Length', '0')
            else:
                state['head_seen'] = True
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                if phase in {'range', 'get_redirect_range'}:
                    self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()

        def do_GET(self):
            if phase.startswith('get_redirect_') and self.path == '/entry':
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/private')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            state['private_body_requests'] += 1
            self.send_response(206 if phase == 'range' else 200)
            if phase == 'range':
                self.send_header('Content-Range', f'bytes 0-{len(content)-1}/{len(content)}')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    class Server(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name = '127.0.0.1'
            self.server_port = self.server_address[1]

    server = Server(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    real_resolve = socket.getaddrinfo
    real_connect = socket.socket.connect

    def resolve(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name == 'origin.test':
            host = ORIGIN_IP
        elif name == 'rebind.test':
            state['lookups'] += 1
            private = state['lookups'] > 1 if phase == 'redirect_head' else state['head_seen']
            host = '127.0.0.1' if private else TARGET_IP
        return real_resolve(host, port, *args, **kwargs)

    def connect(sock, address):
        host, port = address[:2]
        if port != server.server_port and host == '127.0.0.1':
            return real_connect(sock, address)  # Windows asyncio's local wake-up socket pair.
        if host == ORIGIN_IP or (host == TARGET_IP and phase != 'redirect_head'):
            return real_connect(sock, ('127.0.0.1', port))
        if host == TARGET_IP:
            raise ConnectionRefusedError('controlled public endpoint is offline')
        if host == '127.0.0.1':
            state['private_dials'] += 1
            return real_connect(sock, address)
        raise AssertionError(f'unexpected network destination: {host}')

    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setenv('NO_PROXY', '*')
    monkeypatch.delenv('OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS', raising=False)
    destination = tmp_path / 'model.bin'
    try:
        expected = UnsafeURLError if phase.startswith('get_redirect_') else (UnsafeURLError, httpx.ConnectError)
        with pytest.raises(expected):
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(segmented_download(f'http://origin.test:{server.server_port}/entry', str(destination), timeout=2))
        assert not destination.exists()
        assert state['private_dials'] == 0, state
        assert state['private_body_requests'] == 0, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize(('certificate_host', 'phase'), [
    ('xn--mnich-kva.example', 'direct'), ('other.test', 'direct'),
    ('xn--mnich-kva.example', 'body_redirect'), ('xn--mnich-kva.example', 'range_redirect'),
])
@pytest.mark.parametrize('client_context', ['ambient', 'truststore'])
def test_pinned_public_https_retains_sni_and_verifies_the_original_hostname(monkeypatch, tmp_path, certificate_host, phase, client_context):
    import hashlib
    if client_context == 'truststore':
        import truststore
        monkeypatch.setattr(ssl, 'SSLContext', truststore.SSLContext)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from services.segmented_download import segmented_download

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Download fixture CA')])
    now = datetime.now(timezone.utc)
    ca_certificate = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                    key_encipherment=False, data_encipherment=False,
                                    key_agreement=False, key_cert_sign=True, crl_sign=True,
                                    encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_host)])
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                    key_encipherment=False, data_encipherment=False,
                                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                                    encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path, ca_path = tmp_path / 'cert.pem', tmp_path / 'key.pem', tmp_path / 'ca.pem'
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM)
    ca_path.write_bytes(ca_pem)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM) + ca_pem)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    seen_hosts, seen_sni, dials = [], [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_HEAD(self):
            seen_hosts.append(self.headers['Host'])
            self.send_response(200)
            self.send_header('Content-Length', '4')
            if phase == 'range_redirect':
                self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
        def do_GET(self):
            seen_hosts.append(self.headers['Host'])
            if phase != 'direct' and self.path == '/model':
                self.send_response(302)
                self.send_header('Location', '/final')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(206 if phase == 'range_redirect' else 200)
            if phase == 'range_redirect':
                assert self.headers['Range'] == 'bytes=0-3'
                self.send_header('Content-Range', 'bytes 0-3/4')
            self.send_header('Content-Length', '4')
            self.end_headers()
            self.wfile.write(b'data')
    class Server(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name = '127.0.0.1'
            self.server_port = self.server_address[1]
    server = Server(('127.0.0.1', 0), Handler)
    context = _tls_server_context()
    context.load_cert_chain(cert_path, key_path)
    context.set_servername_callback(lambda _socket, hostname, _context: seen_sni.append(hostname))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    real_resolve, real_connect = socket.getaddrinfo, socket.socket.connect
    def resolve(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name == 'xn--mnich-kva.example':
            host = TARGET_IP
        return real_resolve(host, port, *args, **kwargs)
    def connect(sock, address):
        host, port = address[:2]
        if port != server.server_port and host == '127.0.0.1':
            return real_connect(sock, address)  # Windows asyncio's local wake-up socket pair.
        dials.append(host)
        assert host == TARGET_IP, 'a private or unpinned address reached socket.connect'
        return real_connect(sock, ('127.0.0.1', port))
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setenv('NO_PROXY', '*')
    monkeypatch.setenv('SSL_CERT_FILE', str(ca_path))
    monkeypatch.delenv('OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS', raising=False)
    destination = tmp_path / 'model.bin'
    try:
        operation = segmented_download(f'https://münich.example:{server.server_port}/model', str(destination),
                                       timeout=2, expected_etag=hashlib.sha256(b'data').hexdigest())
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            if certificate_host == 'other.test':
                with pytest.raises(httpx.ConnectError):
                    runner.run(operation)
                assert not destination.exists()
                assert seen_hosts == []
            else:
                runner.run(operation)
                assert destination.read_bytes() == b'data'
                assert seen_hosts == [f'xn--mnich-kva.example:{server.server_port}'] * (2 if phase == 'direct' else 3)
        assert dials and set(dials) == {TARGET_IP}
        assert seen_sni and set(seen_sni) == {'xn--mnich-kva.example'}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
