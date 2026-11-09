"""Exercise CLI requests against real FastAPI routers with an isolated database.

Inference and the desktop supervisor are deliberately outside this test's scope.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli_backend(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import describe_voice, projects
    from core import db

    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'cli.db'))
    db.ensure_schema()
    app = FastAPI()
    app.include_router(describe_voice.router)
    app.include_router(projects.router)

    @app.get('/health')
    def health():
        from fastapi.responses import JSONResponse
        return JSONResponse({'status': 'ok', 'version': 'test'}, headers={'X-OmniVoice-Backend': 'test'})

    with TestClient(app) as client:
        class Bridge(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def call(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                response = client.request(self.command, self.path, content=body,
                                          headers={'Content-Type': self.headers.get('Content-Type', 'application/json')})
                self.send_response(response.status_code)
                for key in ['Content-Type', 'X-OmniVoice-Backend']:
                    if key in response.headers:
                        self.send_header(key, response.headers[key])
                self.send_header('Content-Length', str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = call

        server = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**os.environ, 'PYTHONPATH': str(ROOT / 'cli'),
                   'VOICESTUDIO_CONFIG': str(tmp_path / 'config.json'),
                   'VOICESTUDIO_URL': f'http://127.0.0.1:{server.server_port}'}
            env.pop('OMNIVOICE_API_KEY', None)
            def invoke(*args):
                return subprocess.run([sys.executable, '-m', 'voicestudio_cli', *args, '--json'],
                                      env=env, capture_output=True, text=True, timeout=15)
            yield invoke
        finally:
            server.shutdown(); server.server_close(); thread.join()


def test_cli_voice_description_uses_real_router(cli_backend):
    result = cli_backend('voices', 'describe', 'elderly female with low pitch')
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response['attrs']['Gender'] == 'female'
    assert response['matched']


def test_cli_project_crud_roundtrip_and_not_found(cli_backend):
    created = cli_backend('projects', 'create', '--field', 'name=CLI test', '--field', 'state={}')
    assert created.returncode == 0, created.stderr
    project_id = json.loads(created.stdout)['id']
    renamed = cli_backend('projects', 'rename', project_id, 'Renamed')
    assert renamed.returncode == 0, renamed.stderr
    shown = cli_backend('projects', 'show', project_id)
    assert json.loads(shown.stdout)['name'] == 'Renamed'
    removed = cli_backend('projects', 'delete', project_id, '--yes')
    assert removed.returncode == 0, removed.stderr
    missing = cli_backend('projects', 'show', project_id)
    assert missing.returncode == 1
    assert json.loads(missing.stderr)['error']['status'] == 404


def test_cli_reports_real_backend_validation_failure(cli_backend):
    result = cli_backend('projects', 'create', '--field', 'name=Test', '--field', 'state=wrong-type')
    assert result.returncode == 1
    assert json.loads(result.stderr)['error']['code'] == 'validation_error'
