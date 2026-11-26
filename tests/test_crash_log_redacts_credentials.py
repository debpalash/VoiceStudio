"""Credential query values must not reach the files a user can read or paste.

The backend accepts three credentials in the query string: `?api_key=` (master
key, `core/auth.py`), `?pin=` (share PIN, `NetworkAccessMiddleware`) and
`?ws_ticket=` (single-use WebSocket ticket). `global_exception_handler` writes
`request.url` into `crash_log.txt` and the runtime log, and the access log writes
the path too, so a request that carries one of those values leaves it in plain
text in a file Settings > Logs shows and users paste into issues.

`core/scrub.py` already owns the redaction policy (and the diagnostic bundle
applies it on export). These tests pin it to the write path as well.
"""
from __future__ import annotations

import logging

_PIN = "424242"
_API_KEY = "S3CRETKEY123"


def test_crash_log_and_runtime_log_redact_credential_query_values(
    tmp_path, monkeypatch, caplog
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod
    from core import error_journal

    crash = tmp_path / "crash.log"
    monkeypatch.setattr(main_mod, "CRASH_LOG_PATH", str(crash))
    monkeypatch.setattr(
        error_journal, "record", lambda *args, **kwargs: {"error_class": "RuntimeError"}
    )

    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    app.add_exception_handler(Exception, main_mod.global_exception_handler)
    with caplog.at_level(logging.ERROR):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/boom?api_key={_API_KEY}&pin={_PIN}")

    assert response.status_code == 500

    written = crash.read_text(encoding="utf-8")
    # The path stays: it is what makes the log useful.
    assert "/boom" in written
    assert _API_KEY not in written
    assert _PIN not in written

    assert _API_KEY not in caplog.text
    assert _PIN not in caplog.text


def test_crash_log_and_runtime_log_redact_a_credential_inside_the_traceback(
    tmp_path, monkeypatch, caplog
):
    """The exception message travels in the traceback, not just in the URL argument.

    A route that raises `RuntimeError(str(request.url))` puts the credential in
    the formatted traceback, so scrubbing only the log argument would leave the
    value in both files.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod
    from core import error_journal

    crash = tmp_path / "crash.log"
    monkeypatch.setattr(main_mod, "CRASH_LOG_PATH", str(crash))
    monkeypatch.setattr(
        error_journal, "record", lambda *args, **kwargs: {"error_class": "RuntimeError"}
    )

    app = FastAPI()

    @app.get("/boom-traceback")
    def boom():
        raise RuntimeError(
            f"failed for http://127.0.0.1:3900/boom-traceback?api_key={_API_KEY}&pin={_PIN}"
        )

    app.add_exception_handler(Exception, main_mod.global_exception_handler)
    with caplog.at_level(logging.ERROR):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/boom-traceback")

    assert response.status_code == 500

    written = crash.read_text(encoding="utf-8")
    assert "/boom-traceback" in written
    assert _API_KEY not in written
    assert _PIN not in written

    assert _API_KEY not in caplog.text
    assert _PIN not in caplog.text


def test_ws_ticket_is_redacted_like_the_other_query_credentials():
    from core.scrub import scrub_text

    scrubbed = scrub_text("http://127.0.0.1:3900/ws/events?ws_ticket=vs_ws_abcdef123456")
    assert "vs_ws_abcdef123456" not in scrubbed
    assert "ws_ticket=" in scrubbed