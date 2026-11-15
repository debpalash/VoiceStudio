"""Actual Python thread hooks retain bound work authority after scope unwind."""
from concurrent.futures import ThreadPoolExecutor
import gc
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import weakref

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401


_MAIN_THREAD_PROBE = r'''
import json
from pathlib import Path
import sys
from core import diagnostic_reports as reports, prefs

directory, transition = Path(sys.argv[1]), sys.argv[2]
prefs._PREFS_PATH = str(directory / 'prefs.json')
reports._diagnostic_destination = lambda: None
reporter = reports.Reporter()
reporter.schema = reports._schema()
reports._reporter = reporter
if transition in ('regrant', 'same-grant', 'unbound-regrant', 'unbound-same-grant'):
    reporter.consent(True)

previous_calls = 0
def previous(kind, error, trace):
    global previous_calls
    previous_calls += 1
    context_before = reports._grant_authority.get() is reports._GRANT_UNSET
    reporter._tick()
    saved = [json.loads(path.read_text(encoding='utf-8')) for path in reporter.directory.glob('*.json')]
    result = {
        'records': saved,
        'previous_calls': previous_calls,
        'context_unset': context_before and reports._grant_authority.get() is reports._GRANT_UNSET,
        'pending_cleared': getattr(reports._thread_fault_authority, 'pending', None) is None,
        'error_type': kind.__name__,
    }
    (directory / 'hook-result.json').write_text(json.dumps(result), encoding='utf-8')

sys.excepthook = previous
reports._hooks_installed = False
reports._install_hooks()

def failure():
    try:
        raise ValueError('private main-thread failure before cleanup')
    finally:
        if transition not in ('same-grant', 'unbound-same-grant'):
            reporter.consent(False)
            reporter.consent(True)

if transition in ('fresh-bound', 'fresh-unbound'):
    # A caught prior error must not contaminate genuinely later work. Retain
    # its identity in this fixture until the new exception exists, avoiding
    # allocator-ID reuse without requiring production to retain the Error.
    older = None
    try:
        reports.bind_callable(failure)()
    except ValueError as error:
        older = error
    def fresh():
        raise TypeError('private later main-thread failure')
    if transition == 'fresh-bound':
        reports.bind_callable(fresh)()
    else:
        fresh()
elif transition.startswith('unbound-'):
    failure()
else:
    reports.bind_callable(failure)()
'''


