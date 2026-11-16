"""App-intake delivery uses existing bounded, consent-owned diagnostic evidence."""
import importlib
import json
from pathlib import Path

import pytest

KEY = 'smk_' + 'a' * 39
PROFILE = 'install_' + 'a' * 32


def test_configured_app_intake_takes_precedence_without_changing_analytics(monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    from core import analytics
    legacy = {'site': 'legacy123', 'host': 'https://legacy.invalid', 'appUrl': 'https://app.invalid'}
    monkeypatch.setattr(analytics, '_destination', lambda: dict(legacy))
    monkeypatch.setenv('SUPERMETRY_APP_ID', 'app123')
    monkeypatch.setenv('SUPERMETRY_INGEST_KEY', KEY)
    monkeypatch.setenv('SUPERMETRY_APP_HOST', 'https://collector.invalid')
    destination = reports._diagnostic_destination()
    assert destination.get('app_id') == 'app123'
    assert destination['host'] == 'https://collector.invalid'
    assert analytics._destination() == legacy


def test_common_mapping_golden():
    module = importlib.import_module('core.diagnostic_app_transport')
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'diagnostic-app-events.json').read_text(encoding='utf-8'))
    for case in fixture['cases']:
        expected = {**case['expected'], 'id': 'b' * 32, 'ts': 1000, 'install_id': PROFILE, 'runtime': 'python'}
        actual = module.diagnostic_app_event(case['props'], event_id='b' * 32, timestamp_ms=1000, profile_id=PROFILE)
        assert actual == expected, case['name']
        assert list(actual['tags']) == list(case['expected']['tags']), case['name']


@pytest.fixture(autouse=True)
def isolated_app_config(monkeypatch, tmp_path):
    module = importlib.import_module('core.diagnostic_app_transport')
    for name in ('SUPERMETRY_APP_ID', 'SUPERMETRY_INGEST_KEY', 'SUPERMETRY_APP_HOST'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, '_GENERATED_CONFIG', tmp_path / 'generated.json')
    monkeypatch.setattr(module, '_ANALYTICS_CONFIG', tmp_path / 'analytics.json')
    module._ANALYTICS_CONFIG.write_text(json.dumps({'host': 'https://fallback.invalid'}), encoding='utf-8')
    return module


@pytest.mark.parametrize('field,env,value', [
    ('app_id', 'SUPERMETRY_APP_ID', ''), ('app_id', 'SUPERMETRY_APP_ID', 'BAD'),
    ('ingest_key', 'SUPERMETRY_INGEST_KEY', ''), ('ingest_key', 'SUPERMETRY_INGEST_KEY', 'invalid'),
])
def test_invalid_explicit_override_cannot_inherit_generated_credentials(isolated_app_config, monkeypatch, field, env, value):
    module = isolated_app_config
    module._GENERATED_CONFIG.write_text(json.dumps({'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://generated.invalid'}), encoding='utf-8')
    monkeypatch.setenv(env, value)
    assert module.app_destination() == {}


def test_host_override_falls_back_to_analytics_host_not_generated_host(isolated_app_config, monkeypatch):
    module = isolated_app_config
    module._GENERATED_CONFIG.write_text(json.dumps({'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://generated.invalid', 'diagnostics': {'sessionPings': True}}), encoding='utf-8')
    monkeypatch.setenv('SUPERMETRY_APP_HOST', 'http://not-loopback.invalid')
    assert module.app_destination()['host'] == 'https://fallback.invalid'
    assert module.app_destination()['sessionPings'] is True


@pytest.mark.parametrize('host,allowed', [('https://collector.invalid', True), ('http://127.0.0.1:9000', True), ('http://[::1]:9000', True), ('http://localhost:9000', True), ('http://localhost.evil.invalid', False), ('https://user:password@collector.invalid', False), ('https://collector.invalid/path', False)])
def test_app_host_preserves_diagnostic_tls_policy(isolated_app_config, host, allowed):
    assert bool(isolated_app_config._host(host)) == allowed


