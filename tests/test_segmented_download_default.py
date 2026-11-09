import httpx
import pytest


def _download_module():
    from api.routers.setup import download
    return download


def test_segmented_download_default_and_env_values(monkeypatch):
    module = _download_module()
    monkeypatch.delenv("OMNIVOICE_SEGMENTED_DOWNLOAD", raising=False)
    assert module._segmented_enabled() is True
    monkeypatch.setenv("OMNIVOICE_SEGMENTED_DOWNLOAD", "0")
    assert module._segmented_enabled() is False
    for value in ("1", "true", "on", "yes"):
        monkeypatch.setenv("OMNIVOICE_SEGMENTED_DOWNLOAD", value)
        assert module._segmented_enabled() is True


def test_segmented_retry_policy():
    module = _download_module()
    dropped = httpx.RemoteProtocolError("peer closed connection")
    for attempt in (1, 2, 3):
        assert module._segmented_retry_plan(dropped, attempt, 5) == (False, True)
    assert module._segmented_retry_plan(dropped, 4, 5) == (True, True)
    assert module._segmented_retry_plan(dropped, 5, 5) == (True, False)
    assert module._segmented_retry_plan(dropped, 1, 1) == (True, False)
    assert module._segmented_retry_plan(ValueError("bad data"), 1, 5) == (True, False)


def test_segmented_retry_log_phrases_are_truthful():
    module = _download_module()
    active = module._segmented_retry_log_phrase(False, True)
    exhausted = module._segmented_retry_log_phrase(True, True)
    fallback = module._segmented_retry_log_phrase(True, False)
    assert len({active, exhausted, fallback}) == 3
    assert "now" not in exhausted.lower()
    assert "now" in fallback.lower()