@pytest.mark.parametrize('transition', [
    'off-to-on', 'regrant', 'same-grant', 'fresh-bound', 'fresh-unbound',
    'unbound-off-to-on', 'unbound-regrant', 'unbound-same-grant',
])
def test_actual_main_thread_hook_retains_origin_after_cleanup(tmp_path, transition):
    """Let CPython invoke the installed sys hook for a truly uncaught fault."""
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path / 'main-hook'
    directory.mkdir()
    env = {**os.environ, 'PYTHONPATH': str(root / 'backend'), 'OMNIVOICE_DATA_DIR': str(directory),
           'HF_HUB_OFFLINE': '1', 'HF_HOME': str(tmp_path / 'empty-hf'),
           'HF_HUB_CACHE': str(tmp_path / 'empty-hf' / 'hub'),
           'HF_XET_CACHE': str(tmp_path / 'empty-hf' / 'xet'),
           'OMNIVOICE_DISABLE_FILE_LOG': '1'}
    for key in ('OMNIVOICE_DIAGNOSTICS_DIR', 'OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'):
        env.pop(key, None)
    child = subprocess.run([sys.executable, '-c', _MAIN_THREAD_PROBE, str(directory), transition],
                           cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 1, child.stdout + child.stderr
    result = json.loads((directory / 'hook-result.json').read_text(encoding='utf-8'))
    assert result['previous_calls'] == 1 and result['context_unset']
    expected = 1 if transition in ('same-grant', 'fresh-bound') else 0
    assert len(result['records']) == expected, 'main-thread hook borrowed consent without a current operation owner'
    assert result['pending_cleared']
    if expected:
        assert result['records'][0]['props']['error_type'] == result['error_type']


@pytest.fixture
def hooks(reporter, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    completed = []
    monkeypatch.setattr(reports, '_hooks_installed', False)
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    monkeypatch.setattr(threading, 'excepthook', lambda args: completed.append(args.thread.ident))
    reports._install_hooks()
    return reports, completed


def _held_failure(change_consent, hooks, *, owner=None, nested=False):
    reports, completed = hooks
    entered = threading.Event()
    release = threading.Event()

    def fail():
        try:
            raise ValueError('private failure before cleanup')
        finally:
            entered.set()
            assert release.wait(5)

    target = reports.bind_callable(fail) if owner is None else owner(fail)
    if nested:
        inner = target

        def outer():
            try:
                inner()
            finally:
                # Successful nested cleanup must not erase the original fault.
                reports.bind_callable(lambda: None)()

        target = reports.bind_callable(outer)
    thread = threading.Thread(target=target)
    thread.start()
    try:
        assert entered.wait(5)
        change_consent()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert completed == [thread.ident]


@pytest.mark.parametrize('initially_enabled', [False, True])
@pytest.mark.parametrize('nested', [False, True])
def test_thread_hook_cannot_borrow_later_grant_after_failed_cleanup(
    reporter, hooks, initially_enabled, nested,
):
    if initially_enabled:
        reporter.consent(True)

    def regrant():
        reporter.consent(False)
        reporter.consent(True)

    _held_failure(regrant, hooks, nested=nested)
    assert records(reporter) == []
    hooks[1].clear()
    _held_failure(lambda: None, hooks)
    assert len(records(reporter)) == 1  # A genuinely later thread owns grantB.


def test_thread_hook_same_grant_reports_once(reporter, hooks):
    reporter.consent(True)
    _held_failure(lambda: None, hooks, nested=True)
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['error_type'] == 'ValueError'


@pytest.mark.parametrize('transition', ['off-to-on', 'regrant', 'same-grant'])
def test_unbound_thread_fault_stays_local_and_fresh_bound_work_can_report(reporter, hooks, transition):
    if transition != 'off-to-on':
        reporter.consent(True)

    def change():
        if transition != 'same-grant':
            reporter.consent(False)
            reporter.consent(True)

    # An actual Thread target, without bind_callable: no original grant is
    # known at the later exception hook, even if the current choice is ON.
    _held_failure(change, hooks, owner=lambda function: function)
    assert records(reporter) == []
    hooks[1].clear()
    _held_failure(lambda: None, hooks)
    assert len(records(reporter)) == 1


def test_thread_hook_withdrawal_suppresses_old_fault(reporter, hooks):
    reporter.consent(True)
    _held_failure(lambda: reporter.consent(False), hooks)
    assert records(reporter) == []


def test_explicit_inner_off_owner_survives_outer_enabled_scope(reporter, hooks):
    reports, _ = hooks
    reporter.consent(True)
    _held_failure(lambda: None, hooks, owner=lambda fn: reports.bind_callable(fn, None), nested=True)
    assert records(reporter) == []


def test_new_inner_unit_can_report_under_its_own_grant(reporter, hooks):
    reports, completed = hooks
    reporter.consent(True)
    epoch = reporter.epoch

    def fail():
        raise TypeError('private fresh inner failure')

    inner = reports.bind_callable(fail, epoch)
    # A new explicitly owned inner unit has its own grant, even when the older
    # outer operation began OFF. Propagating it cannot replace its authority.
    thread = threading.Thread(target=reports.bind_callable(inner, None))
    thread.start()
    thread.join(5)
    assert completed == [thread.ident]
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['error_type'] == 'TypeError'


@pytest.mark.parametrize('stop', [KeyboardInterrupt, SystemExit])
def test_expected_thread_stop_is_not_reported(reporter, hooks, stop):
    reports, completed = hooks
    reporter.consent(True)

    def cancel():
        raise stop()

    thread = threading.Thread(target=reports.bind_callable(cancel))
    thread.start()
    thread.join(5)
    assert completed == [thread.ident]
    assert records(reporter) == []


def test_executor_caught_error_does_not_report_and_fresh_reused_work_reports(reporter, hooks):
    reports, completed = hooks
    reporter.consent(True)
    entered = threading.Event()
    release = threading.Event()
    identities = []

    def fail():
        identities.append(threading.get_ident())
        try:
            raise ValueError('private caught executor failure')
        finally:
            entered.set()
            assert release.wait(5)

    def fresh():
        identities.append(threading.get_ident())
        assert reports.inherited_grant() == reporter.epoch
        return reports.capture_failure('generation', error=TypeError('private fresh unit'))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reports.bind_callable(fail))
        try:
            assert entered.wait(5)
            reporter.consent(False)
            reporter.consent(True)
        finally:
            release.set()
        with pytest.raises(ValueError):
            future.result(timeout=5)
        assert records(reporter) == []
        assert completed == []  # Future caught the exception: no thread hook.
        assert executor.submit(reports.bind_callable(fresh)).result(timeout=5)
        assert executor.submit(reports.inherited_grant).result(timeout=5) == reporter.epoch
    assert len(set(identities)) == 1
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['error_type'] == 'TypeError'


def test_caught_nested_error_and_successful_followup_cannot_taint_later_fault(reporter, hooks):
    reports, completed = hooks
    reporter.consent(True)

    def caught():
        raise ValueError('private caught inner failure')

    def successful_outer():
        try:
            reports.bind_callable(caught, None)()
        except ValueError:
            pass

    def target():
        reports.bind_callable(successful_outer)()
        reports.bind_callable(lambda: None)()
        # An unbound synchronous fault is local-only; the cleared old marker
        # cannot supply authority merely because consent is currently ON.
        raise TypeError('private genuinely later unbound failure')

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(5)
    assert completed == [thread.ident]
    assert records(reporter) == []
    completed.clear()
    _held_failure(lambda: None, hooks)
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['error_type'] == 'ValueError'


def test_caught_executor_fault_marker_does_not_retain_exception(reporter, hooks):
    reports, completed = hooks
    references = []

    class PrivateFailure(ValueError):
        pass

    def fail():
        error = PrivateFailure('private executor payload')
        references.append(weakref.ref(error))
        raise error

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reports.bind_callable(fail))
        with pytest.raises(PrivateFailure):
            future.result(timeout=5)
        del future
        # Drain the work item and prove both marker cleanup and ContextVar reset
        # on the same executor thread, without retaining its exception object.
        def fresh():
            return dict(reports._thread_fault_authority.__dict__)

        state = executor.submit(reports.bind_callable(fresh)).result(timeout=5)
        assert state.get('pending') is None
        assert state.get('depth') == 1  # Currently inside this fresh bound call.
        state = executor.submit(fresh).result(timeout=5)
        assert state.get('pending') is None
        assert state.get('depth') == 0
    gc.collect()
    assert references[0]() is None
    assert completed == []
    assert records(reporter) == []