def test_batching_respects_count_and_encoded_utf8_bytes(isolated_app_config):
    module = isolated_app_config
    destination = {'app_id': 'app123'}
    events = [{'id': f'{index:032x}', 'frames': 'é' * 900} for index in range(45)]
    batches = list(module.split_batches(destination, events))
    assert sum(batches, []) == events
    assert all(1 <= len(batch) <= 20 and len(module._body(destination, batch)) <= 16384 for batch in batches)
    assert [len(b) for b in module.split_batches(destination, [{'id': f'{i:032x}'} for i in range(45)])] == [20, 20, 5]
    with pytest.raises(ValueError):
        list(module.split_batches(destination, [{'frames': 'é' * 16384}]))


@pytest.mark.parametrize('body', [None, {}, {'accepted': True, 'dropped': []}, {'accepted': 0, 'dropped': []},
    {'accepted': 1, 'dropped': [{'index': 2, 'reason': 'duplicate'}]},
    {'accepted': 0, 'dropped': [{'index': 0, 'reason': 'duplicate'}, {'index': 0, 'reason': 'invalid'}]},
    {'accepted': 0, 'dropped': [{'index': 0, 'reason': 'unknown'}]}])
def test_ambiguous_202_does_not_acknowledge_unspecified_records(isolated_app_config, body):
    assert isolated_app_config.app_results(202, body, 1) == [(None, None)]


def test_mixed_202_uses_exact_indices_and_retains_only_rate_limited(isolated_app_config):
    assert isolated_app_config.app_results(202, {'accepted': 1, 'dropped': [
        {'index': 3, 'reason': 'duplicate'}, {'index': 1, 'reason': 'rate_limited'},
        {'index': 4, 'reason': 'invalid'}, {'index': 2, 'reason': 'too_old'},
    ]}, 5) == [(202, 'accepted'), (429, 'rate_limited'), (202, 'too_old'), (202, 'duplicate'), (202, 'invalid')]


def parked_transport(module, monkeypatch, should_send=lambda: True):
    monkeypatch.setattr(module.threading.Thread, 'start', lambda _: None)
    monkeypatch.setattr(module.time, 'time', lambda: 1.0)
    results = []
    transport = module.AppDiagnosticTransport({'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://collector.invalid'}, should_send, on_result=lambda *result: results.append(result))
    return transport, results


def test_exact_app_wire_headers_and_no_content_fields(isolated_app_config, monkeypatch):
    module = isolated_app_config
    transport, _ = parked_transport(module, monkeypatch)
    requests = []
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, body, headers): requests.append((method, path, json.loads(body), headers))
        def getresponse(self): return type('Response', (), {'status': 202, 'read': lambda self, n: b'{"accepted":1,"dropped":[]}'})()
        def close(self): pass
    monkeypatch.setattr(module, '_HTTPSConnection', Connection)
    assert transport.capture('diagnostic_report', {'category': 'engine_failed', 'message': 'PRIVATE_CONTENT'}, PROFILE, event_id='b'*32, timestamp_ms=1000)
    event = transport._queue.get_nowait()
    assert transport._send([event]) == (202, {'accepted': 1, 'dropped': []})
    method, path, body, headers = requests[0]
    assert (method, path) == ('POST', '/api/public/v1/app-events')
    assert body == {'site': 'app123', 'events': [event]}
    assert headers['X-Supermetry-Key'] == KEY and 'Origin' not in headers
    assert headers['User-Agent'].startswith('VoiceStudio/')
    assert 'PRIVATE_CONTENT' not in json.dumps(body) and KEY not in json.dumps(body)


@pytest.mark.parametrize('statuses', [[429, 202], [503, 202], [None, 202]])
def test_retries_preserve_batch_ids_and_timestamps(isolated_app_config, monkeypatch, statuses):
    module = isolated_app_config
    transport, results = parked_transport(module, monkeypatch)
    monkeypatch.setattr(transport._stopped, 'wait', lambda _: False)
    calls = []
    remaining = iter(statuses)
    def send(events):
        calls.append(json.loads(json.dumps(events)))
        status = next(remaining)
        if status is None: raise OSError('synthetic network failure')
        return status, {'accepted': 1, 'dropped': []}
    monkeypatch.setattr(transport, '_send', send)
    event = module.diagnostic_app_event({'category': 'engine_failed'}, event_id='b'*32, timestamp_ms=1000, profile_id=PROFILE)
    transport._deliver([event])
    assert calls == [[event], [event]]
    assert results == [('b'*32, 202, 'accepted')]


