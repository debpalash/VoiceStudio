"""Real subprocess/HTTP tests, with an independently controlled service."""
import contextlib
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from urllib.parse import urlsplit

import test_cli as base
ROOT = base.ROOT
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli.registry import COMMANDS
from voicestudio_cli.__main__ import build_parser


class Server(ThreadingHTTPServer):
    daemon_threads = True


class HTTPTests(base.CommandTests):
    @classmethod
    def setUpClass(cls):
        cls.requests = queue.Queue()
        cls.response = (200, 'application/json', b'{"ok":true}', {})
        cls.health = True
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def handle_request(self):
                path = urlsplit(self.path).path
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if path == '/health':
                    status, ct, data, headers = (200, 'application/json', b'{"status":"ok","version":"0.5.7"}', {})
                    if not cls.health:
                        data = b'{"status":"ok","version":"unrelated"}'
                elif path == '/profiles' and self.command == 'GET':
                    status, ct, data, headers = 200, 'application/json', b'[{"id":"p1","name":"Narrator"}]', {}
                elif path in cls.routes:
                    cls.requests.put((self.command, self.path, dict(self.headers), body))
                    status, ct, data, headers = cls.routes[path]
                else:
                    cls.requests.put((self.command, self.path, dict(self.headers), body))
                    status, ct, data, headers = cls.response
                self.send_response(status)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(data)))
                if cls.health:
                    self.send_header('X-OmniVoice-Backend', '0.5.7')
                for key, val in headers.items():
                    self.send_header(key, val)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(data)
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request
        cls.server = Server(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        super().setUp()
        self.env['VOICESTUDIO_URL'] = f'http://127.0.0.1:{self.server.server_port}'
        type(self).health = True
        type(self).routes = {}
        type(self).response = (200, 'application/json', b'{"ok":true}', {})
        while not self.requests.empty():
            self.requests.get_nowait()

    # The inherited refusal test needs an intentionally disconnected target.
    def test_connection_refused_is_actionable(self):
        self.env['VOICESTUDIO_URL'] = 'http://127.0.0.1:1'
        super().test_connection_refused_is_actionable()

    def test_all_registered_command_help(self):
        root, nodes = build_parser()
        for path, parser in nodes.items():
            with self.subTest(path=path), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    root.parse_args([*path, '--help'])
                self.assertEqual(exit.exception.code, 0)
                self.assertIn('usage:', parser.format_help())

    def test_registry_success_and_http_failures(self):
        """Every declarative command traverses the real parser, HTTP client and output handler."""
        contracts = json.loads((ROOT / 'cli/voicestudio_cli/contracts.json').read_text(encoding='utf-8'))
        _, nodes = build_parser()
        for spec in COMMANDS:
            parser = nodes[tuple(spec.name.split())]
            if parser.get_default('spec') != spec:
                continue  # A custom handler has dedicated tests instead.
            with self.subTest(command=spec.name):
                args = spec.name.split() + ['value'] * len(spec.params)
                sample = Path(self.temp.name) / 'input.wav'
                sample.write_bytes(b'RIFF-test-input')
                if spec.upload:
                    args.append(str(sample))
                contract = contracts.get(spec.name, {})
                fields = {k: 'example' for k in contract.get('required', []) if k != spec.upload}
                for k, v in fields.items():
                    args += ['--field', f'{k}={v}']
                for k in contract.get('required_query', []):
                    args += ['--query', f'{k}=example']
                if spec.destructive:
                    args.append('--yes')
                output = Path(self.temp.name) / 'result.bin'
                output.unlink(missing_ok=True)
                if spec.task:
                    type(self).response = (200, 'application/json', b'{"task_id":"t1"}', {})
                    type(self).routes = {'/tasks/stream/t1': (200, 'text/event-stream', b'data: {"type":"done"}\n\n', {})}
                elif spec.binary:
                    args += ['-o', str(output)]
                    type(self).response = (200, 'audio/wav', b'RIFF-output', {})
                elif spec.stream:
                    type(self).response = (200, 'text/event-stream', b'data: {"type":"done"}\n\n', {})
                else:
                    type(self).response = (200, 'application/json', b'{"ok":true}', {})
                result = self.run_cli(*args, '--json')
                self.assertEqual(result.returncode, 0, result.stderr)
                if spec.name == 'voices list':
                    continue
                method, path, headers, body = self.requests.get(timeout=1)
                self.assertEqual(method, spec.method)
                self.assertEqual(urlsplit(path).path, spec.route.format(**{p: 'value' for p in spec.params}))
                if spec.binary:
                    self.assertEqual(output.read_bytes(), b'RIFF-output')
                    output.unlink()
                if spec.task:
                    self.assertEqual(urlsplit(self.requests.get(timeout=1)[1]).path, '/tasks/stream/t1')
                type(self).response = (409, 'application/json', b'{"detail":"operation conflict"}', {})
                result = self.run_cli(*args, '--json')
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error']['code'], 'conflict')
                self.requests.get(timeout=1)

    def test_http_error_matrix(self):
        for status, code in [(400, 'backend_error'), (401, 'authentication_error'), (403, 'permission_denied'),
                             (404, 'not_found'), (409, 'conflict'), (422, 'validation_error'),
                             (429, 'rate_limited'), (500, 'backend_error'), (503, 'backend_error')]:
            with self.subTest(status=status):
                type(self).response = (status, 'application/json', b'{"detail":{"message":"failed"}}', {'Retry-After': '3'})
                r = self.run_cli('engines', 'list', '--json')
                err = json.loads(r.stderr)['error']
                self.assertEqual(r.returncode, 1)
                self.assertEqual((err['code'], err['status'], err['retry_after']), (code, status, '3'))
                self.assertEqual(r.stdout, b'')

    def test_wrong_service_never_receives_mutation(self):
        type(self).health = False
        r = self.run_cli('projects', 'rename', '123', 'New name', '--json')
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'wrong_service')
        self.assertTrue(self.requests.empty())

    def test_speak_sends_selected_voice_and_engine_without_global_change(self):
        type(self).response = (200, 'audio/wav', b'RIFF-generation', {})
        output = Path(self.temp.name) / 'speech.wav'
        r = self.run_cli('speak', 'Hello', '--voice', 'Narrator', '--engine', 'kitten', '-o', str(output))
        self.assertEqual(r.returncode, 0, r.stderr)
        method, path, headers, body = self.requests.get(timeout=1)
        self.assertEqual((method, path), ('POST', '/generate'))
        form = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: ' + headers['Content-Type'] + '\r\n\r\n').encode() + body)
        fields = {part.get_param('name', header='content-disposition'): part.get_content() for part in form.iter_parts()}
        self.assertIn('p1', str(fields['profile_id']))
        self.assertIn('kitten', str(fields['engine']))
        self.assertEqual(output.read_bytes(), b'RIFF-generation')
        self.assertTrue(self.requests.empty())

    def test_transcription_upload_and_text_output(self):
        audio = Path(self.temp.name) / 'my recording.wav'; audio.write_bytes(b'RIFF-audio')
        type(self).response = (200, 'text/plain', b'Hello world', {})
        r = self.run_cli('transcribe', str(audio), '--format', 'text')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, b'Hello world\n')
        method, path, headers, body = self.requests.get(timeout=1)
        self.assertEqual((method, path), ('POST', '/v1/audio/transcriptions'))
        self.assertIn(b'RIFF-audio', body)
        self.assertIn(b'my recording.wav', body)

    def test_form_contract_rejects_missing_fields_before_http(self):
        audio = Path(self.temp.name) / 'clip.wav'; audio.write_bytes(b'RIFF')
        r = self.run_cli('gallery', 'import', str(audio), '--json')
        self.assertEqual(r.returncode, 2)
        self.assertIn('name', json.loads(r.stderr)['error']['message'])
        self.assertTrue(self.requests.empty())

    def test_optional_body_is_omitted_without_fields(self):
        """Cleaning the saved transcript sends no body; given segments are sent as JSON."""
        r = self.run_cli('dub', 'transcript', 'clean', 'job1', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        method, path, headers, body = self.requests.get(timeout=1)
        self.assertEqual((method, urlsplit(path).path, body), ('POST', '/dub/cleanup-segments/job1', b''))
        segments = Path(self.temp.name) / 'segments.json'
        segments.write_text(json.dumps({'segments': [{'text': 'hi'}]}), encoding='utf-8')
        r = self.run_cli('dub', 'transcript', 'clean', 'job1', '--file', str(segments), '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.requests.get(timeout=1)[3]
        self.assertEqual(json.loads(body), {'segments': [{'text': 'hi'}]})

    def test_delete_requires_confirmation_without_http(self):
        r = self.run_cli('history', 'clear', '--json')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'confirmation_required')
        self.assertTrue(self.requests.empty())

    def test_redirect_is_not_followed(self):
        type(self).response = (302, 'text/plain', b'', {'Location': '/redirect-target'})
        r = self.run_cli('engines', 'list', '--json')
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'redirect_refused')
        self.assertEqual(self.requests.qsize(), 1)

    def test_malformed_json(self):
        type(self).response = (200, 'application/json', b'{bad', {})
        r = self.run_cli('engines', 'list', '--json')
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'invalid_response')

    def test_stream_error_after_200_is_failure(self):
        payload = Path(self.temp.name) / 'plan.json'; payload.write_text('{"chapters":[]}')
        type(self).response = (200, 'text/event-stream', b'data: {"type":"error","detail":"model failed"}\n\n', {})
        r = self.run_cli('stories', 'render', '--file', str(payload), '--json')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stderr)['error']['message'], 'model failed')

    def test_stream_without_completion_is_failure(self):
        type(self).response = (200, 'application/x-ndjson', b'{"type":"progress","percent":20}\n', {})
        r = self.run_cli('stories', 'render', '--field', 'chapters=[]', '--json')
        self.assertEqual(r.returncode, 1)
        self.assertIn(b'completion', r.stderr)

    def test_engine_selection_and_supported_model_are_persisted_explicitly(self):
        for family, engine, model in [('tts', 'mlx-audio', 'kokoro'), ('asr', 'faster-whisper', 'Systran/faster-whisper-small')]:
            with self.subTest(family=family):
                r = self.run_cli('engines', 'select', engine, '--type', family, '--model', model)
                self.assertEqual(r.returncode, 0, r.stderr)
                method, path, headers, body = self.requests.get(timeout=1)
                self.assertEqual((method, path), ('POST', '/engines/select'))
                self.assertEqual(json.loads(body), {'family': family, 'backend_id': engine, 'model_id': model})
        r = self.run_cli('engines', 'select', 'argos', '--type', 'translation')
        self.assertEqual(r.returncode, 0, r.stderr)
        method, path, headers, body = self.requests.get(timeout=1)
        self.assertEqual((path, json.loads(body)), ('/engines/translation/select', {'engine_id': 'argos'}))

    def test_settings_fields_and_typed_values(self):
        for key, value, field in [('compute-device', 'cpu', 'value'), ('performance-profile', 'balanced', 'tier'),
                                  ('history-retention', '100', 'cap'), ('pause-fill', 'silence', 'mode')]:
            with self.subTest(key=key):
                r = self.run_cli('settings', 'set', key, value)
                self.assertEqual(r.returncode, 0, r.stderr)
                method, path, headers, body = self.requests.get(timeout=1)
                expected = 100 if key == 'history-retention' else value
                self.assertEqual(json.loads(body), {field: expected})
                self.assertEqual(method, 'PUT')

    def test_model_library_installed_filter(self):
        type(self).response = (200, 'application/json', json.dumps({'models': [
            {'repo_id': 'owner/one', 'installed': True}, {'repo_id': 'owner/two', 'installed': False}]}).encode(), {})
        r = self.run_cli('models', 'list', '--installed', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([m['repo_id'] for m in json.loads(r.stdout)['models']], ['owner/one'])

    def test_model_install_requires_separate_license_acceptance(self):
        type(self).routes = {
            '/models': (200, 'application/json', b'{"models":[{"repo_id":"owner/model","installed":false,"supported":true}]}', {}),
            '/models/licenses/acceptance/owner%2Fmodel': (200, 'application/json', b'{"required":true,"accepted":false,"fingerprint":"v2:example"}', {}),
            '/models/licenses/details/owner%2Fmodel': (200, 'application/json', b'{"license":"Read these terms"}', {}),
        }
        r = self.run_cli('models', 'install', 'owner/model', '--json', '--yes')
        self.assertEqual(r.returncode, 1)
        self.assertIn(b'license_required', r.stderr)
        paths = [self.requests.get_nowait()[1] for _ in range(self.requests.qsize())]
        self.assertNotIn('/models/install', paths)
        r = self.run_cli('models', 'install', 'owner/model', '--accept-license', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        requests = [self.requests.get_nowait() for _ in range(self.requests.qsize())]
        self.assertEqual([r[1] for r in requests][-2:], ['/models/licenses/accept', '/models/install'])
        self.assertEqual(json.loads(requests[-2][3])['fingerprint'], 'v2:example')

    def test_license_accept_covers_every_licence_the_engine_needs(self):
        """OmniVoice also needs the Higgs tokenizer's licence, which has no catalogue row (#3855)."""
        model = {'repo_id': 'k2-fsa/OmniVoice', 'installed': True, 'supported': True,
                 'license_dependencies_complete': True,
                 'license_dependencies': [{'repo_id': 'eustlb/higgs-audio-v2-tokenizer', 'required': True,
                                           'accepted': False, 'fingerprint': 'v2:higgs'}]}
        type(self).routes = {
            '/models': (200, 'application/json', json.dumps({'models': [model]}).encode(), {}),
            '/models/licenses/acceptance/k2-fsa%2FOmniVoice': (200, 'application/json',
                b'{"required":true,"accepted":true,"fingerprint":"v2:omni"}', {}),
            '/models/licenses/details/eustlb%2Fhiggs-audio-v2-tokenizer': (200, 'application/json', json.dumps({
                'info': {'license': 'Higgs licence', 'source_url': 'https://example.test/higgs'},
                'acceptance': {'fingerprint': 'v2:higgs'}}).encode(), {}),
        }
        r = self.run_cli('models', 'license', 'accept', 'k2-fsa/OmniVoice')
        self.assertEqual(r.returncode, 1)
        self.assertIn(b'eustlb/higgs-audio-v2-tokenizer', r.stderr)
        self.assertIn(b'https://example.test/higgs', r.stderr)
        paths = [self.requests.get_nowait()[1] for _ in range(self.requests.qsize())]
        self.assertNotIn('/models/licenses/accept', paths)
        r = self.run_cli('models', 'license', 'accept', 'k2-fsa/OmniVoice', '--accept-license', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        accepted = [json.loads(body) for _, path, _, body in
                    [self.requests.get_nowait() for _ in range(self.requests.qsize())]
                    if path == '/models/licenses/accept']
        self.assertEqual(accepted, [{'repo_id': 'eustlb/higgs-audio-v2-tokenizer',
                                     'fingerprint': 'v2:higgs', 'accepted': True}])

    def test_incomplete_licence_list_is_never_accepted_as_the_whole_set(self):
        type(self).routes = {'/models': (200, 'application/json', json.dumps({'models': [
            {'repo_id': 'k2-fsa/OmniVoice', 'supported': True, 'license_dependencies_complete': False}]}).encode(), {})}
        r = self.run_cli('models', 'license', 'accept', 'k2-fsa/OmniVoice', '--accept-license', '--json')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'license_lookup_failed')

    def test_dub_generate_follows_the_task_and_saves_the_track(self):
        type(self).routes = {
            '/dub/generate/job1': (200, 'application/json', b'{"task_id":"dub_job1_1"}', {}),
            '/tasks/stream/dub_job1_1': (200, 'text/event-stream',
                b'data: {"type":"progress","current":1,"total":2}\n\n'
                b'data: {"type":"done","language_code":"es","tracks":["es"]}\n\n', {}),
            '/dub/download-audio/job1': (200, 'audio/wav', b'RIFF-dubbed', {}),
        }
        plan = Path(self.temp.name) / 'plan.json'; plan.write_text('{"segments":[]}', encoding='utf-8')
        output = Path(self.temp.name) / 'dubbed.wav'
        r = self.run_cli('dub', 'generate', 'job1', '--file', str(plan), '-o', str(output), '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(output.read_bytes(), b'RIFF-dubbed')
        paths = [self.requests.get_nowait()[1] for _ in range(self.requests.qsize())]
        self.assertEqual([urlsplit(p).path for p in paths],
                         ['/dub/generate/job1', '/tasks/stream/dub_job1_1', '/dub/download-audio/job1'])
        self.assertEqual(urlsplit(paths[-1]).query, 'lang=es')

    def test_failed_or_cancelled_dub_task_is_a_failure_and_writes_nothing(self):
        plan = Path(self.temp.name) / 'plan.json'; plan.write_text('{"segments":[]}', encoding='utf-8')
        output = Path(self.temp.name) / 'dubbed.wav'
        for events, code in [(b'data: {"type":"error","error":"The speech does not fit its time slot."}\n\n',
                              'backend_error'),
                             (b'data: {"type":"cancelled"}\n\n', 'cancelled')]:
            with self.subTest(code=code):
                type(self).routes = {
                    '/dub/generate/job1': (200, 'application/json', b'{"task_id":"dub_job1_1"}', {}),
                    '/tasks/stream/dub_job1_1': (200, 'text/event-stream', events, {}),
                }
                r = self.run_cli('dub', 'generate', 'job1', '--file', str(plan), '-o', str(output), '--json')
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(json.loads(r.stderr)['error']['code'], code)
                self.assertFalse(output.exists())
                while not self.requests.empty():
                    self.requests.get_nowait()

    def test_existing_output_prevents_generation(self):
        output = Path(self.temp.name) / 'speech.wav'; output.write_bytes(b'original')
        r = self.run_cli('speak', 'Hi', '--voice', 'Narrator', '-o', str(output), '--json')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(output.read_bytes(), b'original')
        self.assertTrue(self.requests.empty())

    def test_custom_http_command_success_contracts(self):
        clip = Path(self.temp.name) / 'reference.wav'; clip.write_bytes(b'RIFF-reference')
        cases = [
            (['voices', 'clone', '--name', 'New', '--audio', str(clip)], 'POST', '/profiles'),
            (['voices', 'describe', 'female'], 'POST', '/design/describe'),
            (['voices', 'consent', 'show', 'p1'], 'GET', '/profiles/p1'),
            (['history', 'star', 'h1'], 'PUT', '/history/h1/starred'),
            (['history', 'unstar', 'h1'], 'PUT', '/history/h1/starred'),
            (['models', 'install-cancel', 'owner/model'], 'POST', '/models/install/cancel'),
            (['engines', 'install', 'gpt-sovits', '--type', 'tts'], 'POST', '/engines/sidecar/gpt-sovits/install'),
            (['engines', 'install-status', 'gpt-sovits', '--type', 'tts'], 'GET', '/engines/sidecar/gpt-sovits/install/status'),
            (['settings', 'get', 'compute-device'], 'GET', '/api/settings/compute-device'),
            (['integrations', 'mcp', 'bindings', 'set', 'agent', '--voice', 'p1'], 'PUT', '/api/mcp/bindings'),
            (['projects', 'rename', 'p1', 'New name'], 'PATCH', '/projects/p1'),
        ]
        for args, method, path in cases:
            with self.subTest(args=args):
                r = self.run_cli(*args, '--json')
                self.assertEqual(r.returncode, 0, r.stderr)
                actual = self.requests.get(timeout=1)
                self.assertEqual(actual[:2], (method, path))
                type(self).response = (403, 'application/json', b'{"detail":"access refused"}', {})
                r = self.run_cli(*args, '--json')
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(json.loads(r.stderr)['error']['code'], 'permission_denied')
                self.requests.get(timeout=1)
                type(self).response = (200, 'application/json', b'{"ok":true}', {})

    def test_voice_design_maps_description_to_profile_fields(self):
        type(self).routes = {'/design/describe': (200, 'application/json',
                              b'{"attrs":{"Gender":"female"},"instruct":"female","matched":[{}]}', {})}
        r = self.run_cli('voices', 'design', '--name', 'Designed', '--description', 'female', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        requests = [self.requests.get_nowait() for _ in range(self.requests.qsize())]
        self.assertEqual([r[1] for r in requests], ['/design/describe', '/profiles'])
        self.assertIn(b'vd_states', requests[-1][3])
        self.assertIn(b'female', requests[-1][3])

    def test_cli_only_configuration_and_mcp_exports_are_offline(self):
        for args in [('connection', 'show'), ('integrations', 'api', 'docs'),
                     ('integrations', 'mcp', 'config'), ('integrations', 'mcp', 'config', '--client', 'codex')]:
            with self.subTest(args=args):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(self.requests.empty())

    def test_credentialed_configuration_does_not_echo_secrets(self):
        r = self.run_cli('connection', 'auth', 'set', '--stdin', '--json', input=b'secret-from-stdin')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(b'secret-from-stdin', r.stdout + r.stderr)
        r = self.run_cli('connection', 'auth', 'clear', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bad_json_and_unknown_fields_never_send_request(self):
        bad = Path(self.temp.name) / 'bad.json'; bad.write_text('{broken')
        for args in [('projects', 'create', '--file', str(bad)),
                     ('projects', 'create', '--field', 'name=Name', '--field', 'state={}', '--field', 'typo=true')]:
            with self.subTest(args=args):
                r = self.run_cli(*args, '--json')
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertTrue(self.requests.empty())

    def test_hf_token_input_is_not_reflected_in_backend_errors(self):
        type(self).response = (400, 'application/json', b'{"detail":"bad secret-hf-input"}', {})
        result = self.run_cli('settings', 'hf-token', 'set', '--stdin', '--json', input=b'secret-hf-input')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b'secret-hf-input', result.stderr)
        self.assertEqual(json.loads(result.stderr)['error']['code'], 'backend_error')

    def test_people_get_readable_text_and_scripts_get_json(self):
        r = self.run_cli('connection', 'show')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b'URL:', r.stdout)
        self.assertIn(b'Authenticated: no', r.stdout)
        self.assertFalse(r.stdout.startswith(b'{'))
        self.assertIn('url', json.loads(self.run_cli('connection', 'show', '--json').stdout))
        r = self.run_cli('status')
        self.assertEqual(r.stdout.strip(), f'VoiceStudio 0.5.7 is running at {self.env["VOICESTUDIO_URL"]}.'.encode())
        self.assertTrue(json.loads(self.run_cli('status', '--json').stdout)['running'])

    def test_settings_get_without_a_name_shows_every_setting(self):
        type(self).routes = {'/api/settings/' + route: (200, 'application/json', json.dumps({field: 'v-' + key}).encode(), {})
                             for key, (route, field) in __import__('voicestudio_cli.__main__', fromlist=['SETTINGS']).SETTINGS.items()}
        r = self.run_cli('settings', 'get', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        values = json.loads(r.stdout)
        self.assertEqual(values['compute-device'], 'v-compute-device')
        self.assertEqual(len(values), 6)
        self.assertIn(b'Pause fill:', self.run_cli('settings', 'get').stdout)

    def test_connection_set_is_local_and_respects_flag_precedence(self):
        r = self.run_cli('connection', 'set', 'https://saved.example', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        # Environment still wins over the saved URL.
        r = self.run_cli('connection', 'show', '--json')
        self.assertEqual(json.loads(r.stdout)['source'], 'environment')
        r = self.run_cli('connection', 'show', '--url', 'https://explicit.example', '--json')
        self.assertEqual(json.loads(r.stdout)['url'], 'https://explicit.example')
        self.assertTrue(self.requests.empty())

    def test_generic_upload_from_stdin_preserves_audio_bytes(self):
        result = self.run_cli('gallery', 'import', '-', '--stdin-filename', 'clip.wav', '--field', 'name=From stdin', input=b'RIFF-stdin-audio')
        self.assertEqual(result.returncode, 0, result.stderr)
        method, path, headers, body = self.requests.get(timeout=1)
        self.assertEqual((method, path), ('POST', '/gallery/upload'))
        self.assertIn(b'RIFF-stdin-audio', body)

    def test_metadata_output_flag_writes_the_actual_result(self):
        target = Path(self.temp.name) / 'status.json'
        result = self.run_cli('status', '-o', str(target), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['status'], 'ok')

    def test_metadata_output_to_stdout_is_not_silent(self):
        result = self.run_cli('version', '-o', '-', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version', json.loads(result.stdout))

    def test_text_output_does_not_add_a_second_newline(self):
        audio = Path(self.temp.name) / 'audio.wav'; audio.write_bytes(b'RIFF')
        type(self).response = (200, 'text/plain', 'café\n'.encode(), {})
        result = self.run_cli('transcribe', str(audio))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'café\n'.encode())

    def test_invalid_response_encoding_is_a_backend_error(self):
        audio = Path(self.temp.name) / 'audio.wav'; audio.write_bytes(b'RIFF')
        type(self).response = (200, 'text/plain', b'\xff', {})
        result = self.run_cli('transcribe', str(audio), '--json')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)['error']['code'], 'invalid_response')


if __name__ == '__main__':
    unittest.main()
