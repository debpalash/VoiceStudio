"""Actual app exception handling retains its original HTTP request authority."""
import asyncio
import importlib

import httpx
import pytest
from fastapi.routing import APIRoute

from test_diagnostic_reports import reporter, records  # noqa: F401


@pytest.fixture
def application(monkeypatch):
    main = importlib.import_module('main')
    monkeypatch.setattr(main._startup_progress, 'is_ready', lambda: True)
    return main


async def _request_with_held_failure(application, monkeypatch, change_consent, *, cancel=False):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def endpoint():
        entered.set()
        await release.wait()
        if cancel:
            raise asyncio.CancelledError()
        raise ValueError('private request content')

    # Put the actual fixture endpoint before the production SPA catch-all.
    route = APIRoute('/_diagnostic-owner-audit', endpoint, methods=['GET'])
    monkeypatch.setattr(application.app.router, 'routes', [route, *application.app.router.routes])
    transport = httpx.ASGITransport(app=application.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
        task = asyncio.create_task(client.get('/_diagnostic-owner-audit'))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            change_consent()
        finally:
            release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status_code == 500


@pytest.mark.parametrize('old_grant', [False, True])
def test_http_failure_cannot_borrow_later_consent(reporter, application, monkeypatch, old_grant):
    if old_grant:
        reporter.consent(True)

    def regrant():
        reporter.consent(False)
        reporter.consent(True)

    async def run():
        await _request_with_held_failure(application, monkeypatch, regrant)
        assert records(reporter) == []
        # A separate later request is a new semantic operation.
        await _request_with_held_failure(application, monkeypatch, lambda: None)
        saved = records(reporter)
        assert len(saved) == 1
        assert saved[0]['props']['error_type'] == 'ValueError'

    asyncio.run(run())


def test_http_failure_under_same_grant_reports(reporter, application, monkeypatch):
    reporter.consent(True)
    asyncio.run(_request_with_held_failure(application, monkeypatch, lambda: None))
    assert len(records(reporter)) == 1


def test_http_request_preserves_inherited_known_off(reporter, application, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    reporter.consent(True)

    async def run():
        with reports.grant_scope(None):
            await _request_with_held_failure(application, monkeypatch, lambda: None)

    asyncio.run(run())
    assert records(reporter) == []


def test_cancelled_http_request_does_not_report(reporter, application, monkeypatch):
    reporter.consent(True)
    asyncio.run(_request_with_held_failure(application, monkeypatch, lambda: None, cancel=True))
    assert records(reporter) == []