def test_401_pauses_all_later_sends_and_preserves_record_outcomes(isolated_app_config, monkeypatch):
    transport, results = parked_transport(isolated_app_config, monkeypatch)
    calls = []
    monkeypatch.setattr(transport, '_send', lambda events: (calls.append(events) or (401, None)))
    first = {'id': 'b'*32}; second = {'id': 'c'*32}
    transport._deliver([first]); transport._deliver([second])
    assert transport.paused and calls == [[first]]
    assert results == [('b'*32, 401, None), ('c'*32, None, None)]


def test_withdrawal_refuses_queued_transmission(isolated_app_config, monkeypatch):
    allowed = [True]
    transport, results = parked_transport(isolated_app_config, monkeypatch, lambda: allowed[0])
    assert transport.capture('diagnostic_report', {}, PROFILE, event_id='b'*32, timestamp_ms=1000)
    event = transport._queue.get_nowait()
    monkeypatch.setattr(transport, '_send', lambda _: pytest.fail('sent after withdrawal'))
    allowed[0] = False
    transport._deliver([event])
    assert results == [('b'*32, None, None)]
    transport.shutdown()
    assert not transport.capture('diagnostic_report', {}, PROFILE, event_id='c'*32, timestamp_ms=1000)


from test_diagnostic_reports import reporter  # noqa: E402,F401


@pytest.mark.parametrize('session_pings', [False, True])
def test_reporter_sessions_are_opt_in_current_run_only_and_retired_on_withdrawal(reporter, monkeypatch, isolated_app_config, session_pings):
    reports = importlib.import_module('core.diagnostic_reports')
    module = isolated_app_config
    destination = {'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://collector.invalid', 'sessionPings': session_pings}
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: destination)
    sent, sessions = [], []
    class Transport:
        def __init__(self, destination, allowed, **kwargs): self.destination = destination; self.allowed = allowed
        def capture(self, *args, **kwargs): sent.append(kwargs); return True
        def capture_session(self, *args): sessions.append(args); return True
        def shutdown(self): pass
    monkeypatch.setattr(module, 'AppDiagnosticTransport', Transport)
    reporter.consent(True)
    # A report restored from an earlier process must never acquire this run's ID.
    old = {'version': 1, 'id': 'e'*32, 'timestamp_ms': int(reports.time.time()*1000), 'epoch': reporter.epoch, 'props': reports.sanitize_props({'category': 'engine_failed'})}
    reporter.directory.mkdir(parents=True, exist_ok=True)
    (reporter.directory / (old['id']+'.json')).write_text(json.dumps(old), encoding='utf-8')
    assert reporter.capture('download_failed')
    reporter._tick(); reporter._tick()
    assert len(sessions) == int(session_pings)
    by_id = {entry['event_id']: entry for entry in sent}
    assert 'session_id' not in by_id[old['id']]
    fresh = next(entry for identity, entry in by_id.items() if identity != old['id'])
    assert ('session_id' in fresh) == session_pings
    for path in reporter.directory.glob('*.json'):
        assert 'session_id' not in path.read_text(encoding='utf-8')
    reporter.consent(False); reporter.consent(True)
    assert reporter.capture('operation_failed')
    reporter._tick()
    assert len(sessions) == int(session_pings) and 'session_id' not in sent[-1]


