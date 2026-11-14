"""Explicit-consent, bounded technical diagnostics. Never serialize exception messages.

Capture is a memory-only enqueue. One worker owns consent reads, sanitization,
private persistence and delivery. Desktop Python only writes the Electron inbox.
"""
from __future__ import annotations

import builtins
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / 'config' / 'diagnostics.json'
_HEX = re.compile(r'[a-f0-9]{32}')
_PROFILE = re.compile(r'install_[a-f0-9]{32}')
_KEYS = {'category','source','stage','error_code','error_type','engine_id','app_version','build_commit','platform','arch','device_family','exit_code','signal','reason','frames','fingerprint','uptime_bucket','elapsed_bucket','memory_bucket','disk_bucket','occurrences','schema_version'}
_ENUMS = {'category':'categories','source':'sources','stage':'stages','error_code':'error_codes','error_type':'error_types','engine_id':'engine_ids','platform':'platforms','arch':'arches','device_family':'device_families','signal':'signals','reason':'reasons'}
_FALLBACKS = {'category':'operation_failed','source':'backend','stage':'other','error_code':'UNKNOWN','error_type':'OtherError','engine_id':'unknown','platform':'unknown','arch':'unknown','device_family':'unknown','reason':'unknown','signal':'unknown'}
_CURRENT_APP_VERSION = object()
_GRANT_UNSET = object()
_grant_authority = ContextVar('diagnostic_grant_authority', default=_GRANT_UNSET)


@contextmanager
def grant_scope(epoch):
    """Bind only a local observed grant; None means known OFF, not unknown."""
    token = _grant_authority.set(epoch)
    try:
        yield
    finally:
        _grant_authority.reset(token)


def inherited_grant():
    """Nested work inherits its owner; an unbound root observes cached consent."""
    bound = _grant_authority.get()
    return observe_grant() if bound is _GRANT_UNSET else bound


async def await_owned(awaitable, epoch):
    with grant_scope(epoch):
        return await awaitable


def bind_callable(function, epoch=_GRANT_UNSET):
    """Carry only authority across an owned executor boundary, not request data."""
    if epoch is _GRANT_UNSET:
        epoch = inherited_grant()
    def run(*args, **kwargs):
        with grant_scope(epoch):
            return function(*args, **kwargs)
    return run


def _app_version(value):
    if isinstance(value, str) and len(value) <= 64 and re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*|(?:a|b|rc|dev)\d{1,6})?', value):
        return value
    return None


def _diagnostic_destination():
    """Diagnostic transport requires TLS, except exact loopback development URLs."""
    from core.analytics import _destination
    from core.analytics_transport import valid_url
    destination = _destination()
    if not isinstance(destination, dict) or not isinstance(destination.get('site'), str) or not re.fullmatch(r'[a-z0-9]{6,32}', destination['site']):
        return {}
    for key in ('host', 'appUrl'):
        value = destination.get(key)
        if not valid_url(value, origin=key == 'host'):
            return {}
        parsed = urlsplit(value)
        if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            return {}
    return destination


