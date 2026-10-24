"""Regression tests for private diagnostics crossing the HTTP boundary."""
from __future__ import annotations

import logging

import pytest

_PRIVATE = (
    "Traceback (most recent call last):\n"
    '  File "/home/alice/private/project.py", line 7\n'
    "RuntimeError: hf_abcdefghijklmnopqrstuvwxyz1234567890"
)


def test_public_failure_logs_only_stable_class_and_returns_fixed_text(caplog):
    from core.public_errors import public_failure

    logger = logging.getLogger("test.response_safety")

    with caplog.at_level(logging.ERROR):
        result = public_failure(
            logger,
            "operation failed",
            RuntimeError(_PRIVATE),
            response="Operation failed; check the backend log for details.",
        )

    assert result == "Operation failed; check the backend log for details."
    assert _PRIVATE not in caplog.text
    assert "class=RuntimeError" in caplog.text
    assert "Traceback" not in result
    assert "/home/alice" not in result
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in result


def test_engine_health_never_returns_engine_owned_diagnostic():
    from core.public_errors import public_engine_health

    assert public_engine_health(False, _PRIVATE) == (
        "Engine unavailable; check the backend log for details."
    )
    assert public_engine_health(True, _PRIVATE) == "Healthy"


def test_recognized_failure_gets_constant_recovery_without_private_text():
    from core.public_errors import public_exception_response

    private = RuntimeError(
        "Using SOCKS proxy from /home/alice/private with "
        "hf_abcdefghijklmnopqrstuvwxyz1234567890 but socksio is not installed"
    )
    payload = public_exception_response(private, fallback="Internal error.")

    assert payload["docs_topic"] == "SOCKS_PROXY_SUPPORT_MISSING"
    assert "unset ALL_PROXY/HTTPS_PROXY" in payload["hint"]
    serialized = str(payload)
    assert "/home/alice" not in serialized
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in serialized


def test_unknown_failure_has_no_diagnostic_derived_guidance():
    from core.public_errors import public_exception_response

    payload = public_exception_response(RuntimeError(_PRIVATE), fallback="Internal error.")
    assert payload == {"detail": "Internal error."}


def test_engine_health_route_logs_but_does_not_return_private_diagnostic(
    monkeypatch, caplog
):
    from api.routers import engines

    class BrokenEngine:
        @classmethod
        def is_available(cls):
            raise RuntimeError(_PRIVATE)

    monkeypatch.setattr(engines, "_resolve_engine_class", lambda _engine_id: BrokenEngine)
    hostile_engine_id = "broken\nFORGED ENGINE LOG"
    with caplog.at_level(logging.WARNING):
        result = engines.engine_health(hostile_engine_id)

    assert result["ok"] is False
    assert result["message"] == "Engine unavailable; check the backend log for details."
    assert _PRIVATE not in caplog.text
    assert hostile_engine_id not in caplog.text
    assert "FORGED ENGINE LOG" not in caplog.text
    assert "Traceback" not in result["message"]
    assert "/home/alice" not in result["message"]
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in result["message"]


def test_engine_health_log_names_the_engine_and_failure_class(monkeypatch, caplog):
    """The withheld line still withholds, but it now says what failed (#1866).

    The response tells the user to "check the backend log for details" and
    docs/engines/*.md asks a user diagnosing an unavailable engine to copy that
    engine's log lines. The old line carried neither the engine nor the kind of
    failure, so a log with several engines probed could not answer either.

    The boundary above is unchanged and deliberately so: the id comes off the
    resolved registry class rather than the caller, and the failure is a stable
    class name rather than the exception text — the same shape
    ``core.public_errors.public_failure`` already logs as ``class=...``.
    """
    from api.routers import engines

    class BrokenEngine:
        id = "broken-engine"

        @classmethod
        def is_available(cls):
            raise RuntimeError(_PRIVATE)

    monkeypatch.setattr(engines, "_resolve_engine_class", lambda _engine_id: BrokenEngine)
    with caplog.at_level(logging.WARNING):
        engines.engine_health("broken-engine")

    assert "engine=broken-engine" in caplog.text
    assert "probe=raised:RuntimeError" in caplog.text
    assert caplog.text.count("\n") == 1  # one record, one line — nothing forged
    # Still nothing engine-owned: no message text, no path, no token.
    assert _PRIVATE not in caplog.text
    assert "/home/alice" not in caplog.text
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in caplog.text
    assert "Traceback" not in caplog.text