def test_app_401_is_not_a_terminal_outbox_ack_even_after_regrant(reporter, monkeypatch, isolated_app_config):
    reports = importlib.import_module('core.diagnostic_reports')
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: {'app_id': 'app123'})
    reporter.consent(True)
    reporter.capture('engine_failed'); reporter._persist_intake()
    row, = reporter._files()
    reporter._app_delivery_result(reporter.epoch, row['id'], 401, None)
    reporter._tick()
    assert reporter.app_delivery_paused and (reporter.directory / (row['id']+'.json')).exists()
    reporter.consent(False); reporter.consent(True); reporter._tick()
    assert reporter.app_delivery_paused and reporter.transport is None
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: {'site': 'legacy123', 'host': 'https://legacy.invalid', 'appUrl': 'https://app.invalid'})
    reporter._tick()
    assert reporter.transport is None


@pytest.mark.parametrize('status', [302, 403, 404, 422])
def test_undocumented_status_never_deletes_a_report(isolated_app_config, status):
    assert isolated_app_config.app_results(status, {}, 2) == [(None, 'unexpected_status')] * 2


def test_expiry_is_rechecked_before_retry_with_stable_local_outcome(isolated_app_config, monkeypatch):
    module = isolated_app_config
    transport, results = parked_transport(module, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    transport.ttl_ms = 1000
    calls = []
    def send(events):
        calls.append(events)
        clock[0] += 2
        return 503, None
    monkeypatch.setattr(transport, '_send', send)
    monkeypatch.setattr(transport._stopped, 'wait', lambda _: False)
    event = {'id': 'b'*32, 'ts': 1000000}
    transport._deliver([event])
    assert calls == [[event]]
    assert results == [('b'*32, 202, 'too_old')]


def test_session_transport_requires_explicit_flag(isolated_app_config, monkeypatch):
    transport, _ = parked_transport(isolated_app_config, monkeypatch)
    assert not transport.capture_session(PROFILE, 'b'*32, 'c'*32, 1000, '0.5.9-beta.3')
    assert transport._queue.empty()


@pytest.fixture
def real_http_collector():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    state = {'requests': [], 'reply': lambda body: (202, {'accepted': len(body['events']), 'dropped': []})}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            body = json.loads(raw)
            state['requests'].append((self.path, dict(self.headers), body, raw))
            status, payload = state['reply'](body)
            encoded = json.dumps(payload).encode()
            self.send_response(status); self.send_header('Content-Length', str(len(encoded))); self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state['host'] = 'http://127.0.0.1:' + str(server.server_port)
    try:
        yield state
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def real_transport(module, collector, *, count, allowed=lambda: True):
    import threading
    results = []
    done = threading.Event()
    def result(*values):
        results.append(values)
        if len(results) >= count: done.set()
    transport = module.AppDiagnosticTransport({'app_id': 'app123', 'ingest_key': KEY, 'host': collector['host']}, allowed, on_result=result)
    return transport, results, done


def enqueue_now(transport, identity):
    import time
    return transport.capture('diagnostic_report', {'category': 'engine_failed'}, PROFILE, event_id=f'{identity:032x}', timestamp_ms=int(time.time()*1000))


def test_real_http_mixed_202_batch_has_exact_wire_and_outcomes(isolated_app_config, real_http_collector):
    module = isolated_app_config
    collector = real_http_collector
    reasons = {1: 'rate_limited', 2: 'too_old', 3: 'duplicate', 4: 'invalid'}
    def reply(body):
        dropped = [{'index': index, 'reason': reasons[int(event['id'], 16)]} for index, event in enumerate(body['events']) if int(event['id'], 16) in reasons]
        return 202, {'accepted': len(body['events'])-len(dropped), 'dropped': dropped, 'warnings': [{'index': 0, 'reason': 'tag_not_allowed', 'tag': 'category'}]}
    collector['reply'] = reply
    transport, results, done = real_transport(module, collector, count=5)
    try:
        for identity in range(5): assert enqueue_now(transport, identity)
        assert done.wait(5)
        assert {identity: (status, reason) for identity, status, reason in results} == {
            f'{i:032x}': (429 if i == 1 else 202, reasons.get(i, 'accepted')) for i in range(5)}
        assert sum(len(request[2]['events']) for request in collector['requests']) == 5
        for path, headers, body, raw in collector['requests']:
            assert path == '/api/public/v1/app-events' and headers['X-Supermetry-Key'] == KEY
            assert 'Origin' not in headers and len(raw) <= 16384 and len(body['events']) <= 20
            assert body['site'] == 'app123' and KEY not in raw.decode()
    finally:
        transport.shutdown()


@pytest.mark.parametrize('first_status', [429, 503])
def test_real_http_retry_keeps_identical_ids_and_failure_times(isolated_app_config, real_http_collector, first_status):
    collector = real_http_collector
    def reply(body):
        return (first_status, {}) if len(collector['requests']) == 1 else (202, {'accepted': len(body['events']), 'dropped': []})
    collector['reply'] = reply
    transport, results, done = real_transport(isolated_app_config, collector, count=1)
    try:
        assert enqueue_now(transport, 1) and done.wait(5)
        assert results == [('0'*31+'1', 202, 'accepted')]
        assert len(collector['requests']) == 2
        assert collector['requests'][0][3] == collector['requests'][1][3]
    finally:
        transport.shutdown()


def test_real_http_401_pauses_later_events_for_the_run(isolated_app_config, real_http_collector):
    collector = real_http_collector
    collector['reply'] = lambda body: (401, {})
    transport, results, done = real_transport(isolated_app_config, collector, count=1)
    try:
        assert enqueue_now(transport, 1) and done.wait(5)
        assert results == [('0'*31+'1', 401, None)]
        assert transport.paused and not enqueue_now(transport, 2)
        assert len(collector['requests']) == 1
    finally:
        transport.shutdown()


def test_real_http_withdrawal_discards_queued_sends_behind_inflight(isolated_app_config, real_http_collector):
    import threading
    entered = threading.Event(); release = threading.Event()
    allowed = [True]
    def reply(body):
        entered.set(); assert release.wait(5)
        return 202, {'accepted': len(body['events']), 'dropped': []}
    real_http_collector['reply'] = reply
    transport, _, _ = real_transport(isolated_app_config, real_http_collector, count=1, allowed=lambda: allowed[0])
    try:
        assert enqueue_now(transport, 1) and entered.wait(5)
        assert enqueue_now(transport, 2)
        allowed[0] = False; transport.shutdown(); release.set()
        transport._thread.join(5)
        assert not transport._thread.is_alive()
        assert len(real_http_collector['requests']) == 1
        assert not enqueue_now(transport, 3)
    finally:
        release.set(); transport.shutdown()


def test_unexpected_delivery_failure_releases_slots_and_worker_continues(isolated_app_config, monkeypatch):
    import threading
    module = isolated_app_config
    results = []; done = threading.Event(); first = threading.Event()
    def result(*values):
        results.append(values)
        first.set()
        if len(results) == 2: done.set()
    transport = module.AppDiagnosticTransport({'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://collector.invalid'}, lambda: True, on_result=result)
    original = transport._deliver
    calls = [0]
    def deliver(batch):
        calls[0] += 1
        if calls[0] == 1: raise RuntimeError('synthetic worker bookkeeping fault')
        original(batch)
    monkeypatch.setattr(transport, '_deliver', deliver)
    monkeypatch.setattr(transport, '_send', lambda batch: (202, {'accepted': len(batch), 'dropped': []}))
    try:
        assert enqueue_now(transport, 1)
        # First failure's callback proves its slot was reached before new work.
        assert first.wait(5)
        assert enqueue_now(transport, 2) and done.wait(5)
        assert results == [('0'*31+'1', None, 'invalid_response'), ('0'*31+'2', 202, 'accepted')]
    finally:
        transport.shutdown(); transport._thread.join(5)
    assert transport._queue.unfinished_tasks == 0


def test_warning_hook_failure_does_not_kill_worker(isolated_app_config, monkeypatch):
    import threading
    module = isolated_app_config
    done = threading.Event(); warned = threading.Event(); results = []
    def warning(*args): warned.set(); raise RuntimeError('synthetic local warning hook')
    def result(*args):
        results.append(args)
        if len(results) == 2: done.set()
    transport = module.AppDiagnosticTransport({'app_id': 'app123', 'ingest_key': KEY, 'host': 'https://collector.invalid'}, lambda: True, on_result=result, on_warning=warning)
    monkeypatch.setattr(transport, '_send', lambda batch: (202, {'accepted': len(batch), 'dropped': [], 'warnings': [{'index': 0, 'reason': 'tag_not_allowed', 'tag': 'category'}]}))
    try:
        assert enqueue_now(transport, 1) and warned.wait(5)
        assert enqueue_now(transport, 2) and done.wait(5)
    finally:
        transport.shutdown(); transport._thread.join(5)
    assert len(results) == 2 and transport._queue.unfinished_tasks == 0


def test_legacy_fallback_retains_exact_request_shape(isolated_app_config, monkeypatch):
    from core import analytics, analytics_transport
    reports = importlib.import_module('core.diagnostic_reports')
    legacy = {'site': 'legacy123', 'host': 'https://legacy.invalid', 'appUrl': 'https://desktop.invalid'}
    monkeypatch.setattr(analytics, '_destination', lambda: legacy)
    assert reports._diagnostic_destination() == legacy
    monkeypatch.setattr(analytics_transport.threading.Thread, 'start', lambda _: None)
    requests = []
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, body, headers): requests.append((method, path, json.loads(body), headers))
        def getresponse(self): return type('Response', (), {'status': 202})()
        def close(self): pass
    monkeypatch.setattr(analytics_transport, '_HTTPSConnection', Connection)
    transport = analytics_transport.SupermetryTransport(legacy, lambda: True)
    props = {'category': 'operation_failed'}
    assert transport.capture('diagnostic_report', props, PROFILE, path='/diagnostics', event_id='b'*32, timestamp_ms=1000)
    transport._send(transport._queue.get_nowait())
    method, path, body, headers = requests[0]
    assert (method, path) == ('POST', '/api/public/v1/events')
    assert body == {'v': 1, 'id': 'b'*32, 'ts': 1000, 'site': 'legacy123', 'name': 'diagnostic_report', 'url': 'https://desktop.invalid/diagnostics', 'props': props, 'profile_id': PROFILE}
    assert headers == {'Content-Type': 'application/json', 'User-Agent': analytics_transport.user_agent(), 'Origin': 'https://desktop.invalid'}


