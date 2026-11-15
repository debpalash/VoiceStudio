"""The real background preload owns consent across its awaited load leaf."""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401


@pytest.fixture
def preload(monkeypatch):
    manager = importlib.import_module('services.model_manager')
    backend = importlib.import_module('services.tts_backend')
    caps = importlib.import_module('core.device_caps')
    monkeypatch.setattr(manager, 'model', None)
    monkeypatch.setattr(manager, '_model_lock', asyncio.Lock())
    monkeypatch.setattr(backend, 'active_backend_id', lambda: 'omnivoice')
    monkeypatch.setattr(caps, 'detect_host_caps', lambda: SimpleNamespace(family='cpu'))
    monkeypatch.setattr(manager, '_ensure_omnivoice_licence', lambda: None)
    monkeypatch.setattr(manager, 'resolve_omnivoice_checkpoint', lambda: 'test')
    monkeypatch.setattr(manager, '_checkpoint_in_local_cache', lambda _: True)
    return manager


@pytest.mark.parametrize('old_grant', [False, True])
def test_preload_delayed_failure_cannot_borrow_replacement_grant(
    reporter, preload, monkeypatch, old_grant,
):
    if old_grant:
        reporter.consent(True)

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_load():
            entered.set()
            await release.wait()
            raise ImportError('private installed model path')

        monkeypatch.setattr(preload, '_load_model_with_timeout', held_load)
        task = asyncio.create_task(preload.preload_model())
        await entered.wait()
        reporter.consent(False)
        reporter.consent(True)
        release.set()
        await task
        assert records(reporter) == []
        # A new independent warm-up may use the newly explicit choice.
        await preload.preload_model()
        saved = records(reporter)
        assert len(saved) == 1
        assert saved[0]['props']['category'] == 'model_load_failed'
        assert saved[0]['props']['error_type'] == 'ImportError'

    asyncio.run(run())


def test_preload_preserves_nested_known_off_authority(reporter, preload, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    reporter.consent(True)
    leaf_authority = []

    async def fail():
        leaf_authority.append(reports.inherited_grant())
        raise ImportError('private model path')

    monkeypatch.setattr(preload, '_load_model_with_timeout', fail)

    async def run():
        with reports.grant_scope(None):
            await preload.preload_model()

    asyncio.run(run())
    assert leaf_authority == [None]
    assert records(reporter) == []


def test_preload_same_grant_reports_and_binds_nested_leaf(reporter, preload, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    reporter.consent(True)
    leaf_authority = []

    async def fail():
        leaf_authority.append(reports.inherited_grant())
        raise ValueError('private model path')

    monkeypatch.setattr(preload, '_load_model_with_timeout', fail)
    asyncio.run(preload.preload_model())
    assert leaf_authority == [reporter.epoch]
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]['props']['error_type'] == 'ValueError'


def test_cancelled_preload_is_not_a_failure_report(reporter, preload, monkeypatch):
    reporter.consent(True)

    async def cancel():
        raise asyncio.CancelledError()

    monkeypatch.setattr(preload, '_load_model_with_timeout', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(preload.preload_model())
    assert records(reporter) == []
