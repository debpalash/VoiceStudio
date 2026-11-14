"""Real local Supermetry receiver: stable replay and abortable consent withdrawal."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from core import diagnostic_reports as reports
from test_diagnostic_reports import reporter,records


def wait_for(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def receiver(monkeypatch):
    from core import analytics
    state=SimpleNamespace(requests=[],status=201)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.requests.append((self.path,dict(self.headers),body))
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
    wait_for(lambda:len(receiver.requests)==1)
    wait_for(lambda:not records(reporter))
    path,headers,body=receiver.requests[0]
    assert path=='/api/public/v1/events'
    assert headers['Origin']=='https://app.test.invalid'
    assert not {'Cookie','Authorization','Referer'} & headers.keys()
    assert body['name']=='diagnostic_report' and body['url']=='https://app.test.invalid/diagnostics'
    assert body['id']==saved['id'] and body['ts']==saved['timestamp_ms']
    assert 'epoch' not in body and 'private' not in json.dumps(body)
    assert len(json.dumps(body).encode())<8192


def test_real_offline_retry_relaunch_preserves_report_identity(reporter,receiver):
    reporter.consent(True);receiver.status=503
    reporter.capture('download_failed',stage='download')
    saved=records(reporter)[0]
    wait_for(lambda:len(receiver.requests)==3)
    wait_for(lambda:not reporter.transport._queue.unfinished_tasks)
    reporter._tick()
    assert records(reporter)==[saved]
    reporter.shutdown()
    receiver.status=201
    relaunched=reports.Reporter();relaunched.schema=reports._schema()
    try:
        relaunched._tick()
        wait_for(lambda:len(receiver.requests)==4)
        wait_for(lambda:not records(relaunched))
        assert {item[2]['id'] for item in receiver.requests}=={saved['id']}
        assert {item[2]['ts'] for item in receiver.requests}=={saved['timestamp_ms']}
    finally:relaunched.shutdown()


def test_real_retry_stops_and_queue_is_purged_on_withdrawal(reporter,receiver):
    reporter.consent(True);receiver.status=503
    reporter.capture('generation_failed');records(reporter)
    wait_for(lambda:len(receiver.requests)==1)
    reporter.consent(False)
    time.sleep(.7)  # Beyond the first bounded retry backoff.
    assert len(receiver.requests)==1
    assert records(reporter)==[]
    assert not reporter.capture('backend_crash')


def test_withdrawal_while_connecting_prevents_request(reporter,receiver,monkeypatch):
    from core import analytics_transport
    entered=threading.Event();release=threading.Event()
    connect=analytics_transport._HTTPConnection.connect
    def delayed(connection):
        entered.set();assert release.wait(2)
        return connect(connection)
    monkeypatch.setattr(analytics_transport._HTTPConnection,'connect',delayed)
    reporter.consent(True);reporter.capture('generation_failed');records(reporter)
    assert entered.wait(2)
    reporter.consent(False);release.set()
    time.sleep(.2)
    assert receiver.requests==[] and records(reporter)==[]