def test_mapping_enforces_server_field_and_utf8_tag_bounds(isolated_app_config):
    props = {'category': 'engine_failed', 'app_version': 'x'*65, 'build_commit': 'x'*65,
             'error_type': 'x'*65, 'device_family': 'x'*33, 'reason': 'x'*33,
             'signal': 'x'*33, 'engine_id': 'é'*100, 'stage': 'é'*101,
             **{f'arbitrary_{i}': 'ignored' for i in range(30)}}
    event = isolated_app_config.diagnostic_app_event(props, event_id='b'*32, timestamp_ms=1000, profile_id=PROFILE)
    assert not {'release', 'build', 'error_type', 'device_class', 'exit_reason', 'signal'} & event.keys()
    assert event['tags'] == {'category': 'engine_failed', 'engine_id': 'é'*100}
    assert len(event['tags']) <= 20
    assert isolated_app_config.app_results(400, None, 20) == [(400, 'invalid')]*20


def test_session_flag_does_not_add_legacy_requests(reporter, isolated_app_config, monkeypatch):
    from core import analytics, analytics_transport
    module = isolated_app_config
    module._GENERATED_CONFIG.write_text(json.dumps({'diagnostics': {'sessionPings': True}}), encoding='utf-8')
    monkeypatch.setattr(analytics, '_destination', lambda: {'site': 'legacy123', 'host': 'https://legacy.invalid', 'appUrl': 'https://app.invalid'})
    calls = []
    class Legacy:
        def __init__(self, *args, **kwargs): pass
        def capture(self, *args, **kwargs): calls.append((args, kwargs)); return True
        def shutdown(self): pass
    monkeypatch.setattr(analytics_transport, 'SupermetryTransport', Legacy)
    reporter.consent(True); reporter.capture('engine_failed'); reporter._tick()
    assert len(calls) == 1 and calls[0][0][0] == 'diagnostic_report'
    assert 'session_id' not in calls[0][1] and reporter.run_session_id is None


