"""Desktop's two durable stages share the same total storage budget."""
import json
import importlib

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401


@pytest.mark.parametrize("managed,expected", [(False, 40), (True, 32)])
def test_managed_inbox_reserves_half_the_desktop_budget(
    reporter, monkeypatch, tmp_path, managed, expected,
):
    reports = importlib.import_module("core.diagnostic_reports")
    instance = reporter
    if managed:
        root = tmp_path / "managed"
        root.mkdir()
        monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DIR", str(root))
        instance = reports.Reporter()
        instance.schema = reports._schema()
        (root / "consent.json").write_text(json.dumps({
            "version": 1, "enabled": True, "epoch": "a" * 32,
            "profile_id": "install_" + "b" * 32,
        }), encoding="utf-8")
        instance._tick()
    else:
        instance.consent(True)
    try:
        for line in range(1, 41):
            assert instance.capture("engine_failed", stage="engine",
                                    frames=f"backend/main.py:{line}")
        saved = records(instance)
        assert len(saved) == expected
        assert instance.dropped == 40 - expected
        assert instance.transport is None
    finally:
        if managed:
            instance.shutdown()
