"""Crash recovery retains its version; intentional teardown retires only the marker."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.fixture
def session_reporter(monkeypatch, tmp_path):
    reports = importlib.import_module('core.diagnostic_reports')
    from core import analytics, config, prefs, run_sentinel, version
    monkeypatch.delenv('OMNIVOICE_DIAGNOSTICS_DIR', raising=False)
    monkeypatch.delenv('OMNIVOICE_DIAGNOSTICS_DISABLED', raising=False)
    monkeypatch.delenv('OMNIVOICE_ANALYTICS_DISABLED', raising=False)
    monkeypatch.delenv('OMNIVOICE_API_KEY', raising=False)
    monkeypatch.delenv('OMNIVOICE_SERVER_MODE', raising=False)
    monkeypatch.setattr(prefs, '_PREFS_PATH', str(tmp_path / 'prefs.json'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(analytics, '_destination', lambda: {})
    monkeypatch.setattr(run_sentinel, '_previous_run_alive', lambda *_: False)
    monkeypatch.setattr(version, 'APP_VERSION', '0.5.8-beta.1')
    instance = reports.Reporter()
    instance.schema = reports._schema()
    monkeypatch.setattr(reports, '_reporter', instance)
    yield reports, instance
    instance.shutdown()


def saved(instance):
    return [json.loads(path.read_text(encoding='utf-8')) for path in instance.directory.glob('*.json')]


@pytest.mark.parametrize('fault', ['read', 'stat'])
def test_unread_recent_session_is_preserved_until_bounded_recovery(session_reporter, monkeypatch, fault):
    reports, old = session_reporter
    old.consent(True); old._tick()
    marker = old.root / 'session.json'
    original = marker.read_bytes()
    before = json.loads(original)
    read, stat = Path.read_text, Path.stat
    unreadable = True
    attempts = []
    clock = [100.0]
    monkeypatch.setattr(reports.time, 'monotonic', lambda: clock[0])
    def controlled_read(path, *args, **kwargs):
        if path == marker and unreadable and fault == 'read':
            attempts.append('read')
            raise PermissionError('temporary old journal lock')
        return read(path, *args, **kwargs)
    def controlled_stat(path, *args, **kwargs):
        if path == marker and unreadable and fault == 'stat':
            attempts.append('stat')
            raise PermissionError('temporary old journal metadata lock')
        return stat(path, *args, **kwargs)
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    monkeypatch.setattr(Path, 'read_text', controlled_read)
    monkeypatch.setattr(Path, 'stat', controlled_stat)
    try:
        recovered._tick()
        assert marker.read_bytes() == original, 'readable-later crash evidence was overwritten'
        assert not recovered.marker_owned and saved(recovered) == []
        assert recovered.storage_retry_at == 160 and len(attempts) == 1
        unreadable = False
        clock[0] += 59
        recovered._tick()
        assert marker.read_bytes() == original
        clock[0] += 1
        recovered._tick()
        row, = saved(recovered)
        assert row['timestamp_ms'] == int(before['last_active_at']*1000)
        assert row['props']['app_version'] == before['app_version']
        assert recovered.marker_owned
        recovered._tick()
        assert len(saved(recovered)) == 1
    finally:
        unreadable = False
        recovered.shutdown(); old.marker_owned = False


@pytest.mark.parametrize('content', ['{broken', '[]'])
def test_proven_invalid_session_can_be_replaced_without_cooldown(session_reporter, content):
    _, reporter = session_reporter
    reporter.consent(True)
    reporter.root.mkdir(exist_ok=True)
    marker = reporter.root / 'session.json'
    marker.write_text(content, encoding='utf-8')
    reporter._tick()
    assert reporter.marker_owned and reporter.storage_retry_at == 0
    assert saved(reporter) == []
    assert json.loads(marker.read_text(encoding='utf-8'))['pid'] == os.getpid()


def test_old_session_disappearing_before_read_does_not_delay_current_marker(session_reporter, monkeypatch):
    reports, old = session_reporter
    old.consent(True); old._tick()
    marker = old.root / 'session.json'
    read = Path.read_text
    def consumed(path, *args, **kwargs):
        if path == marker:
            path.unlink(missing_ok=True)
        return read(path, *args, **kwargs)
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, 'read_text', consumed)
            recovered._tick()
        assert recovered.marker_owned and recovered.storage_retry_at == 0
        assert saved(recovered) == []
        assert marker.exists()
    finally:
        recovered.shutdown(); old.marker_owned = False


@pytest.mark.parametrize('new_grant', [False, True])
def test_unread_session_cannot_survive_withdrawal_or_borrow_new_grant(session_reporter, monkeypatch, new_grant):
    reports, old = session_reporter
    old.consent(True); old._tick()
    marker = old.root / 'session.json'
    original = marker.read_bytes()
    read = Path.read_text
    def unreadable(path, *args, **kwargs):
        if path == marker:
            raise PermissionError('temporary old journal lock')
        return read(path, *args, **kwargs)
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, 'read_text', unreadable)
            recovered._tick()
            assert marker.read_bytes() == original
            recovered.consent(False)
            recovered._tick()
        assert not marker.exists() and saved(recovered) == []
        if new_grant:
            recovered.consent(True); recovered._tick()
            assert saved(recovered) == []
            assert json.loads(marker.read_text(encoding='utf-8'))['epoch'] == recovered.epoch
    finally:
        recovered.shutdown(); old.marker_owned = False


@pytest.mark.parametrize('age', [8 * 86400, -3600])
def test_expired_or_future_crash_marker_is_not_renewed(session_reporter, monkeypatch, age):
    reports, old = session_reporter
    old.consent(True)
    old._tick()
    now = time.time()
    marker = old.root / 'session.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    value.update(started_at=now-age, last_active_at=now-age)
    marker.write_text(json.dumps(value), encoding='utf-8')
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    try:
        recovered._tick()
        assert saved(recovered) == [], 'expired/future evidence was renewed as a current report'
    finally:
        recovered.shutdown()
        old.marker_owned = False


def test_recent_crash_after_long_session_uses_persisted_heartbeat_time(session_reporter, monkeypatch):
    reports, old = session_reporter
    now = [time.time()]
    monkeypatch.setattr(reports.time, 'time', lambda: now[0])
    old.started = now[0] - 8 * 86400
    old.consent(True)
    old._tick()
    marker = old.root / 'session.json'
    first = json.loads(marker.read_text(encoding='utf-8'))
    assert first['started_at'] == old.started
    now[0] += 61
    old._tick()
    heartbeat = json.loads(marker.read_text(encoding='utf-8'))
    assert heartbeat['started_at'] == first['started_at']
    assert heartbeat['app_version'] == first['app_version']
    assert heartbeat['last_active_at'] == now[0]
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    now[0] += 10
    try:
        recovered._tick()
        row, = saved(recovered)
        assert row['timestamp_ms'] == int(heartbeat['last_active_at'] * 1000)
        assert row['props']['app_version'] == first['app_version']
    finally:
        recovered.shutdown()
        old.marker_owned = False


def test_storage_retry_cannot_renew_recovered_evidence(session_reporter, monkeypatch):
    reports, old = session_reporter
    old.consent(True); old._tick()
    now = [time.time()]
    marker = old.root / 'session.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    value['last_active_at'] = now[0] - 60
    marker.write_text(json.dumps(value), encoding='utf-8')
    atomic = reports._atomic
    failed = True
    def controlled_atomic(path, record):
        if path.parent == old.directory and failed:
            raise OSError('temporary report storage failure')
        return atomic(path, record)
    monkeypatch.setattr(reports, '_atomic', controlled_atomic)
    monkeypatch.setattr(reports.time, 'time', lambda: now[0])
    recovered = reports.Reporter(); recovered.schema = reports._schema()
    try:
        recovered._tick()
        assert recovered.intake.qsize() == 1
        failed = False
        now[0] += 8 * 86400
        recovered.storage_retry_at = 0
        recovered._tick()
        assert saved(recovered) == []
        assert recovered.intake.empty()
    finally:
        recovered.shutdown()
        old.marker_owned = False


def test_lifespan_diagnostic_shutdown_does_not_wait_for_default_executor(session_reporter, monkeypatch):
    reports, reporter = session_reporter
    import main
    reporter.consent(True); reporter._tick()
    marker = reporter.root / 'session.json'
    monkeypatch.setattr(reports, 'initialize', lambda: None)
    @asynccontextmanager
    async def application(_):
        yield
        assert not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('clean') is True
    monkeypatch.setattr(main, '_application_lifespan', application)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release = threading.Event()
        entered = asyncio.Event()
        def occupied():
            loop.call_soon_threadsafe(entered.set)
            release.wait(5)
        blocker = loop.run_in_executor(None, occupied)
        await asyncio.wait_for(entered.wait(), 2)
        async def lifespan():
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pass
        task = asyncio.create_task(lifespan())
        try:
            done, _ = await asyncio.wait({task}, timeout=1)
            assert task in done, 'diagnostics queued shutdown behind occupied default executor'
            await task
        finally:
            release.set()
            await asyncio.wait_for(blocker, 2)
            await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_diagnostic_finalization_timeout_does_not_join_a_blocked_daemon(monkeypatch):
    import main
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = threading.Event()
        release = threading.Event()
        finished = asyncio.Event()
        def blocked():
            entered.set()
            release.wait(5)
            loop.call_soon_threadsafe(finished.set)
        try:
            await main._finalize_diagnostics(blocked, timeout=.05)
            assert entered.is_set() and not finished.is_set()
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 2)
    asyncio.run(scenario())


def test_actual_old_session_crash_keeps_its_app_version_after_upgrade(session_reporter, monkeypatch):
    reports, old = session_reporter
    from core import version
    old.consent(True)
    old._tick()  # Actual live marker from version A; emulate abrupt process loss.
    monkeypatch.setattr(version, 'APP_VERSION', '0.5.9-beta.2')
    upgraded = reports.Reporter()
    upgraded.schema = reports._schema()
    try:
        upgraded._tick()
        row, = saved(upgraded)
        assert row['props']['category'] == 'backend_crash'
        assert row['props']['app_version'] == '0.5.8-beta.1'
        assert json.loads((upgraded.root / 'session.json').read_text(encoding='utf-8'))['app_version'] == '0.5.9-beta.2'
    finally:
        upgraded.shutdown()
        old.marker_owned = False


def test_installed_pep440_beta_version_survives_marker_and_upgrade_recovery(session_reporter, monkeypatch):
    reports, _ = session_reporter
    from core import version
    monkeypatch.setattr(version, 'APP_VERSION', '0.5.8b1')
    old = reports.Reporter()
    old.schema = reports._schema()
    old.consent(True)
    old._tick()
    assert json.loads((old.root / 'session.json').read_text(encoding='utf-8'))['app_version'] == '0.5.8b1'
    monkeypatch.setattr(version, 'APP_VERSION', '0.5.9rc2')
    upgraded = reports.Reporter()
    upgraded.schema = reports._schema()
    try:
        upgraded._tick()
        row, = saved(upgraded)
        assert row['props']['app_version'] == '0.5.8b1'
    finally:
        upgraded.shutdown()
        old.marker_owned = False
        old.shutdown()


@pytest.mark.parametrize('prior_version', [None, 'private/customer/version'])
def test_legacy_or_invalid_marker_version_is_unknown_not_the_new_app_version(session_reporter, prior_version):
    _, reporter = session_reporter
    reporter.consent(True)
    marker = {'version': 1, 'epoch': reporter.epoch, 'pid': 42, 'started_at': time.time()}
    if prior_version is not None:
        marker['app_version'] = prior_version
    reporter.root.mkdir(exist_ok=True)
    (reporter.root / 'session.json').write_text(json.dumps(marker), encoding='utf-8')
    reporter._tick()
    row, = saved(reporter)
    assert row['props']['category'] == 'backend_crash'
    assert 'app_version' not in row['props']
    assert 'private' not in json.dumps(row)


def test_live_event_uses_captured_runtime_version_not_spool_time_or_ui_metadata(session_reporter, monkeypatch):
    _, reporter = session_reporter
    from core import version
    reporter.consent(True)
    assert reporter.capture('generation_failed', app_version='9.9.9')
    monkeypatch.setattr(version, 'APP_VERSION', '0.5.9-beta.2')
    reporter._tick()
    row, = saved(reporter)
    assert row['props']['app_version'] == '0.5.8-beta.1'


def test_marker_retires_before_blocked_application_teardown_but_errors_still_spool(session_reporter, monkeypatch):
    reports, reporter = session_reporter
    import main
    reporter.consent(True)
    reporter._tick()
    marker = reporter.root / 'session.json'
    assert marker.exists()
    monkeypatch.setattr(reports, 'initialize', lambda: None)

    async def exercise():
        yielded = asyncio.Event()
        finish = asyncio.Event()
        teardown_entered = asyncio.Event()
        release_teardown = asyncio.Event()
        @asynccontextmanager
        async def application(_):
            try:
                yield
            finally:
                teardown_entered.set()
                await release_teardown.wait()
                assert reports.capture('operation_failed', stage='other', error=RuntimeError('private teardown'))
        monkeypatch.setattr(main, '_application_lifespan', application)
        async def serve():
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                yielded.set()
                await finish.wait()
        task = asyncio.create_task(serve())
        await asyncio.wait_for(yielded.wait(), 2)
        finish.set()
        try:
            await asyncio.wait_for(teardown_entered.wait(), 2)
            retired_before_wait = not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('clean') is True
            reporter._tick()  # A still-running diagnostic worker must not re-arm it.
            retired_after_tick = not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('clean') is True
        finally:
            release_teardown.set()
            await asyncio.wait_for(task, 2)
        assert retired_before_wait, 'intentional shutdown still looks like a crash during teardown waits'
        assert retired_after_tick, 'diagnostic worker recreated a retired session marker'
        row, = saved(reporter)
        assert row['props']['category'] == 'operation_failed'
        assert 'private teardown' not in json.dumps(row)
    asyncio.run(exercise())


def test_authorized_startup_shutdown_intent_retires_diagnostics_without_stopping_capture(session_reporter, monkeypatch):
    reports, reporter = session_reporter
    from core import run_sentinel
    from fastapi.testclient import TestClient
    from main import app
    reporter.consent(True)
    reporter._tick()
    marker = reporter.root / 'session.json'
    monkeypatch.setattr(run_sentinel, 'clear_sentinel', lambda: True)
    monkeypatch.setenv('OMNIVOICE_SERVER_MODE', '1')
    denied = TestClient(app, client=('203.0.113.9', 50000)).post('/system/shutdown-intent')
    assert denied.status_code == 403
    assert marker.exists()
    allowed = TestClient(app, client=('127.0.0.1', 50000)).post('/system/shutdown-intent')
    assert allowed.status_code == 200 and allowed.json()['prepared'] is True
    assert not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('clean') is True
    reporter._tick()
    assert not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('clean') is True
    assert reports.capture('operation_failed', error=RuntimeError('private teardown'))
    reporter._tick()
    assert len(saved(reporter)) == 1


def test_prepare_shutdown_does_not_initialize_or_create_a_profile(session_reporter, monkeypatch, tmp_path):
    reports, _ = session_reporter
    monkeypatch.setattr(reports, '_reporter', None)
    reports.prepare_shutdown()
    assert reports._reporter is None
    assert not (tmp_path / 'diagnostics').exists()