def test_session_retry_keeps_one_id_and_original_grant(reporter, isolated_app_config, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    module = isolated_app_config
    clock = [100.0]
    monkeypatch.setattr(reports.time, 'monotonic', lambda: clock[0])
    destination = {'app_id': 'app123', 'sessionPings': True}
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: destination)
    sent = []
    class Transport:
        def __init__(self, destination, allowed, **kwargs): self.destination = destination; self.callback = kwargs['on_result']
        def capture(self, *args, **kwargs): return True
        def capture_session(self, *args): sent.append(args); return True
        def shutdown(self): pass
    monkeypatch.setattr(module, 'AppDiagnosticTransport', Transport)
    reporter.consent(True); reporter._tick()
    first, = sent
    reporter.transport.callback(first[2], 429, 'rate_limited')
    clock[0] = 159; reporter._tick(); assert sent == [first]
    clock[0] = 160; reporter._tick(); assert sent == [first, first]
    reporter.consent(False); reporter.consent(True); clock[0] = 220; reporter._tick()
    assert sent == [first, first]


def test_current_run_id_tracking_follows_durable_capacity(reporter):
    reporter.consent(True)
    for _ in range(3):
        assert reporter.capture('engine_failed')
        reporter._tick()
        row, = reporter._files()
        assert reporter.current_run_records == {row['id']}
        (reporter.directory / (row['id']+'.json')).unlink()
        reporter._files()
        assert reporter.current_run_records == set()


