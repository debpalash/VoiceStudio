"""Crash recovery retains its version; intentional teardown retires only the marker."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import json
from types import SimpleNamespace

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
    marker = {'version': 1, 'epoch': reporter.epoch, 'pid': 42, 'started_at': 1}
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
