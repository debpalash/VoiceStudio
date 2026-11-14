"""Delayed UI events cannot borrow a replacement consent grant."""
import importlib
import json

import pytest

from test_diagnostic_reports import reporter  # noqa: F401

EVENT = {"source": "renderer", "category": "operation_failed", "stage": "other"}


def test_unbound_ui_failure_cannot_borrow_later_opt_in(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    reporter.consent(False)
    reporter.consent(True)
    assert not reports.capture_from_ui(EVENT), "An unbound old UI event borrowed the replacement grant"


def test_current_owner_token_is_required_and_never_becomes_event_metadata(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    old = reporter.status().get("grant_epoch")
    assert old == reporter.epoch
    reporter.consent(False)
    assert reporter.status().get("grant_epoch") is None
    reporter.consent(True)
    fresh = reporter.status()["grant_epoch"]
    assert fresh != old
    assert not reports.capture_from_ui({**EVENT, "expected_epoch": old})
    assert reports.capture_from_ui({**EVENT, "expected_epoch": fresh})
    assert reporter.capture("engine_failed"), "Fresh internal owners must keep working"
    reporter._tick()
    saved = reporter._files()
    assert len(saved) == 2
    assert all("expected_epoch" not in row["props"] and "grant_epoch" not in row["props"] for row in saved)
    assert all(row["epoch"] == fresh for row in saved)
    with pytest.raises(ValueError):
        reports.capture_from_ui({**EVENT, "expected_epoch": "private text"})


def test_settings_http_capture_binds_the_current_owner_grant(reporter, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.settings import router
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    try:
        reporter.consent(True)
        old = client.get("/api/settings/diagnostics").json()["grant_epoch"]
        reporter.consent(False)
        reporter.consent(True)
        fresh = client.get("/api/settings/diagnostics").json()["grant_epoch"]
        assert fresh != old
        for control in ({}, {"expected_epoch": old}):
            result = client.post("/api/settings/diagnostics/event", json={**EVENT, **control})
            assert result.status_code == 200 and result.json() == {"sent": False}
        result = client.post("/api/settings/diagnostics/event", json={**EVENT, "expected_epoch": fresh})
        assert result.status_code == 200 and result.json() == {"sent": True}
        result = client.post("/api/settings/diagnostics/event", json={**EVENT, "expected_epoch": "not-a-grant"})
        assert result.status_code == 422
        reporter._tick()
        saved, = reporter._files()
        assert saved["epoch"] == fresh
        assert "expected_epoch" not in saved["props"]
    finally:
        client.close()