def test_environment_app_credentials_trim_like_generated_config(isolated_app_config, monkeypatch):
    monkeypatch.setenv('SUPERMETRY_APP_ID', ' app123 ')
    monkeypatch.setenv('SUPERMETRY_INGEST_KEY', ' '+KEY+'\n')
    destination = isolated_app_config.app_destination()
    assert destination['app_id'] == 'app123' and destination['ingest_key'] == KEY


def test_unacknowledged_session_retries_same_grant_after_unread_consent(reporter, isolated_app_config, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    clock = [100.0]
    monkeypatch.setattr(reports.time, 'monotonic', lambda: clock[0])
    destination = {'app_id': 'app123', 'sessionPings': True}
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: destination)
    sent = []
    class Transport:
        def __init__(self, destination, allowed, **kwargs): self.destination = destination
        def capture(self, *args, **kwargs): return True
        def capture_session(self, *args): sent.append(args); return True
        def shutdown(self): pass
    monkeypatch.setattr(isolated_app_config, 'AppDiagnosticTransport', Transport)
    reporter.consent(True); reporter._tick()
    first, = sent
    read = reporter._consent
    def unread(): raise OSError('synthetic unknown grant')
    monkeypatch.setattr(reporter, '_consent', unread)
    reporter._tick()
    assert not reporter.enabled and not reporter.run_session_submitted
    monkeypatch.setattr(reporter, '_consent', read)
    clock[0] = 159; reporter._tick(); assert sent == [first]
    clock[0] = 160; reporter._tick(); assert sent == [first, first]


def test_fast_session_result_cannot_be_overwritten_by_enqueue_return(reporter, isolated_app_config, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    destination = {'app_id': 'app123', 'sessionPings': True}
    monkeypatch.setattr(reports, '_diagnostic_destination', lambda: destination)
    class Transport:
        def __init__(self, destination, allowed, **kwargs): self.destination = destination; self.callback = kwargs['on_result']
        def capture(self, *args, **kwargs): return True
        def capture_session(self, *args):
            # A transport thread may finish before its caller resumes.
            self.callback(args[2], 429, 'rate_limited')
            return True
        def shutdown(self): pass
    monkeypatch.setattr(isolated_app_config, 'AppDiagnosticTransport', Transport)
    reporter.consent(True); reporter._tick()
    assert not reporter.run_session_submitted and reporter.run_session_retry_at > 0
