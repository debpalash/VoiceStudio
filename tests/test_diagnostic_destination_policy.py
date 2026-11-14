"""Diagnostic consent never authorizes plaintext non-loopback collection URLs."""
import importlib
import json

import pytest


@pytest.fixture
def destination_reporter(monkeypatch, tmp_path):
    reports = importlib.import_module('core.diagnostic_reports')
    from core import analytics, analytics_transport, config, prefs
    for key in ('OMNIVOICE_DIAGNOSTICS_DIR', 'OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(prefs, '_PREFS_PATH', str(tmp_path / 'prefs.json'))
    transports = []
    class Transport:
        def __init__(self, destination, *_args, **_kwargs):
            transports.append(destination)
        def capture(self, *_args, **_kwargs):
            return True
        def shutdown(self):
            pass
    monkeypatch.setattr(analytics_transport, 'SupermetryTransport', Transport)
    monkeypatch.setattr(analytics, '_destination', lambda: {})
    reporter = reports.Reporter()
    reporter.schema = reports._schema()
    yield reporter, transports
    reporter.shutdown()


@pytest.mark.parametrize('field', ['host', 'appUrl'])
@pytest.mark.parametrize('host', ['collector.example', '192.168.1.2', '127.0.0.2', 'localhost.', 'localtest.me', '[::ffff:127.0.0.1]'])
def test_diagnostics_refuses_plaintext_except_exact_loopback_literals(destination_reporter, monkeypatch, field, host):
    reporter, transports = destination_reporter
    from core import analytics
    destination = {'site': 'fixture123', 'host': 'https://collector.example', 'appUrl': 'https://app.example'}
    destination[field] = f'http://{host}'
    monkeypatch.setattr(analytics, '_destination', lambda: destination)
    # The analytics URL contract deliberately remains unchanged.
    assert analytics.valid_url(destination[field], origin=field == 'host')
    assert reporter.status()['available'] is False
    reporter.consent(True)
    reporter.capture('generation_failed')
    reporter._tick()
    assert transports == []


@pytest.mark.parametrize('host,app_url', [
    ('https://collector.example', 'https://app.example'),
    ('http://localhost:3456', 'http://127.0.0.1:3000'),
    ('http://127.0.0.1:3456', 'http://[::1]:3000'),
    ('http://[::1]:3456', 'http://localhost:3000'),
])
def test_https_and_literal_loopback_development_destinations_work(destination_reporter, monkeypatch, host, app_url):
    reporter, transports = destination_reporter
    from core import analytics
    destination = {'site': 'fixture123', 'host': host, 'appUrl': app_url}
    monkeypatch.setattr(analytics, '_destination', lambda: destination)
    assert reporter.status()['available'] is True
    reporter.consent(True)
    reporter._tick()
    assert transports == [destination]


def test_managed_desktop_inbox_never_resolves_a_python_destination(destination_reporter, monkeypatch, tmp_path):
    _, transports = destination_reporter
    reports = importlib.import_module('core.diagnostic_reports')
    from core import analytics
    root = tmp_path / 'managed'
    root.mkdir()
    (root / 'consent.json').write_text(json.dumps({'version': 1, 'enabled': True, 'epoch': 'a' * 32,
        'profile_id': 'install_' + 'b' * 32}), encoding='utf-8')
    monkeypatch.setenv('OMNIVOICE_DIAGNOSTICS_DIR', str(root))
    monkeypatch.setattr(analytics, '_destination', lambda: pytest.fail('managed Python must not resolve transport settings'))
    managed = reports.Reporter()
    managed.schema = reports._schema()
    try:
        managed._tick()
        assert managed.status()['available'] is True
        assert managed.capture('generation_failed')
        managed._tick()
        assert len(list(managed.directory.glob('*.json'))) == 1
        assert transports == []
    finally:
        managed.shutdown()
