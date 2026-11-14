"""An eager ASGI startup exception is a current failure, even when propagated."""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter  # noqa: F401


@pytest.mark.parametrize("enabled", [False, True])
def test_propagated_eager_startup_failure_is_reported_only_with_consent(reporter, monkeypatch, enabled):
    main = importlib.import_module("main")
    reports = importlib.import_module("core.diagnostic_reports")
    from core import run_sentinel
    reporter.consent(enabled)
    monkeypatch.setenv("OMNIVOICE_STARTUP_WATCHDOG_S", "0")
    monkeypatch.setattr(main, "_EAGER", True)
    monkeypatch.setattr(main, "_phase_a_build", lambda: None)
    monkeypatch.setattr(main, "_phase_a_finalize", lambda: None)
    monkeypatch.setattr(main, "_start_analytics_lifecycle", lambda *_: None)
    monkeypatch.setattr(run_sentinel, "detect_unclean_shutdown", lambda: None)
    monkeypatch.setattr(run_sentinel, "write_sentinel", lambda: None)
    async def failed(_app):
        raise RuntimeError("PRIVATE_STARTUP_CONTENT")
    monkeypatch.setattr(main, "_phase_b", failed)
    async def run():
        previous = lambda *_: None
        asyncio.get_running_loop().set_exception_handler(previous)
        with pytest.raises(RuntimeError, match="PRIVATE_STARTUP_CONTENT"):
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail("Failed startup must not yield")
        assert asyncio.get_running_loop().get_exception_handler() is previous
        assert reports._reporter is None
    asyncio.run(run())
    saved = [json.loads(path.read_text(encoding="utf-8")) for path in reporter.directory.glob("*.json")]
    assert len(saved) == int(enabled)
    if saved:
        assert saved[0]["props"]["category"] == "backend_crash"
        assert saved[0]["props"]["stage"] == "startup"
        assert saved[0]["props"]["reason"] == "launch_failed"
        assert "PRIVATE_STARTUP_CONTENT" not in json.dumps(saved)