def test_engine_health_log_flattens_a_newline_bearing_engine_id(monkeypatch, caplog):
    """The id is registry-authored, but the line is still flattened."""
    from api.routers import engines

    class ForgedId:
        id = "evil\nWARNING FORGED ENGINE LOG"

        @classmethod
        def is_available(cls):
            return False, "nope"

    monkeypatch.setattr(engines, "_resolve_engine_class", lambda _engine_id: ForgedId)
    with caplog.at_level(logging.WARNING):
        engines.engine_health("evil")

    assert "FORGED ENGINE LOG" not in caplog.text
    assert "engine=evil-WARNING-FORGED-ENGINE-LOG" in caplog.text
    assert caplog.text.count("\n") == 1


def test_engine_health_log_distinguishes_not_available_from_a_raised_probe(
    monkeypatch, caplog
):
    """`probe=` says what the probe did, and claims nothing more.

    A probe that returns rather than raises reads `returned-unavailable`
    whatever the cause — a package that was never installed and a sidecar that
    died both land here, because SubprocessBackend.health_check() swallows its
    own exceptions by contract. Telling those apart needs structured failure
    metadata from the probes, so the field deliberately does not pretend to.
    """
    from api.routers import engines

    class NotInstalled:
        id = "not-installed"

        @classmethod
        def is_available(cls):
            return False, "voxcpm package not installed."

    monkeypatch.setattr(engines, "_resolve_engine_class", lambda _engine_id: NotInstalled)
    with caplog.at_level(logging.WARNING):
        engines.engine_health("not-installed")

    assert "engine=not-installed" in caplog.text
    assert "probe=returned-unavailable" in caplog.text
    assert "voxcpm package not installed" not in caplog.text


def test_tailscale_enable_failure_keeps_service_output_private(monkeypatch, caplog):
    import asyncio

    from api.routers import system

    private = f"{_PRIVATE}\nTOKEN=private-value"
    monkeypatch.setattr(
        system._tailscale,
        "serve_enable",
        lambda: {"ok": False, "error": private},
    )

    with caplog.at_level(logging.ERROR):
        result = asyncio.run(system.tailscale_enable())

    assert result == {
        "ok": False,
        "error": "Tailscale sharing could not be enabled; check the backend log for details.",
    }
    assert private not in caplog.text
    assert "TOKEN=private-value" not in caplog.text
    assert "/home/alice" not in caplog.text


def test_tailscale_command_exception_logs_only_safe_context(monkeypatch, caplog):
    from services import tailscale
    from core.logging_filter import install_redaction_filter

    # Exercise configured logging without depending on another test importing
    # main, and restore pytest's shared capture handler after this test.
    logger = logging.Logger("owned.tailscale", level=logging.ERROR)
    logger.handlers = [caplog.handler]
    monkeypatch.setattr(tailscale, "logger", logger)
    monkeypatch.setattr(caplog.handler, "formatter", caplog.handler.formatter)
    monkeypatch.setattr(caplog.handler, "filters", list(caplog.handler.filters))
    install_redaction_filter(logger)

    private = f"{_PRIVATE}\nTOKEN=private-value"

    def fail_run(*_args, **_kwargs):
        raise OSError(private)

    monkeypatch.setattr(tailscale.subprocess, "run", fail_run)
    with caplog.at_level(logging.ERROR, logger="omnivoice.tailscale"):
        result = tailscale._run(["tailscale", "serve"])

    assert result == {"ok": False, "error": "tailscale command failed"}
    synthetic_token = "hf_" + "abcdefghijklmnopqrstuvwxyz" + "1234567890"
    assert "Tailscale command failed (OSError)" in caplog.text
    assert synthetic_token not in caplog.text
    assert "TOKEN=private-value" not in caplog.text
    assert "/home/alice" not in caplog.text
    assert private not in str(result)


