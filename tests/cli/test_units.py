import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli.config import connection, save_config, normalize_url
from voicestudio_cli.errors import CLIError, scrub
from voicestudio_cli.transport import download, multipart, Client
import voicestudio_cli.__main__ as command_module
from voicestudio_cli.__main__ import main, stream_events


class Response(io.BytesIO):
    def __init__(self, data, content_type='application/octet-stream', length=None):
        super().__init__(data)
        self.headers = {'Content-Type': content_type, 'Content-Length': str(len(data) if length is None else length)}


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        env = patch.dict(os.environ, {'VOICESTUDIO_CONFIG': str(self.path / 'config.json')})
        env.start(); self.addCleanup(env.stop)
        for name in ['VOICESTUDIO_URL', 'OMNIVOICE_API_KEY']:
            os.environ.pop(name, None)

    def test_connection_precedence_and_credential_binding(self):
        save_config({'url': 'http://127.0.0.1:3900', 'credential_url': 'http://127.0.0.1:3900', 'api_key': 'saved'})
        self.assertEqual(connection(None), ('http://127.0.0.1:3900', 'saved', 'configuration'))
        os.environ['VOICESTUDIO_URL'] = 'https://other.example'
        self.assertEqual(connection(None), ('https://other.example', '', 'environment'))
        self.assertEqual(connection('https://explicit.example')[2], 'flag')
        os.environ['OMNIVOICE_API_KEY'] = 'environment-key'
        self.assertEqual(connection(None)[1], 'environment-key')
        if os.name != 'nt':
            self.assertEqual((self.path / 'config.json').stat().st_mode & 0o777, 0o600)

    def test_unsafe_urls_and_credential_transports(self):
        for url in ['file:///etc/passwd', 'http://user:password@host', 'http://host?token=x',
                    'http://host/#secret', 'http://host:0', 'http://host:99999', 'http://host/\n']:
            with self.subTest(url=url), self.assertRaises(CLIError):
                normalize_url(url)
        with self.assertRaisesRegex(CLIError, 'HTTPS'):
            Client('http://remote.example', 'secret')

    def test_partial_download_does_not_replace_existing_file(self):
        destination = self.path / 'result.wav'; destination.write_bytes(b'original')
        with self.assertRaises(CLIError):
            download(Response(b'partial', length=100), str(destination), overwrite=True)
        self.assertEqual(destination.read_bytes(), b'original')
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ['result.wav'])

    def test_existing_output_is_preserved(self):
        destination = self.path / 'result.wav'; destination.write_bytes(b'original')
        with self.assertRaises(CLIError):
            download(Response(b'replacement'), str(destination))
        self.assertEqual(destination.read_bytes(), b'original')

    def test_successful_download_unicode_path(self):
        destination = self.path / 'speech café.wav'
        download(Response(b'audio'), str(destination))
        self.assertEqual(destination.read_bytes(), b'audio')

    def test_multipart_bytes_and_filename_escaping(self):
        path = self.path / 'clip " name.wav'
        # A quoted filename is legal on POSIX, not Windows. Exercise header
        # escaping on both platforms without asking the OS to create it.
        with patch.object(Path, 'open', return_value=io.BytesIO(b'\x00\x01\xff')):
            stream, ct, length = multipart({'text': 'café'}, {'file': path})
        with stream:
            data = stream.read()
        self.assertEqual(len(data), length)
        self.assertIn(b'\x00\x01\xff', data)
        self.assertIn('café'.encode(), data)
        self.assertNotIn(b'filename="clip "', data)
        self.assertIn('multipart/form-data', ct)

    def test_error_redaction_and_terminal_escapes(self):
        raw = 'Bearer abc\x1b[31m {"api_key":"secret"} https://u:p@host/?token=secret'
        output = scrub(raw, ['abc'])
        self.assertNotIn('abc', output); self.assertNotIn('secret', output)
        self.assertNotIn('u:p', output); self.assertNotIn('\x1b', output)

    def test_parser_failures_are_structured(self):
        for args in [['speak'], ['engines', 'list', '--type', 'wrong'],
                     ['status', '--timeout', 'NaN'], ['status', '--timeout', '-1'],
                     ['voices', 'show'], ['voi', '--help']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([*args, '--json']), 2)
                self.assertEqual(json.loads(err.getvalue())['error']['code'], 'usage_error')

    def test_sse_split_data_and_truncation(self):
        raw = b'event: progress\ndata: {"type":\ndata: "progress"}\n\ndata: {"type":"done"}\n\n'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(stream_events(Response(raw, 'text/event-stream'))['type'], 'done')
        with self.assertRaises(CLIError):
            stream_events(Response(b'{bad}\n', 'application/x-ndjson'))

    def test_interruption_has_structured_error_and_exit_code(self):
        with patch.object(command_module, 'dispatch', side_effect=KeyboardInterrupt), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['version', '--json']), 130)
        self.assertEqual(json.loads(err.getvalue())['error']['code'], 'cancelled')

    def test_interrupted_download_cleans_up_and_preserves_destination(self):
        class Interrupted(Response):
            def read(self, *_):
                raise KeyboardInterrupt
        destination = self.path / 'output.wav'; destination.write_bytes(b'original')
        with self.assertRaises(KeyboardInterrupt):
            download(Interrupted(b''), str(destination), overwrite=True)
        self.assertEqual(destination.read_bytes(), b'original')
        self.assertEqual(list(self.path.iterdir()), [destination])

    def test_transport_timeout_and_tls_failures(self):
        from urllib.error import URLError
        import ssl
        for error in [TimeoutError('timeout'), URLError('DNS lookup failed'), ssl.SSLError('certificate invalid')]:
            with self.subTest(error=type(error).__name__):
                client = Client('http://127.0.0.1:1')
                with patch.object(client.opener, 'open', side_effect=error):
                    with self.assertRaises(CLIError) as exc:
                        client.health()
                self.assertEqual(exc.exception.code, 'connection_error')


