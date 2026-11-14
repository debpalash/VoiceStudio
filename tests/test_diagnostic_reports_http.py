"""Real local Supermetry receiver: stable replay and abortable consent withdrawal."""
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter,records


def wait_for_sender(transport):
    # Completion comes from the sender's finally/task_done path, not a delay.
    with transport._queue.all_tasks_done:
        assert transport._queue.all_tasks_done.wait_for(
            lambda:transport._queue.unfinished_tasks==0,timeout=5,
        )


def wait_for_stopped_sender(transport):
    transport._thread.join(timeout=5)
    assert not transport._thread.is_alive()
    assert transport._queue.unfinished_tasks==0


@pytest.fixture
def receiver(monkeypatch):
    from core import analytics
    state=SimpleNamespace(requests=[],status=201,changed=threading.Condition())
    def wait_for_requests(count):
        with state.changed:
            assert state.changed.wait_for(lambda:len(state.requests)>=count,timeout=5)
    state.wait_for_requests=wait_for_requests
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with state.changed:
                state.requests.append((self.path,dict(self.headers),body))
                state.changed.notify_all()
            self.send_response(state.status);self.end_headers()
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    destination={'site':'localtest123','host':f'http://127.0.0.1:{server.server_port}','appUrl':'https://app.test.invalid'}
    monkeypatch.setattr(analytics,'_destination',lambda:destination)
    yield state
    server.shutdown();server.server_close();thread.join(2)


def test_real_delivery_independent_consent_and_private_envelope(reporter,receiver):
    from core import prefs
    prefs.set_('analytics_enabled',True)
    assert not reporter.capture('generation_failed',error=ValueError('private recording'))
    reporter._tick();assert receiver.requests==[]
    reporter.consent(True)
    assert reporter.capture('generation_failed',stage='generation',error=ValueError('private recording'))
    saved=records(reporter)[0]
    receiver.wait_for_requests(1)
    wait_for_sender(reporter.transport)
    assert records(reporter)==[]
    path,headers,body=receiver.requests[0]
    assert path=='/api/public/v1/events'
    assert headers['Origin']=='https://app.test.invalid'
    assert not {'Cookie','Authorization','Referer'} & headers.keys()
    assert body['name']=='diagnostic_report' and body['url']=='https://app.test.invalid/diagnostics'
    assert body['id']==saved['id'] and body['ts']==saved['timestamp_ms']
    assert 'epoch' not in body and 'private' not in json.dumps(body)
    assert len(json.dumps(body).encode())<8192


def test_real_offline_retry_relaunch_preserves_report_identity(reporter,receiver):
    reports=importlib.import_module('core.diagnostic_reports')
    reporter.consent(True);receiver.status=503
    reporter.capture('download_failed',stage='download')
    saved=records(reporter)[0]
    receiver.wait_for_requests(3)
    wait_for_sender(reporter.transport)
    reporter._tick()
    assert records(reporter)==[saved]
    reporter.shutdown()
    receiver.status=201
    relaunched=reports.Reporter();relaunched.schema=reports._schema()
    try:
        relaunched._tick()
        receiver.wait_for_requests(4)
        wait_for_sender(relaunched.transport)
        assert records(relaunched)==[]
        assert {item[2]['id'] for item in receiver.requests}=={saved['id']}
        assert {item[2]['ts'] for item in receiver.requests}=={saved['timestamp_ms']}
    finally:relaunched.shutdown()


def test_real_retry_stops_and_queue_is_purged_on_withdrawal(reporter,receiver):
    reporter.consent(True);receiver.status=503
    reporter.capture('generation_failed');records(reporter)
    receiver.wait_for_requests(1)
    transport=reporter.transport
    reporter.consent(False)
    wait_for_stopped_sender(transport)
    assert len(receiver.requests)==1
    assert records(reporter)==[]
    assert not reporter.capture('backend_crash')


def test_withdrawal_while_connecting_prevents_request(reporter,receiver,monkeypatch):
    from core import analytics_transport
    entered=threading.Event();release=threading.Event();completed=threading.Event()
    connect=analytics_transport._HTTPConnection.connect
    def delayed(connection):
        entered.set();assert release.wait(2)
        try:return connect(connection)
        finally:completed.set()
    monkeypatch.setattr(analytics_transport._HTTPConnection,'connect',delayed)
    reporter.consent(True);reporter.capture('generation_failed');records(reporter)
    assert entered.wait(2)
    transport=reporter.transport
    try:reporter.consent(False)
    finally:release.set()
    assert completed.wait(2)
    wait_for_stopped_sender(transport)
    assert receiver.requests==[] and records(reporter)==[]
