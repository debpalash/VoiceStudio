"""Streaming HTTP transport with origin-bound credentials and atomic output."""
from contextlib import contextmanager
import http.client
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import ssl
import tempfile
from urllib import request, error
from urllib.parse import urlencode, urlsplit

from .config import loopback, normalize_url
from .errors import CLIError, scrub
from .terminal import is_terminal

CHUNK = 64 * 1024
MAX_JSON = 16 * 1024 * 1024


def response_error(status, headers, raw, key=''):
    """One error contract for live HTTP and local application responses."""
    raw = raw.decode('utf-8', errors='replace')
    detail = None
    try:
        detail = json.loads(raw)
        detail = detail.get('detail', detail.get('error', detail)) if isinstance(detail, dict) else detail
        if isinstance(detail, dict):
            detail = detail.get('message', detail.get('detail', detail))
        message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    except ValueError:
        message = raw or f'Request failed with status {status}'
    codes = {401: 'authentication_error', 403: 'permission_denied', 404: 'not_found',
             409: 'conflict', 422: 'validation_error', 429: 'rate_limited'}
    return CLIError(codes.get(status, 'backend_error'), f'HTTP {status}: {scrub(str(message), [key])}',
                    status=status, retry_after=headers.get('Retry-After'), details=detail)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CLIError('redirect_refused', 'Backend redirected the request; configure its final URL explicitly')


class Client:
    def __init__(self, url: str, key: str = '', timeout: float = 600):
        self.url, self.key, self.timeout = normalize_url(url), key, timeout
        self.identified = False
        if key and urlsplit(url).scheme != 'https' and not loopback(urlsplit(url).hostname):
            raise CLIError('credential_transport', 'Remote credentials require HTTPS')
        handlers = [NoRedirect()]
        if loopback(urlsplit(url).hostname):
            handlers.append(request.ProxyHandler({}))
        self.opener = request.build_opener(*handlers)

    @contextmanager
    def open(self, method: str, path: str, *, payload=None, form=None, files=None, query=None, raw_file=None):
        if not path.startswith('/') or path.startswith('//') or '..' in path.split('/'):
            raise CLIError('invalid_response', 'Refused unsafe backend resource path')
        if path != '/health' and not self.identified:
            self.health()
        target = self.url + path + (('?' + urlencode(query, doseq=True)) if query else '')
        headers = {'Accept': '*/*', 'User-Agent': 'VoiceStudio-CLI'}
        if self.key:
            headers['Authorization'] = f'Bearer {self.key}'
        body = None
        stream = None
        if raw_file is not None:
            stream = Path(raw_file).open('rb')
            body = stream
            headers.update({'Content-Type': 'application/octet-stream',
                            'Content-Length': str(os.fstat(stream.fileno()).st_size)})
        elif files or form is not None:
            stream, content_type, length = multipart(form or {}, files or {})
            body = stream
            headers.update({'Content-Type': content_type, 'Content-Length': str(length)})
        elif payload is not None:
            body = json.dumps(payload, allow_nan=False).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self.opener.open(request.Request(target, data=body, method=method, headers=headers),
                                  timeout=self.timeout) as response:
                yield response
        except error.HTTPError as exc:
            raise response_error(exc.code, exc.headers, exc.read(64 * 1024), self.key) from None
        except (error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
            raise CLIError('connection_error',
                           f'Cannot connect to {self.url}. Open VoiceStudio or configure --url. '
                           f'{scrub(str(exc), [self.key])}') from None
        except (http.client.HTTPException, ConnectionError) as exc:
            raise CLIError('incomplete_response', f'Backend connection interrupted: {exc}') from None
        finally:
            if stream is not None:
                stream.close()

    def json(self, method: str, path: str, **kwargs):
        with self.open(method, path, **kwargs) as response:
            if response.status == 204:
                return {'ok': True}
            if 'json' not in response.headers.get('Content-Type', ''):
                raise CLIError('invalid_response', 'Expected JSON from the backend')
            data = response.read(MAX_JSON + 1)
            if len(data) > MAX_JSON:
                raise CLIError('invalid_response', 'Backend JSON response exceeded 16 MiB')
            try:
                return json.loads(data)
            except (ValueError, UnicodeError):
                raise CLIError('invalid_response', 'Backend returned invalid JSON') from None

    def health(self):
        with self.open('GET', '/health') as response:
            marker = response.headers.get('X-OmniVoice-Backend')
            try:
                value = json.loads(response.read(64 * 1024))
            except (ValueError, UnicodeError):
                raise CLIError('wrong_service', 'The selected endpoint is not a VoiceStudio backend') from None
            if not marker or not isinstance(value, dict) or value.get('status') != 'ok' or not isinstance(value.get('version'), str):
                raise CLIError('wrong_service', 'The selected endpoint is not a healthy VoiceStudio backend')
            self.identified = True
            return value


def multipart(fields: dict, files: dict):
    """Spool multipart bodies to disk, bounding memory for large videos."""
    boundary = 'voicestudio-' + secrets.token_hex(16)
    stream = tempfile.TemporaryFile()
    try:
        for name, value in fields.items():
            if value is None:
                continue
            stream.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            stream.write(text.encode('utf-8') + b'\r\n')
        for name, source in files.items():
            path = Path(source)
            filename = path.name.replace('"', '_').replace('\r', '_').replace('\n', '_').replace('\\', '_')
            media = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            stream.write((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                          f'filename="{filename}"\r\nContent-Type: {media}\r\n\r\n').encode())
            with path.open('rb') as audio:
                while chunk := audio.read(CHUNK):
                    stream.write(chunk)
            stream.write(b'\r\n')
        stream.write(f'--{boundary}--\r\n'.encode())
        length = stream.tell()
        stream.seek(0)
        return stream, f'multipart/form-data; boundary={boundary}', length
    except BaseException:
        stream.close()
        raise


def download(response, destination: str, *, overwrite: bool = False) -> dict:
    import sys
    if destination == '-':
        if is_terminal(sys.stdout):
            raise CLIError('usage_error', 'Refusing binary output to a terminal; use --output <file>', exit_code=2)
        while chunk := response.read(CHUNK):
            sys.stdout.buffer.write(chunk)
        return {'output': '-'}
    path = Path(destination).expanduser()
    if path.exists() and not overwrite:
        raise CLIError('output_exists', f'Output already exists: {path}. Use --overwrite to replace it.')
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.part', dir=path.parent)
    received = 0
    try:
        with os.fdopen(fd, 'wb') as stream:
            while chunk := response.read(CHUNK):
                stream.write(chunk)
                received += len(chunk)
        expected = response.headers.get('Content-Length')
        if expected is not None and received != int(expected):
            raise CLIError('incomplete_response', 'Download ended before all bytes arrived')
        if overwrite:
            os.replace(name, path)
        else:
            # Exclusive creation prevents races from overwriting another process's result.
            try:
                os.link(name, path)
            except FileExistsError:
                raise CLIError('output_exists', f'Output already exists: {path}') from None
        return {'output': str(path), 'bytes': received}
    finally:
        Path(name).unlink(missing_ok=True)