def _schema():
    value = json.loads(_CONFIG.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('diagnostic schema unavailable')
    value['_frame_paths'] = frozenset(value.get('frame_paths',[]))
    return value


def _limit(schema, key, ceiling):
    value = schema.get('limits',{}).get(key,ceiling)
    return min(value,ceiling) if type(value) is int and value > 0 else ceiling


def _killed():
    return any(os.environ.get(key, '').strip().lower() in {'1', 'true', 'yes', 'on'}
               for key in ('OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'))


def _frames(value, schema):
    if not isinstance(value, str) or len(value.encode('utf-8')) > _limit(schema,'frame_bytes',1800):
        return ''
    known = schema.get('_frame_paths',frozenset(schema.get('frame_paths', [])))
    result = []
    for line in value.splitlines()[:24]:
        match = re.fullmatch(r'([^:\\]+):(\d{1,8})(?::(\d{1,8}))?', line)
        if match and match[1] in known and 0 < int(match[2]) <= 10000000 and (not match[3] or 0 < int(match[3]) <= 10000000):
            result.append(line)
    return '\n'.join(result)


def sanitize_props(raw, schema=None):
    """Closed values as well as keys; safe again at the persistence/send boundary."""
    schema = schema or _schema()
    if not isinstance(raw, dict):
        raise ValueError('invalid diagnostic properties')
    result = {}
    for key, values in _ENUMS.items():
        value = raw.get(key, _FALLBACKS.get(key))
        if value is not None:
            result[key] = value if isinstance(value, str) and value in schema.get(values, []) else _FALLBACKS.get(key, 'unknown')
    for key in ('uptime_bucket','elapsed_bucket','memory_bucket','disk_bucket'):
        if key in raw:
            values = schema.get(key + 's', schema.get('buckets', []))
            result[key] = raw[key] if raw[key] in values else 'unknown'
    for key in ('app_version','build_commit'):
        value = raw.get(key)
        if key == 'app_version' and _app_version(value) is not None:
            result[key] = value
        elif key == 'build_commit' and isinstance(value, str) and re.fullmatch(r'[a-f0-9]{7,40}', value):
            result[key] = value
    exit_code = raw.get('exit_code')
    if type(exit_code) is int and -(2**31) <= exit_code <= 2**32-1:
        result['exit_code'] = exit_code
    result['occurrences'] = min(1000000, max(1, raw.get('occurrences', 1))) if type(raw.get('occurrences', 1)) is int else 1
    result['schema_version'] = 1
    frames = _frames(raw.get('frames', ''), schema)
    if frames:
        result['frames'] = frames
    # No caller-supplied hash, message or arbitrary identifier participates.
    technical = {k:v for k,v in result.items() if k not in {'occurrences','schema_version'}}
    result['fingerprint'] = hashlib.sha256(json.dumps(technical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def validate_record(value, epoch, schema=None):
    if not isinstance(value, dict) or set(value) != {'version','id','timestamp_ms','epoch','props'}:
        raise ValueError('invalid diagnostic record')
    if value['version'] != 1 or not isinstance(value['id'], str) or not _HEX.fullmatch(value['id']) or value['epoch'] != epoch:
        raise ValueError('invalid diagnostic identity')
    stamp = value['timestamp_ms']
    now = int(time.time()*1000)
    if type(stamp) is not int or not now-_limit(schema or _schema(),'ttl_ms',604800000) <= stamp <= now+60000:
        raise ValueError('expired diagnostic record')
    return {**value, 'props':sanitize_props(value['props'], schema)}


_causal_counter = itertools.count(1)


def _exception_fields(error, schema):
    """Bounded projection only: no __str__, filesystem access or retained frames."""
    result = {}
    if not isinstance(error, BaseException):
        return result
    kinds = type(error).__mro__
    for cls in kinds:
        if cls.__module__ == 'builtins' and cls.__name__ in schema.get('error_types', []) and getattr(builtins, cls.__name__, None) is cls:
            result['error_type'] = cls.__name__
            break
    result.setdefault('error_type','OtherError')
    if MemoryError in kinds:
        result['error_code'] = 'HOST_MEMORY_EXHAUSTED'
    elif TimeoutError in kinds or ConnectionError in kinds:
        result['error_code'] = 'NETWORK_ERROR'
    elif OSError in kinds:
        number = BaseException.__getattribute__(error,'errno')
        if number in (errno.ENOSPC,errno.EDQUOT):
            result['error_code'] = 'DISK_FULL'
    lines = []
    total = 0
    tb = BaseException.__getattribute__(error,'__traceback__')
    known = schema.get('_frame_paths',schema.get('frame_paths',[]))
    for _ in range(24):
        if tb is None:
            break
        filename = tb.tb_frame.f_code.co_filename
        prefix = str(_ROOT) + os.sep
        relative = filename[len(prefix):].replace(os.sep,'/') if filename.startswith(prefix) else filename.replace(os.sep,'/')
        if relative in known:
            line = f'{relative}:{tb.tb_lineno}'
            if total + len(line.encode('utf-8')) + 1 <= 1800:
                lines.append(line)
                total += len(line.encode('utf-8')) + 1
        tb = tb.tb_next
    if lines:
        result['frames'] = '\n'.join(lines)
    return result


def _atomic(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.diagnostic-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            # A successful replacement has already consumed the temporary file.
            pass


class _CaptureQueue(queue.Queue):
    def peek_nowait(self):
        # Retain the slot while the sole worker publishes. Producers only append,
        # so a failed write needs neither a second queue nor a racy re-insertion.
        with self.mutex:
            if not self._qsize():
                raise queue.Empty
            return self.queue[0]


class Reporter:
    def __init__(self):
        from core.version import APP_VERSION
        self.app_version = _app_version(APP_VERSION)
        self.managed = os.environ.get('OMNIVOICE_DIAGNOSTICS_DIR')
        from core.config import DATA_DIR
        self.root = Path(self.managed) if self.managed else Path(DATA_DIR) / 'diagnostics'
        self.directory = self.root / ('inbox' if self.managed else 'outbox')
        self.lock = threading.RLock()
        self.intake = _CaptureQueue(maxsize=64)
        self.acks = _CaptureQueue(maxsize=64)
        self.ack_lock = threading.Lock()
        self.stop = threading.Event()
        self.enabled = False
        self.consent_failed = False
        self.kill_observed = False
        self.prompted = False
        self.epoch = None
        self.profile = None
        self.dropped = 0
        self.pending = 0
        self.transport = None
        self.inflight = {}
        self.retry_at = {}
        self.storage_retry_at = 0
        self.publication = None
        self.schema = None
        self.marker_owned = False
        self.marker_active_at = None
        self.recovered_marker = None
        self.session_retired = False
        self.seen_errors = []
        self.started = time.time()
        self.thread = threading.Thread(target=self._run, name='diagnostic-reports', daemon=True)

    def start(self):
        self.thread.start()

    def _capacity(self, kind):
        """Allocate desktop durable storage between main and Python stages."""
        ceiling = 64 if kind == 'count' else 524288
        limit = _limit(self.schema, 'queue_' + kind, ceiling)
        if self.managed:
            limit = min(limit, _limit(self.schema, 'managed_inbox_' + kind, ceiling // 2))
        return limit

    def _hard_killed(self):
        current = _killed()
        # Standalone consent belongs to this process. Managed consent remains
        # desktop-owned; Python must not rewrite its authority's grant.
        if current and not self.managed:
            self.kill_observed = True
        return current or self.kill_observed

    def capture(self, category, *, stage='other', error=None, error_code=None, engine_id=None, _reported_app_version=_CURRENT_APP_VERSION, _expected_epoch=None, _reported_timestamp_ms=None, **metadata):
        try:
            # Only bounded projection against cached policy here; no retained
            # exceptions, file reads or waits on the diagnostic worker.
            hard_killed = self._hard_killed()
            epoch = self.epoch
            if not self.enabled or not epoch or hard_killed or self.stop.is_set():
                return False
            bound = _grant_authority.get()
            if bound is not _GRANT_UNSET and (bound is None or bound != epoch):
                return False
            if _expected_epoch is not None and (type(_expected_epoch) is not str or _expected_epoch != epoch):
                return False
            schema = self.schema
            if schema is None:
                return False
            timestamp = int(time.time()*1000) if _reported_timestamp_ms is None else _reported_timestamp_ms
            if type(timestamp) is not int or not self._recent(timestamp):
                return False
            fields = _exception_fields(error,schema)
            fields.update({k:v for k,v in metadata.items() if k in _KEYS and (type(v) in (bool,int) or isinstance(v,str) and len(v.encode('utf-8')) <= 1800)})
            fields.update(category=category,stage=stage,source='renderer' if metadata.get('source') == 'renderer' else 'backend')
            if error_code is not None:
                fields['error_code'] = error_code
            if engine_id is not None:
                fields['engine_id'] = engine_id
            token = None
            if isinstance(error,BaseException):
                state = BaseException.__getattribute__(error,'__dict__')
                marker = state.get('_voicestudio_diagnostic_token')
                if not isinstance(marker,tuple) or len(marker) != 2 or marker[0] != epoch or type(marker[1]) is not int:
                    marker = (epoch,next(_causal_counter))
                    state['_voicestudio_diagnostic_token'] = marker
                token = marker[1]
            # Only local session recovery can supply a prior runtime version.
            # Keep it separate from caller/UI metadata and from later spool IO.
            reported_version = self.app_version if _reported_app_version is _CURRENT_APP_VERSION else _app_version(_reported_app_version)
            item = (epoch,sanitize_props(fields,schema),token,reported_version,timestamp)
            try:
                self.intake.put_nowait(item)
                return True
            except queue.Full:
                self.dropped += 1
        except Exception:
            pass
        return False

    def _recent(self, timestamp):
        now = time.time()*1000
        return now-_limit(self.schema,'ttl_ms',604800000) <= timestamp <= now+60000

    def _consent(self):
        try:
            if self.managed:
                path = self.root / 'consent.json'
                if path.is_symlink() or path.stat().st_size > 4096:
                    return {}
                data = json.loads(path.read_text(encoding='utf-8'))
                return data if isinstance(data, dict) and data.get('version') == 1 else {}
            from core import prefs
            if (self.root / 'revoked.json').exists():
                return {'version':1, 'enabled':False, 'prompted':True}
            data = prefs._load()
            return {'version':1,'enabled':data.get('diagnostics_enabled') is True,'prompted':data.get('diagnostics_prompted') is True,
                    'epoch':data.get('diagnostics_epoch'),'profile_id':data.get('diagnostics_profile_id')}
        except Exception:
            return {}

    def _purge(self):
        for folder in (self.root / 'outbox', self.root / 'inbox') if not self.managed else (self.directory,):
            if folder.is_dir() and not folder.is_symlink():
                for path in folder.iterdir():
                    if path.is_file() and (_HEX.fullmatch(path.stem) or path.name.startswith('.diagnostic-')):
                        path.unlink(missing_ok=True)
        if not self.managed:
            (self.root / 'session.json').unlink(missing_ok=True)
        self.marker_owned = False
        self.pending = 0

    def refresh(self):
        with self.lock:
            hard_killed = self._hard_killed()
            data = self._consent()
            if hard_killed and not self.managed and data.get('enabled') is True:
                try:
                    # Preserve analytics/shared prefs, and retain the existing
                    # private revocation tombstone if saving OFF fails.
                    self.consent(False)
                except OSError:
                    pass
                data = self._consent()
            epoch, profile = data.get('epoch'), data.get('profile_id')
            valid = data.get('enabled') is True and isinstance(epoch,str) and _HEX.fullmatch(epoch) and isinstance(profile,str) and _PROFILE.fullmatch(profile)
            enabled = bool(valid and not hard_killed and not self.consent_failed)
            self.prompted = data.get('prompted', bool(data)) is True
            if self.epoch != epoch or self.enabled != enabled:
                with self.ack_lock:
                    self.enabled = False
                if self.transport:
                    self.transport.shutdown()
                    self.transport = None
                self.seen_errors.clear()
                self.inflight.clear()
                self.retry_at.clear()
                self.storage_retry_at = 0
                self.publication = None
                if not enabled:
                    try:
                        self._purge()
                    except OSError:
                        self.dropped += 1
                with self.ack_lock:
                    while not self.acks.empty():
                        try:self.acks.get_nowait()
                        except queue.Empty:break
                    self.epoch, self.profile = epoch if valid else None, profile if valid else None
            self.enabled = enabled and self.schema is not None

    def _files(self):
        records = []
        if not self.directory.exists() or self.directory.is_symlink():
            return records
        total = 0
        for path in sorted(self.directory.iterdir())[:256]:
            try:
                if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.json',path.name):
                    continue
                size = path.stat().st_size
                if size > _limit(self.schema,'body_bytes',8192):
                    path.unlink()
                    self.dropped += 1
                    continue
                record = validate_record(json.loads(path.read_text(encoding='utf-8')), self.epoch, self.schema)
                if path.stem != record['id'] or len(records) >= self._capacity('count') or total+size > self._capacity('bytes'):
                    raise ValueError('diagnostic queue capacity')
                total += size
                records.append(record)
            except FileNotFoundError:
                # Electron normally consumes and unlinks immutable inbox IDs.
                continue
            except OSError:
                # An unread record is not corrupt. Preserve it and defer intake
                # publication too, since its size/identity may still be unknown.
                raise
            except (ValueError, TypeError, KeyError):
                path.unlink(missing_ok=True)
                self.dropped += 1
        self.pending = len(records)
        return records

    def _spool(self, item, records):
        epoch, fields, token, reported_version, timestamp = item
        if epoch != self.epoch or not self.enabled:
            return
        if not self._recent(timestamp):
            self.dropped += 1
            return
        if token is not None:
            now = time.monotonic()
            self.seen_errors = [(value,stamp) for value,stamp in self.seen_errors[-63:] if now-stamp < 60]
            if any(value == token for value,stamp in self.seen_errors):
                return
        if self.publication is not None and self.publication[0] is item:
            self._publish(item, self.publication[1], records)
            if token is not None:
                self.seen_errors.append((token,now))
            return
        fields = dict(fields)
        fields.pop('app_version', None)
        if reported_version is not None:
            fields['app_version'] = reported_version
        fields.update(platform={'Darwin':'macos','Windows':'windows','Linux':'linux'}.get(platform.system(),'unknown'),arch={'AMD64':'x64','x86_64':'x64','aarch64':'arm64','arm64':'arm64','i386':'x86','i686':'x86'}.get(platform.machine(),'unknown'))
        props = sanitize_props(fields,self.schema)
        # Desktop can already have read a published inbox file before it
        # commits and removes that ID. Rewriting it here would lose the new
        # occurrence at that unlink, so managed records stay immutable. The
        # standalone worker owns its outbox and can safely coalesce there.
        if not self.managed:
            for existing in records:
                if existing['props']['fingerprint'] == props['fingerprint'] and existing['id'] not in self.inflight:
                    updated = {**existing, 'props': {**existing['props'], 'occurrences': min(1000000,existing['props']['occurrences']+1)}}
                    self._publish(item, updated, records)
                    if token is not None:
                        self.seen_errors.append((token,now))
                    return
        if len(records) >= self._capacity('count'):
            self.dropped += 1
            return
        record = {'version':1,'id':uuid.uuid4().hex,'timestamp_ms':timestamp,'epoch':epoch,'props':props}
        size = len(json.dumps(record,allow_nan=False,separators=(',',':')).encode())
        existing_size = sum(len(json.dumps(r,allow_nan=False,separators=(',',':')).encode()) for r in records)
        if existing_size + size > self._capacity('bytes'):
            self.dropped += 1
            return
        self._publish(item, record, records)
        if token is not None:
            self.seen_errors.append((token,now))

    def _publish(self, item, record, records):
        # A rename may become visible before its directory fsync fails. Retain
        # this exact intended ID/count until commit, rather than coalescing the
        # same accepted capture again after rereading that visible file.
        self.publication = (item, record)
        _atomic(self.directory / (record['id']+'.json'), record)
        existing = next((row for row in records if row['id'] == record['id']), None)
        if existing is None:
            records.append(record)
        else:
            existing.update(record)
        self.publication = None
        self.pending = len(records)

    def _persist_intake(self):
        if self.storage_retry_at > time.monotonic():
            return None
        try:
            records = self._files()
            self._session()
            for _ in range(64):
                try:item = self.intake.peek_nowait()
                except queue.Empty:break
                self._spool(item,records)
                self.intake.get_nowait()
                self.publication = None
        except OSError:
            # The worker still checks consent every tick. Retry durable IO at
            # a finite cadence without losing a slot or transmitting memory-only
            # reports; fresh events are not required to wake recovery.
            self.storage_retry_at = time.monotonic()+60
            return None
        self.storage_retry_at = 0
        return records

    def _session(self):
        if self.managed or self.session_retired:
            return
        path = self.root / 'session.json'
        now = time.time()
        if self.marker_owned and self.marker_active_at is not None and 0 <= now-self.marker_active_at < 60:
            return
        if not self.marker_owned and path.exists():
            try:
                previous = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size <= 4096 and not path.is_symlink() else {}
                from core.run_sentinel import _previous_run_alive
                pid, started = previous.get('pid'), previous.get('started_at')
                if type(pid) is int and _previous_run_alive(pid,started):
                    return  # A live concurrent process owns this marker.
                active = previous.get('last_active_at', started)
                identity = (previous.get('epoch'), pid, started)
                if previous.get('epoch') == self.epoch and type(pid) is int and previous.get('clean') is not True and identity != self.recovered_marker and type(active) in (int,float) and math.isfinite(active) and self._recent(active*1000):
                    if self.capture('backend_crash', stage='startup', reason='unclean_exit', _reported_app_version=previous.get('app_version'), _reported_timestamp_ms=int(active*1000)):
                        self.recovered_marker = identity
            except FileNotFoundError:
                pass  # A removed marker is normal absence, not a storage fault.
            except OSError:
                # Preserve unread evidence until the existing worker's bounded
                # storage retry can establish its age, owner and consent epoch.
                raise
            except (ValueError, TypeError, AttributeError, OverflowError):
                pass
        _atomic(path, {'version':1,'epoch':self.epoch,'pid':os.getpid(),'started_at':self.started,'last_active_at':now,'app_version':self.app_version})
        self.marker_owned = True
        self.marker_active_at = now

    def retire_session(self):
        """Intentional shutdown begins before service teardown; capture stays enabled."""
        with self.lock:
            self.session_retired = True
            if not self.marker_owned:
                return
            marker = self.root / 'session.json'
            try:
                # A locked delete must still describe an intentional exit.
                _atomic(marker, {'version':1,'epoch':self.epoch,'pid':os.getpid(),'started_at':self.started,'app_version':self.app_version,'clean':True})
            except OSError:
                # Deletion below can still retire a marker whose rewrite failed.
                pass
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                self.dropped += 1
            self.marker_owned = False

    def _tick(self):
        self.refresh()
        if not self.enabled:
            self._purge()
            while not self.intake.empty():
                try:self.intake.get_nowait()
                except queue.Empty:break
            return
        while True:
            try:ack_epoch,identity,status = self.acks.peek_nowait()
            except queue.Empty:break
            if ack_epoch != self.epoch:
                self.acks.get_nowait()
                continue
            if status is not None and 0 < status < 500 and status != 429:
                (self.directory / (identity+'.json')).unlink(missing_ok=True)
                self.retry_at.pop(identity,None)
                # Delivery cleanup can release capacity while intake writes
                # remain blocked by a full disk. Do not wait out that cooldown.
                self.storage_retry_at = 0
            else:
                self.retry_at[identity] = time.monotonic()+60
            self.inflight.pop(identity,None)
            self.acks.get_nowait()
        records = self._persist_intake()
        if records is None:
            return
        if self.managed:
            return
        self.pending = len(records)
        if not self.transport:
            destination = _diagnostic_destination()
            if destination:
                from core.analytics_transport import SupermetryTransport
                epoch = self.epoch
                self.transport = SupermetryTransport(destination,lambda:self.enabled and self.epoch == epoch and not self.stop.is_set() and not self._hard_killed(),on_result=lambda identity,status:self._delivery_result(epoch,identity,status))
        if self.transport:
            for record in records:
                identity = record['id']
                if identity in self.inflight or self.retry_at.get(identity,0) > time.monotonic():
                    continue
                safe = validate_record(record,self.epoch,self.schema)
                if self.transport.capture('diagnostic_report',safe['props'],self.profile,path='/diagnostics',event_id=identity,timestamp_ms=safe['timestamp_ms']):
                    self.inflight[identity] = True

    def _delivery_result(self, epoch, identity, status):
        # A late callback cannot consume the new epoch's bounded ack capacity.
        with self.ack_lock:
            if self.enabled and self.epoch == epoch and not self.stop.is_set():
                self.acks.put_nowait((epoch,identity,status))

    def _run(self):
        if self._hard_killed():
            # Revocation cannot depend on a valid reporting policy: a missing
            # schema must not preserve a grant for the next un-killed launch.
            try:
                self.refresh()
            except Exception:
                pass
        try:
            self.schema = _schema()
            self.intake.maxsize = _limit(self.schema,'queue_count',64)
        except Exception:return  # Missing/corrupt policy fails closed.
        while not self.stop.is_set():
            try:
                with self.lock:
                    if self.stop.is_set():
                        break
                    self._tick()
            except Exception:
                # Never log raw input, and never let diagnostics stop the app.
                pass
            self.stop.wait(.1)

    def status(self):
        with self.lock:
            hard_killed = self._hard_killed()
            # Availability describes configuration, independently of consent or
            # transport creation. Resolve only local configuration here so the
            # first source/Docker settings response can offer an explicit choice.
            available = bool(self.managed)
            if not self.managed:
                available = bool(_diagnostic_destination())
            return {'enabled':self.enabled and not hard_killed,'opted_in':self.enabled and not hard_killed,'available':available and not _killed(),'prompted':self.prompted,'externally_managed':bool(self.managed),'pending_count':self.pending,'dropped_count':self.dropped,'grant_epoch':self.epoch if self.enabled and not hard_killed else None}

    def consent(self, enabled):
        if self.managed:
            raise PermissionError('Desktop controls diagnostic consent')
        from core import prefs
        with self.lock:
            if enabled and _killed():
                self.refresh()
                return self.status()
            if enabled and self.enabled and not self.kill_observed:
                return self.status()
            # A persistence failure must leave this process closed, rather
            # than letting the worker restore an older enabled preference.
            self.enabled = False
            self.consent_failed = True
            if self.transport:
                self.transport.shutdown()
                self.transport = None
            if not enabled:
                # Keep withdrawal durable even if replacing the shared prefs
                # fails. This private tombstone contains no old grant or ID.
                try:
                    _atomic(self.root / 'revoked.json', {'version':1})
                except OSError:
                    pass  # A successful prefs write below still persists OFF.
            with prefs._MUTATE_LOCK:
                data = prefs._load()
                data.update(diagnostics_enabled=enabled,diagnostics_prompted=True,diagnostics_epoch=uuid.uuid4().hex,diagnostics_profile_id='install_'+uuid.uuid4().hex)
                prefs._save(data)
            if enabled:
                # Removing a durable revocation is allowed only after a new
                # explicit grant has been saved. Failure stays closed.
                (self.root / 'revoked.json').unlink(missing_ok=True)
                self.kill_observed = False
            self.consent_failed = False
            try:
                self._purge()
            except OSError:
                # Disabled worker retries cleanup; persisted new epoch makes
                # undeletable old reports ineligible even after a restart.
                self.dropped += 1
            self.schema = _schema()
            self.intake.maxsize = _limit(self.schema,'queue_count',64)
            self.refresh()
            return self.status()

    def shutdown(self):
        with self.lock:
            self.retire_session()
            try:
                self.refresh()
                if self.enabled:
                    # Make one final bounded publication attempt before exit.
                    self.storage_retry_at = 0
                    self._persist_intake()
            except Exception:
                pass
            self.stop.set()
            self.enabled = False
            if self.transport:
                self.transport.shutdown()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=2.5)


_reporter = None
_init_lock = threading.Lock()
_hooks_installed = False


def _install_hooks():
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    previous = sys.excepthook
    def unhandled(kind, error, trace):
        if not issubclass(kind, (KeyboardInterrupt,SystemExit)):
            capture('backend_crash',stage='other',error=error,reason='unhandled_exception')
        previous(kind,error,trace)
    sys.excepthook = unhandled
    previous_thread = threading.excepthook
    def thread_unhandled(args):
        if not issubclass(args.exc_type,(KeyboardInterrupt,SystemExit)):
            capture('operation_failed',stage='other',error=args.exc_value,reason='unhandled_exception')
        previous_thread(args)
    threading.excepthook = thread_unhandled


def initialize():
    global _reporter
    with _init_lock:
        if _reporter is None:
            _reporter = Reporter()
            _reporter.start()
            _install_hooks()


def capture(category, *, stage='other', error=None, error_code=None, engine_id=None, **typed_metadata):
    try:
        return bool(_reporter and _reporter.capture(category,stage=stage,error=error,error_code=error_code,engine_id=engine_id,**typed_metadata))
    except Exception:
        return False


def observe_grant():
    """Snapshot cached authority before async work, without initialization or IO."""
    instance = _reporter
    if instance is None:
        return None
    epoch = instance.epoch
    return epoch if epoch and instance.enabled and instance.epoch == epoch and not instance.stop.is_set() and not instance._hard_killed() else None


def shutdown():
    global _reporter
    with _init_lock:
        if _reporter:
            _reporter.shutdown()
            _reporter = None


def prepare_shutdown():
    """Retire an existing session only; never initialize consent or a reporter."""
    instance = _reporter
    if instance is not None:
        instance.retire_session()


def status():
    initialize()
    return _reporter.status()


def set_opted_in(enabled):
    if type(enabled) is not bool:
        raise ValueError('Diagnostic consent requires a boolean')
    initialize()
    return _reporter.consent(enabled)


def capture_from_ui(body):
    if not isinstance(body,dict) or set(body)-{'category','source','stage','error_type','error_code','engine_id','frames','expected_epoch'}:
        raise ValueError('Unsupported diagnostic fields')
    schema = _schema()
    if body.get('source') != 'renderer':
        raise ValueError('Unsupported diagnostic source')
    for key in ('category','stage','error_type','error_code','engine_id'):
        if key in body and body[key] not in schema.get(_ENUMS[key],[]):
            raise ValueError('Unsupported diagnostic value')
    if 'frames' in body and _frames(body['frames'],schema) != body['frames']:
        raise ValueError('Unsupported diagnostic frames')
    expected = body.get('expected_epoch')
    if expected is not None and (type(expected) is not str or not _HEX.fullmatch(expected)):
        raise ValueError('Unsupported diagnostic grant')
    if expected is None:
        return False
    # The grant is a local control value, never part of event properties.
    fields = {key:value for key,value in body.items() if key != 'expected_epoch'}
    return capture(_expected_epoch=expected, **fields)


def capture_failure(stage, *, error=None, error_code=None, engine_id=None, expected_epoch=None):
    """Terminal owners pass a coarse fixed stage, never their raw context."""
    # HTTP validation/auth/licence refusals and explicit user cancellation are
    # expected outcomes. Do not classify by arbitrary exception messages.
    if error is not None:
        kinds = {(cls.__module__,cls.__name__) for cls in type(error).__mro__}
        if kinds & {('asyncio.exceptions','CancelledError'),('builtins','KeyboardInterrupt'),('builtins','SystemExit'),('services.model_manager','ModelLoadInterruptedByShutdown'),('services.model_acceptance','ModelLicenceNotAccepted')}:
            return False
        if ('starlette.exceptions','HTTPException') in kinds and getattr(error,'status_code',500) < 500:
            return False
    category = {'generation':'generation_failed','dub':'generation_failed','batch':'generation_failed','transcription':'generation_failed','translation':'operation_failed','model_load':'model_load_failed','engine':'engine_failed','install':'install_failed','download':'download_failed'}.get(stage,'operation_failed')
    return capture(category,stage=stage,error=error,error_code=error_code,engine_id=engine_id,_expected_epoch=expected_epoch)


def route_stage(route):
    """Exact known route components only; request path text is never retained."""
    components = set(str(route).split('/'))
    for names,stage in [({'generate','speech','generation'},'generation'),({'dub','dub-generate','dub_generate'},'dub'),({'transcribe','transcriptions','dictate','capture','dub_transcribe'},'transcription'),({'batch'},'batch'),({'translate','translations','dub_translate'},'translation'),({'model','models'},'model_load')]:
        if components & names:
            return stage
    return 'other'
