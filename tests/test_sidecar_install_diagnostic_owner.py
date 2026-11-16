"""Real optional-install worker authority through subprocess output cleanup."""
import asyncio
import importlib
import json
import subprocess
import sys
import threading

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401


@pytest.fixture
def install_worker(monkeypatch):
    install = importlib.import_module('services.sidecar_install')
    spec = install.SPECS['indextts2']
    monkeypatch.setattr(install, '_jobs', {})
    monkeypatch.setattr(install, '_healthy', lambda *args, **kwargs: False)
    monkeypatch.setattr(install, '_user_managed_dir', lambda spec: None)
    monkeypatch.setattr(install, 'host_support', lambda spec: (True, ''))
    return install, spec


def _failed_child_with_held_drain(install_worker, monkeypatch, change_consent):
    install, spec = install_worker
    drain_entered = threading.Event()
    child_exited = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    processes = []
    original_spawn = install.spawn_owned
    original_log = install._log
    original_worker = install._run_install

    class ObservedProcess:
        def __init__(self, process):
            self.process = process

        def __getattr__(self, name):
            return getattr(self.process, name)

        def wait(self, **kwargs):
            result = self.process.wait(**kwargs)
            child_exited.set()
            return result

    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        processes.append(process)
        return ObservedProcess(process)

    def log(job, line):
        if line.strip() == 'held-install-drain':
            drain_entered.set()
            assert release.wait(5), 'test did not release failed-child output cleanup'
        original_log(job, line)

    def preflight(spec, job):
        rc = install._run_logged(job, [
            sys.executable, '-c',
            'print("held-install-drain", flush=True); raise SystemExit(7)',
        ], timeout=5)
        assert rc == 7
        raise install._StepError('private install output', 'private remediation')

    def worker(*args):
        try:
            original_worker(*args)
        finally:
            finished.set()

    with monkeypatch.context() as scoped:
        scoped.setattr(install, 'spawn_owned', spawn)
        scoped.setattr(install, '_log', log)
        scoped.setattr(install, '_step_preflight', preflight)
        scoped.setattr(install, '_run_install', worker)
        try:
            assert install.start_install(spec.engine_id)['status'] == 'started'
            assert drain_entered.wait(5), 'actual subprocess did not enter output drain'
            assert child_exited.wait(5), 'actual failing subprocess did not exit'
            assert not finished.is_set()
            change_consent()
        finally:
            release.set()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            assert finished.wait(5), 'install worker did not finish after cleanup'
    assert install.get_status(spec.engine_id)['job']['state'] == 'failed'


@pytest.mark.parametrize('initially_enabled', [False, True])
def test_failed_install_cannot_borrow_consent_during_actual_child_drain(
    reporter, install_worker, monkeypatch, initially_enabled,
):
    if initially_enabled:
        reporter.consent(True)

    def regrant():
        reporter.consent(False)
        reporter.consent(True)

    _failed_child_with_held_drain(install_worker, monkeypatch, regrant)
    assert records(reporter) == []
    # A genuinely new user-started install under the new grant still reports.
    _failed_child_with_held_drain(install_worker, monkeypatch, lambda: None)
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['category'] == 'install_failed'
    assert saved[0]['props']['engine_id'] == 'indextts2'
    assert 'private' not in json.dumps(saved)


def test_same_grant_failed_install_reports_after_actual_child_drain(
    reporter, install_worker, monkeypatch,
):
    reporter.consent(True)
    _failed_child_with_held_drain(install_worker, monkeypatch, lambda: None)
    assert len(records(reporter)) == 1


@pytest.mark.parametrize('initially_enabled', [False, True])
def test_failed_install_remains_private_when_off_after_actual_child_drain(
    reporter, install_worker, monkeypatch, initially_enabled,
):
    if initially_enabled:
        reporter.consent(True)
    _failed_child_with_held_drain(
        install_worker, monkeypatch, lambda: reporter.consent(False),
    )
    assert records(reporter) == []


def test_install_nested_in_known_off_owner_cannot_borrow_enabled_grant(
    reporter, install_worker, monkeypatch,
):
    reports = importlib.import_module('core.diagnostic_reports')
    reporter.consent(True)
    with reports.grant_scope(None):
        _failed_child_with_held_drain(install_worker, monkeypatch, lambda: None)
    assert records(reporter) == []


def test_cancelled_install_worker_does_not_report_failure(
    reporter, install_worker, monkeypatch,
):
    install, spec = install_worker
    reporter.consent(True)
    finished = threading.Event()
    original_worker = install._run_install

    def cancel(*args):
        raise asyncio.CancelledError()

    def worker(*args):
        try:
            original_worker(*args)
        except asyncio.CancelledError:
            pass  # No public install-cancel API: exercise the expected-stop exit.
        finally:
            finished.set()

    monkeypatch.setattr(install, '_step_preflight', cancel)
    monkeypatch.setattr(install, '_run_install', worker)
    assert install.start_install(spec.engine_id)['status'] == 'started'
    assert finished.wait(5)
    assert records(reporter) == []