def test_tailscale_nonzero_output_logs_only_exit_code(monkeypatch, caplog):
    from types import SimpleNamespace

    from services import tailscale

    private = f"{_PRIVATE}\nTOKEN=private-value"
    monkeypatch.setattr(
        tailscale.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=private,
        ),
    )

    with caplog.at_level(logging.ERROR, logger="omnivoice.tailscale"):
        result = tailscale._run(["tailscale", "serve"])

    assert result == {"ok": False, "error": "tailscale command failed"}
    assert "Tailscale command exited with code 1" in caplog.text
    assert "/home/alice" not in caplog.text
    assert "TOKEN=private-value" not in caplog.text
    assert private not in str(result)


def test_wrapped_cache_repair_failure_keeps_constant_recovery_guidance():
    from core.public_errors import public_exception_response

    private = RuntimeError(
        "The TTS model cache for /home/alice/models--private is incomplete and "
        "could not be auto-repaired. hf_abcdefghijklmnopqrstuvwxyz1234567890"
    )
    payload = public_exception_response(private, fallback="Internal error.")
    assert payload["docs_topic"] == "MODEL_CACHE_CORRUPT"
    assert "delete the model's models--<org>--<name> folder" in payload["hint"]
    assert "/home/alice" not in str(payload)
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in str(payload)


def test_global_exception_handler_keeps_private_failure_out_of_response(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod
    from core import error_journal

    monkeypatch.setattr(main_mod, "CRASH_LOG_PATH", str(tmp_path / "crash.log"))
    monkeypatch.setattr(
        error_journal,
        "record",
        lambda *args, **kwargs: {"error_class": "RuntimeError"},
    )
    app = FastAPI()

    @app.get("/private-failure")
    def private_failure():
        raise RuntimeError(f"{_PRIVATE}\nUsing SOCKS proxy but socksio is not installed")

    app.add_exception_handler(Exception, main_mod.global_exception_handler)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/private-failure")

    assert response.status_code == 500
    body = response.json()
    assert body["error_class"] == "RuntimeError"
    assert body["docs_topic"] == "SOCKS_PROXY_SUPPORT_MISSING"
    assert "unset ALL_PROXY/HTTPS_PROXY" in body["hint"]
    assert body["detail"].endswith(body["hint"])
    serialized = response.text
    assert "Traceback" not in serialized
    assert "/home/alice" not in serialized
    assert "hf_abcdefghijklmnopqrstuvwxyz1234567890" not in serialized


@pytest.mark.parametrize("error_kind", ["os", "timeout", "decode"])
def test_tailscale_expected_subprocess_failures_degrade_safely(monkeypatch, caplog, error_kind):
    from services import tailscale
    import subprocess

    if error_kind == "os":
        error = OSError("synthetic unavailable command")
    elif error_kind == "timeout":
        error = subprocess.TimeoutExpired(["tailscale", "serve"], timeout=20)
    else:
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid synthetic output")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(tailscale.subprocess, "run", fail)
    with caplog.at_level(logging.ERROR):
        result = tailscale._run(["tailscale", "serve"])
    assert result == {"ok": False, "error": "tailscale command failed"}
    assert f"Tailscale command failed ({type(error).__name__})" in caplog.text


def test_tailscale_unexpected_programming_error_is_not_hidden(monkeypatch):
    from services import tailscale

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic programming failure")

    monkeypatch.setattr(tailscale.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="synthetic programming failure"):
        tailscale._run(["tailscale", "serve"])
