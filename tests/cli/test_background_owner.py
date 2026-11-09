"""The CLI reaches the desktop app's backend, or starts the app in the background."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import socket
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli import runtime, desktop
from voicestudio_cli.errors import CLIError


class Backend:
    """A VoiceStudio-shaped HTTP backend on an ephemeral loopback port."""

    def __init__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get('Authorization')))
                body = (b'{"status":"ok","version":"1.0.0"}' if self.path == '/health'
                        else b'[{"id":"p1","name":"Narrator"}]')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('X-OmniVoice-Backend', '1.0.0')
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def unused_url():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{sock.getsockname()[1]}'


class Desktop:
    """Stands in for the Electron app: records launches and acts like it."""

    def __init__(self, on_launch=None, code=None):
        self.launches = []
        self.on_launch = on_launch
        self.code = code

    def __call__(self, arguments, *, detached=False):
        self.launches.append((list(arguments), detached))
        if self.on_launch:
            self.on_launch(arguments)
        code = self.code

        class Child:
            def poll(self):
                return code

            def wait(self, timeout=None):
                return code or 0
        return Child()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICESTUDIO_USER_DATA', str(tmp_path / 'user-data'))
    monkeypatch.setenv('VOICESTUDIO_CONFIG', str(tmp_path / 'cli.json'))
    monkeypatch.setenv('OMNIVOICE_STARTUP_BUDGET_S', '5')
    monkeypatch.delenv('VOICESTUDIO_URL', raising=False)
    monkeypatch.delenv('OMNIVOICE_API_KEY', raising=False)
    monkeypatch.setattr(runtime.time, 'sleep', lambda _s: None)
    (tmp_path / 'user-data').mkdir()
    backends = []
    yield tmp_path, backends
    for backend in backends:
        backend.close()


def publish(tmp_path, url, **fields):
    record = {'schema': 1, 'stage': 'ready', 'url': url, 'pid': 1, 'version': '1.0.0',
              'headless': True, 'window': False, 'remote': False, **fields}
    (tmp_path / 'user-data/backend.json').write_text(json.dumps(record))


def test_uses_the_published_fallback_port_without_launching(home, monkeypatch):
    tmp_path, backends = home
    backend = Backend(); backends.append(backend)
    publish(tmp_path, backend.url)
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    client = runtime.LocalClient(unused_url(), '', 5, 'voices list')
    assert client.json('GET', '/profiles') == [{'id': 'p1', 'name': 'Narrator'}]
    assert launch.launches == []


def test_an_unpublished_backend_on_the_default_port_is_used(home, monkeypatch):
    # An older app, a source checkout or Docker publishes no backend.json.
    tmp_path, backends = home
    monkeypatch.delenv('VOICESTUDIO_USER_DATA')
    monkeypatch.setattr(runtime, 'user_data', lambda: tmp_path / 'user-data')
    backend = Backend(); backends.append(backend)
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    assert runtime.LocalClient(backend.url, '', 5, 'voices list').json('GET', '/profiles')[0]['id'] == 'p1'
    assert launch.launches == []


def test_a_chosen_profile_never_borrows_the_default_ports_backend(home, monkeypatch):
    # VOICESTUDIO_USER_DATA (set by the fixture) selects a profile with nothing running;
    # the backend on the default port belongs to some other profile.
    _, backends = home
    backend = Backend(); backends.append(backend)
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    with pytest.raises(CLIError) as error:
        runtime.LocalClient(backend.url, '', 5, 'status').health()
    assert error.value.code == 'backend_stopped'


def test_starts_the_desktop_app_in_the_background_and_waits_for_it(home, monkeypatch):
    tmp_path, backends = home

    def started(_arguments):
        backend = Backend(); backends.append(backend)
        publish(tmp_path, backend.url)
    launch = Desktop(started); monkeypatch.setattr(runtime, 'desktop_process', launch)
    client = runtime.LocalClient(unused_url(), '', 5, 'speak', quiet=True)
    assert client.json('GET', '/profiles')[0]['id'] == 'p1'
    assert launch.launches == [([desktop.BACKGROUND_FLAG], True)]


@pytest.mark.parametrize('command', ['status', 'models loaded', 'jobs cancel', 'connection test'])
def test_state_queries_never_start_the_app(home, monkeypatch, command):
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    with pytest.raises(CLIError) as error:
        runtime.LocalClient(unused_url(), '', 5, command).health()
    assert error.value.code == 'backend_stopped'
    assert launch.launches == []


@pytest.mark.parametrize('stage, code', [('setup_required', 'runtime_not_ready'),
                                         ('failed', 'backend_failed'), ('crashed', 'backend_failed'),
                                         ('port_in_use', 'backend_failed')])
def test_setup_and_startup_failures_are_reported_without_waiting(home, monkeypatch, stage, code):
    tmp_path, _ = home
    url = unused_url()
    launch = Desktop(lambda _a: publish(tmp_path, url, stage=stage))
    monkeypatch.setattr(runtime, 'desktop_process', launch)
    with pytest.raises(CLIError) as error:
        runtime.start_backend(unused_url(), quiet=True)
    assert error.value.code == code


def test_a_stale_failure_record_from_a_previous_run_does_not_fail_a_new_start(home, monkeypatch):
    tmp_path, backends = home
    publish(tmp_path, unused_url(), stage='failed', pid=999)

    def started(_arguments):
        backend = Backend(); backends.append(backend)
        publish(tmp_path, backend.url)
    monkeypatch.setattr(runtime, 'desktop_process', Desktop(started))
    assert runtime.start_backend(unused_url(), quiet=True).startswith('http://127.0.0.1:')


def test_app_that_exits_during_startup_is_an_error(home, monkeypatch):
    monkeypatch.setattr(runtime, 'desktop_process', Desktop(code=1))
    with pytest.raises(CLIError) as error:
        runtime.start_backend(unused_url(), quiet=True)
    assert error.value.code == 'backend_failed'


def test_remote_or_offsite_records_are_never_followed(home, monkeypatch):
    tmp_path, _ = home
    publish(tmp_path, 'https://studio.example.com', remote=True)
    with pytest.raises(CLIError) as error:
        runtime.running_backend(unused_url())
    assert error.value.code == 'remote_backend'
    publish(tmp_path, 'http://example.com:3900')
    assert runtime.discovery() is None


def test_saved_credential_stays_with_its_origin(home, monkeypatch):
    tmp_path, backends = home
    backend = Backend(); backends.append(backend)
    publish(tmp_path, backend.url)
    client = runtime.LocalClient(unused_url(), 'saved-secret', 5, 'voices list')
    client.json('GET', '/profiles')
    assert ('/profiles', None) in backend.requests


def test_stop_leaves_an_open_window_and_external_backends_alone(home, monkeypatch):
    tmp_path, backends = home
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    assert runtime.stop_backend(unused_url())['stopped'] is False
    backend = Backend(); backends.append(backend)
    # A chosen profile with nothing running: the default port is someone else's.
    assert runtime.stop_backend(backend.url)['stopped'] is False
    with monkeypatch.context() as default_profile:
        default_profile.delenv('VOICESTUDIO_USER_DATA')
        default_profile.setattr(runtime, 'user_data', lambda: tmp_path / 'user-data')
        with pytest.raises(CLIError) as error:
            runtime.stop_backend(backend.url)
    assert error.value.code == 'not_app_owned'
    publish(tmp_path, backend.url, window=True)
    with pytest.raises(CLIError) as error:
        runtime.stop_backend(unused_url())
    assert error.value.code == 'app_open'
    assert launch.launches == []


def test_stop_asks_the_background_app_to_quit_and_waits(home, monkeypatch):
    tmp_path, _ = home
    backend = Backend()
    publish(tmp_path, backend.url)
    launch = Desktop(lambda _a: backend.close())
    monkeypatch.setattr(runtime, 'desktop_process', launch)
    assert runtime.stop_backend(unused_url()) == {'stopped': True, 'message': 'VoiceStudio stopped.'}
    assert launch.launches == [([desktop.STOP_FLAG], False)]


def test_ownership_switches_reach_the_app_through_the_public_command():
    for flag in [desktop.BACKGROUND_FLAG, desktop.STOP_FLAG, desktop.CHECK_UPDATES_FLAG]:
        assert desktop.split_arguments([flag]) == ([], [flag])


def test_background_launch_targets_an_executable_that_receives_arguments(tmp_path, monkeypatch):
    bundle = tmp_path / 'VoiceStudio.app'
    (bundle / 'Contents/MacOS').mkdir(parents=True)
    with (bundle / 'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleExecutable': 'VoiceStudio'}, stream)
    assert desktop._direct_command(['/usr/bin/open', '-a', str(bundle), '--args']) == [
        str(bundle / 'Contents/MacOS/VoiceStudio')]
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('APPIMAGE', '/home/user/Apps/VoiceStudio.AppImage')
    assert desktop._direct_command(['/tmp/.mount_x/voicestudio-desktop']) == [
        '/home/user/Apps/VoiceStudio.AppImage']


def test_selected_user_data_is_passed_to_the_app(home, monkeypatch):
    tmp_path, _ = home
    seen = {}

    class Popen:
        def __init__(self, command, **options):
            seen['command'] = command
            seen['options'] = options
    monkeypatch.setattr(desktop, 'resolve_desktop', lambda: ['/opt/VoiceStudio/voicestudio-desktop'])
    monkeypatch.setattr(desktop.subprocess, 'Popen', Popen)
    desktop.desktop_process([desktop.BACKGROUND_FLAG], detached=True)
    assert seen['command'] == ['/opt/VoiceStudio/voicestudio-desktop', desktop.BACKGROUND_FLAG,
                               '--user-data-dir=' + str(tmp_path / 'user-data')]
    assert seen['options'].get('start_new_session') or os.name == 'nt'


def test_default_connection_respects_the_selected_backend_port(home, monkeypatch):
    from voicestudio_cli.config import connection
    monkeypatch.setenv('OMNIVOICE_PORT', '14900')
    assert connection(None)[0] == 'http://127.0.0.1:14900'


@pytest.mark.parametrize('port', ['-1', '65536', 'invalid', ''])
def test_invalid_backend_port_fails_before_execution(home, monkeypatch, port):
    from voicestudio_cli.config import connection
    monkeypatch.setenv('OMNIVOICE_PORT', port)
    with pytest.raises(CLIError) as error:
        connection(None)
    assert error.value.code == 'config_error'


def test_explicit_endpoint_ignores_unused_local_port(home, monkeypatch):
    from voicestudio_cli.config import connection
    monkeypatch.setenv('OMNIVOICE_PORT', 'invalid')
    assert connection('https://example.com')[0] == 'https://example.com'


def test_stop_never_targets_an_explicitly_selected_backend(home, monkeypatch, capsys):
    from voicestudio_cli.__main__ import main
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    assert main(['stop', '--url', 'http://127.0.0.1:9', '--json']) == 2
    assert json.loads(capsys.readouterr().err)['error']['code'] == 'usage_error'
    assert launch.launches == []


def test_stopped_status_is_an_answer_with_exit_code_3(home, monkeypatch, capsys):
    from voicestudio_cli.__main__ import main
    launch = Desktop(); monkeypatch.setattr(runtime, 'desktop_process', launch)
    monkeypatch.setenv('OMNIVOICE_PORT', unused_url().rsplit(':', 1)[1])
    assert main(['status']) == 3
    assert capsys.readouterr().out.strip() == 'VoiceStudio is not running.'
    assert main(['status', '--json']) == 3
    assert json.loads(capsys.readouterr().out) == {'running': False, 'message': 'VoiceStudio is not running.'}
    assert launch.launches == []