if __name__ == '__main__':
    unittest.main()


class PlatformOutputTests(unittest.TestCase):
    """Output and prompts behave the same on Windows as on macOS and Linux."""

    def test_output_uses_lf_even_where_the_platform_default_is_crlf(self):
        raw = io.BytesIO()
        windows_stdout = io.TextIOWrapper(raw, encoding='cp1252', newline='\r\n')
        with patch.object(sys, 'stdout', windows_stdout):
            self.assertEqual(main(['completion', 'bash']), 0)
            windows_stdout.flush()
        self.assertIn(b'\n', raw.getvalue())
        self.assertNotIn(b'\r\n', raw.getvalue())

    def test_windows_nul_device_is_not_a_terminal(self):
        from types import SimpleNamespace
        import voicestudio_cli.terminal as terminal

        class Nul(io.StringIO):
            def isatty(self):
                return True  # What Windows reports for NUL.
            def fileno(self):
                return 0
        with patch.object(terminal, 'os', SimpleNamespace(name='nt')), \
                patch.object(terminal, '_windows_console', lambda fd: False):
            self.assertFalse(terminal.is_terminal(Nul()))
            with patch.object(sys, 'stdin', Nul()), patch.object(sys, 'stderr', io.StringIO()) as err:
                with self.assertRaises(CLIError) as raised:
                    command_module.confirm(SimpleNamespace(yes=False), 'Confirm clearing')
                self.assertEqual(raised.exception.code, 'confirmation_required')
                self.assertEqual(err.getvalue(), '')  # no prompt nobody can answer
        with patch.object(terminal, 'os', SimpleNamespace(name='nt')), \
                patch.object(terminal, '_windows_console', lambda fd: True):
            self.assertTrue(terminal.is_terminal(Nul()))


class TaskFollowTests(unittest.TestCase):
    def test_ctrl_c_while_following_cancels_the_backend_task(self):
        from types import SimpleNamespace
        calls = []

        class Interrupted:
            def open(self, method, path, **kwargs):
                calls.append((method, path))
                raise KeyboardInterrupt

            def json(self, method, path, **kwargs):
                calls.append((method, path))
                return {'cancelled': True}

        with self.assertRaises(KeyboardInterrupt):
            command_module.follow_task(Interrupted(), 'dub_job/1', SimpleNamespace(json=True))
        self.assertEqual(calls, [('GET', '/tasks/stream/dub_job%2F1'), ('POST', '/tasks/cancel/dub_job%2F1')])
