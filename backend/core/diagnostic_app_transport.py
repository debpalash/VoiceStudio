"""Diagnostics-only app intake. Product analytics keeps its existing transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import queue
import re
import threading
import time
from urllib.parse import urlsplit

from core.analytics_transport import _HTTPConnection, _HTTPSConnection, valid_url

_ROOT = Path(__file__).resolve().parents[2]
_GENERATED_CONFIG = _ROOT / 'config' / 'supermetry-app.generated.json'
_ANALYTICS_CONFIG = _ROOT / 'config' / 'analytics.json'
_TAGS = ('category', 'source', 'stage', 'error_code', 'engine_id', 'uptime_bucket', 'elapsed_bucket', 'memory_bucket', 'disk_bucket', 'schema_version')
_DROPS = {'too_old', 'invalid', 'duplicate', 'rate_limited'}
_MAX_BODY = 16384
_MAX_EVENTS = 20


def _config(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def _host(value):
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip('/')
    if not valid_url(value, origin=True):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme == 'https' or parsed.hostname in {'localhost', '127.0.0.1', '::1'} else None


def app_destination():
    """Explicit invalid overrides are absent, never an older generated grant."""
    config = _config(_GENERATED_CONFIG)
    app_id = os.environ.get('SUPERMETRY_APP_ID', config.get('app_id'))
    key = os.environ.get('SUPERMETRY_INGEST_KEY', config.get('ingest_key'))
    app_id = app_id.strip() if isinstance(app_id, str) else app_id
    key = key.strip() if isinstance(key, str) else key
    if not isinstance(app_id, str) or not re.fullmatch(r'[a-z0-9]{6,32}', app_id):
        return {}
    if not isinstance(key, str) or not re.fullmatch(r'smk_[a-z2-7]{39}', key):
        return {}
    host = _host(os.environ.get('SUPERMETRY_APP_HOST', config.get('host')))
    if host is None:
        host = _host(_config(_ANALYTICS_CONFIG).get('host'))
    if host is None:
        return {}
    diagnostics = config.get('diagnostics')
    return {'app_id': app_id, 'ingest_key': key, 'host': host,
            'sessionPings': isinstance(diagnostics, dict) and diagnostics.get('sessionPings') is True}


def normalized_platform(value):
    value = value.lower() if isinstance(value, str) else ''
    return {'darwin': 'macos', 'mac': 'macos', 'osx': 'macos', 'win32': 'windows', 'win': 'windows'}.get(value, value if value in {'windows', 'macos', 'linux', 'ios', 'android', 'web'} else 'other')


def normalized_arch(value):
    value = value.lower() if isinstance(value, str) else ''
    return {'x64': 'x64', 'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64', 'ia32': 'x86', 'x86': 'x86', 'arm': 'arm', 'armv7l': 'arm'}.get(value, 'other')


def diagnostic_app_event(props, *, event_id, timestamp_ms, profile_id, session_id=None):
    """Map already sanitized closed diagnostic fields once; no messages or URLs."""
    category = props.get('category')
    crash = category in {'app_crash', 'backend_crash', 'renderer_crash'}
    kind = ('crash' if crash else category if category in {'install_failed', 'install_stalled', 'incompatibility'}
            else 'operation_failed' if category in {'generation_failed', 'model_load_failed', 'engine_failed', 'download_failed', 'operation_failed'} else 'error')
    event = {'id': event_id, 'ts': timestamp_ms, 'install_id': profile_id, 'runtime': 'python',
             'kind': kind, 'level': 'fatal' if crash else 'error', 'handled': not crash}
    for source, target, limit in (('app_version', 'release', 64), ('build_commit', 'build', 64),
                                  ('device_family', 'device_class', 32), ('reason', 'exit_reason', 32),
                                  ('error_type', 'error_type', 64), ('signal', 'signal', 32),
                                  ('frames', 'frames', 1800), ('fingerprint', 'fingerprint', 64)):
        value = props.get(source)
        if isinstance(value, str) and value and len(value.encode('utf-8')) <= limit:
            event[target] = value
    event['platform'] = normalized_platform(props.get('platform'))
    event['arch'] = normalized_arch(props.get('arch'))
    for name in ('exit_code', 'occurrences'):
        if type(props.get(name)) is int:
            event[name] = props[name]
    event['tags'] = {name: props[name] for name in _TAGS if name in props and
                     (type(props[name]) in (int, bool) or isinstance(props[name], str) and props[name] and len(props[name].encode('utf-8')) <= 200)}
    if session_id is not None:
        event['session_id'] = session_id
    return event


def _body(destination, events):
    return json.dumps({'site': destination['app_id'], 'events': events}, allow_nan=False, separators=(',', ':')).encode('utf-8')


def split_batches(destination, events):
    """Never construct an oversized wire request, including multibyte values."""
    batch = []
    for event in events:
        if len(_body(destination, [event])) > _MAX_BODY:
            raise ValueError('Diagnostic event exceeds app intake limit')
        if batch and (len(batch) == _MAX_EVENTS or len(_body(destination, [*batch, event])) > _MAX_BODY):
            yield batch
            batch = []
        batch.append(event)
    if batch:
        yield batch


def app_results(status, data, count):
    """Return exact per-index outcomes; ambiguity cannot acknowledge evidence."""
    if status != 202:
        if status is not None and status < 500 and status not in {400,401,429}:
            return [(None, 'unexpected_status')] * count
        return [(status, 'invalid' if status == 400 else 'rate_limited' if status == 429 else None)] * count
    if not isinstance(data, dict) or type(data.get('accepted')) is not int or not isinstance(data.get('dropped'), list):
        return [(None, None)] * count
    dropped = {}
    for entry in data['dropped']:
        if not isinstance(entry, dict) or type(entry.get('index')) is not int or not 0 <= entry['index'] < count or entry['index'] in dropped or entry.get('reason') not in _DROPS:
            return [(None, None)] * count
        dropped[entry['index']] = entry['reason']
    if data['accepted'] != count - len(dropped):
        return [(None, None)] * count
    return [(429, 'rate_limited') if dropped.get(i) == 'rate_limited' else (202, dropped.get(i, 'accepted')) for i in range(count)]


class AppDiagnosticTransport:
    """Bounded batches and IDs; 401 disables this transport for the entire run."""
    def __init__(self, destination, should_send, *, on_result=None, on_warning=None, ttl_ms=604800000):
        self.destination = destination
        self.should_send = should_send
        self.on_result = on_result
        self.on_warning = on_warning
        self.ttl_ms = min(ttl_ms,604800000) if type(ttl_ms) is int and ttl_ms > 0 else 604800000
        self._queue = queue.Queue(maxsize=64)
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._connection = None
        self._ids = set()
        self.paused = False
        self._thread = threading.Thread(target=self._run, name='diagnostic-app-intake', daemon=True)
        self._thread.start()

    def _enqueue(self, event):
        if len(_body(self.destination, [event])) > _MAX_BODY:
            return False
        with self._lock:
            if self._stopped.is_set() or self.paused or not self.should_send() or len(self._ids) >= 64 or event['id'] in self._ids:
                return False
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                return False
            self._ids.add(event['id'])
            return True

    def capture(self, event, properties, profile_id=None, path='/diagnostics', *, event_id=None, timestamp_ms=None, session_id=None):
        if (event != 'diagnostic_report' or not isinstance(properties, dict)
                or not isinstance(event_id, str) or not re.fullmatch(r'[a-f0-9]{32}', event_id)
                or type(timestamp_ms) is not int or not 0 < timestamp_ms < 2**53
                or not isinstance(profile_id, str) or not re.fullmatch(r'install_[a-f0-9]{32}', profile_id)
                or session_id is not None and (not isinstance(session_id, str) or not re.fullmatch(r'[a-f0-9]{32}', session_id))):
            return False
        return self._enqueue(diagnostic_app_event(properties, event_id=event_id, timestamp_ms=timestamp_ms, profile_id=profile_id, session_id=session_id))

    def capture_session(self, profile_id, session_id, event_id, timestamp_ms, app_version):
        if (self.destination.get('sessionPings') is not True or not isinstance(profile_id,str) or not re.fullmatch(r'install_[a-f0-9]{32}',profile_id)
                or any(not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value) for value in (session_id,event_id))
                or type(timestamp_ms) is not int or not 0 < timestamp_ms < 2**53
                or not isinstance(app_version,str) or not 0 < len(app_version.encode('utf-8')) <= 64):
            return False
        event = {'kind': 'session', 'id': event_id, 'ts': timestamp_ms, 'session_id': session_id, 'install_id': profile_id,
                 'release': app_version, 'platform': normalized_platform(platform.system()), 'arch': normalized_arch(platform.machine()), 'runtime': 'python'}
        return self._enqueue(event)

    def _send(self, events):
        parsed = urlsplit(self.destination['host'])
        factory = _HTTPSConnection if parsed.scheme == 'https' else _HTTPConnection
        connection = factory(parsed.hostname, parsed.port, timeout=2, cancelled=self._stopped, allowed=self.should_send)
        with self._lock:
            if self._stopped.is_set() or self.paused or not self.should_send():
                return None, None
            self._connection = connection
        try:
            from core.version import APP_VERSION
            connection.request('POST', '/api/public/v1/app-events', _body(self.destination, events), {
                'Content-Type': 'application/json', 'X-Supermetry-Key': self.destination['ingest_key'],
                'User-Agent': f'VoiceStudio/{APP_VERSION} ({normalized_platform(platform.system())}; {normalized_arch(platform.machine())})',
            })
            response = connection.getresponse()
            raw = response.read(_MAX_BODY + 1)
            try:
                data = json.loads(raw) if len(raw) <= _MAX_BODY else None
            except (ValueError, UnicodeError, RecursionError):
                data = None
            return response.status, data
        finally:
            connection.close()
            with self._lock:
                self._connection = None

    def _finish(self, event, outcome, reason):
        try:
            if self.on_result:
                self.on_result(event['id'], outcome, reason)
        except Exception:
            pass  # Optional bookkeeping must not strand bounded slots.
        finally:
            with self._lock:
                self._ids.discard(event['id'])

    def _deliver(self, batch):
        status, data = None, None
        for attempt in range(3):
            active = []
            now = int(time.time()*1000)
            for event in batch:
                if type(event.get('ts')) is int and event['ts'] < now-self.ttl_ms:
                    self._finish(event,202,'too_old')
                else:
                    active.append(event)
            batch = active
            if not batch:
                return
            if self._stopped.is_set() or self.paused or not self.should_send():
                break
            try:
                status, data = self._send(batch)
                if status is not None and status != 429 and status < 500:
                    break
            except Exception:
                status, data = None, None
            if attempt == 2 or self._stopped.wait(.5 * 2**attempt):
                break
        if status == 401:
            self.paused = True
        for event, (outcome, reason) in zip(batch, app_results(status, data, len(batch))):
            self._finish(event,outcome,reason)
        if status == 202 and isinstance(data, dict) and self.on_warning:
            for warning in data.get('warnings', []) if isinstance(data.get('warnings'), list) else []:
                if isinstance(warning, dict) and type(warning.get('index')) is int and 0 <= warning['index'] < len(batch) and warning.get('reason') == 'tag_not_allowed' and warning.get('tag') in _TAGS:
                    try:
                        self.on_warning(batch[warning['index']]['id'], 'tag_not_allowed', warning['tag'])
                    except Exception:
                        pass  # A local advisory cannot terminate the sender.

    def _run(self):
        while not self._stopped.is_set():
            if self.paused:
                self._stopped.wait(.1)
                continue
            try:
                first = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            events = [first]
            # Briefly coalesce one bounded wave, never block a crash callback.
            self._stopped.wait(.01)
            for _ in range(19):
                try:
                    events.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            pending = {event['id']: event for event in events}
            try:
                for batch in split_batches(self.destination, events):
                    self._deliver(batch)
                    for event in batch:
                        pending.pop(event['id'], None)
            except Exception:
                # Optional sender bookkeeping cannot terminate the worker or
                # strand slots. Leave any unacknowledged durable evidence.
                for identity, event in pending.items():
                    with self._lock:
                        unfinished = identity in self._ids
                    if unfinished:
                        self._finish(event,None,'invalid_response')
            finally:
                for _ in events:
                    self._queue.task_done()

    def shutdown(self):
        self._stopped.set()
        with self._lock:
            connection, self._connection = self._connection, None
            self._ids.clear()
        if connection:
            try:
                connection.close()
            except OSError:
                pass
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
