"""Actual HTTP ranges must prove their identity before a model is published."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.mark.parametrize('mode', ['wrong_start', 'wrong_total', 'ignored_range', 'valid', 'valid_unknown_total'])
def test_wrong_range_response_never_commits_model(tmp_path, mode):
    from services.segmented_download import segmented_download

    content = b'A' * (4 * 1024 * 1024) + b'B' * (4 * 1024 * 1024)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()

        def do_GET(self):
            lo, hi = map(int, self.headers['Range'].removeprefix('bytes=').split('-'))
            self.send_response(200 if mode == 'ignored_range' else 206)
            advertised_lo = lo + 1 if mode == 'wrong_start' else lo
            total = '*' if mode == 'valid_unknown_total' else len(content) + 1 if mode == 'wrong_total' else len(content)
            self.send_header('Content-Range', f'bytes {advertised_lo}-{hi}/{total}')
            self.send_header('Content-Length', str(hi - lo + 1))
            self.end_headers()
            try:
                self.wfile.write(content[lo:hi + 1] if mode.startswith('valid') else b'X' * (hi - lo + 1))
            except (BrokenPipeError, ConnectionResetError):
                pass  # Invalid headers may be rejected before the response body.

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    destination = tmp_path / 'model.bin'
    try:
        call = segmented_download(f'http://127.0.0.1:{server.server_port}/model', str(destination), num_connections=2)
        if mode.startswith('valid'):
            asyncio.run(call)
            assert destination.read_bytes() == content
        else:
            with pytest.raises(ValueError, match='range'):
                asyncio.run(call)
            assert not destination.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
