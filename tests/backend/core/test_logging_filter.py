"""Tests for backend/core/logging_filter.py — AUTH-05 + T-01-02 (info-disclosure
via logs). The HFTokenRedactor strips `hf_[A-Za-z0-9]{30,}` substrings from
both `record.msg` (string format strings) and `record.args` (per-element)
so no real HF token can land in a runtime log, error toast, or traceback.
"""
import logging

import pytest


VALID_TOKEN = "hf_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"  # 43-char token
ANOTHER_TOKEN = "hf_QWERTYUIOPasdfghjklZXCVBNM0123456789xyzAB"


@pytest.fixture
def redactor_logger():
    """A logger with HFTokenRedactor installed, isolated from the rest of
    the logging tree so other tests don't see the filter."""
    from core.logging_filter import HFTokenRedactor

    logger = logging.getLogger("omnivoice.test_redactor")
    logger.setLevel(logging.DEBUG)
    # Strip any leftover filters from a prior test run.
    for f in list(logger.filters):
        logger.removeFilter(f)
    logger.addFilter(HFTokenRedactor())
    return logger


def test_redacts_msg_substring(redactor_logger, caplog):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("download failed for %s while reading model", VALID_TOKEN)
    text = caplog.records[0].getMessage()
    assert VALID_TOKEN not in text
    assert "hf_***REDACTED***" in text


def test_redacts_args_tuple(redactor_logger, caplog):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("token=%s, source=%s", VALID_TOKEN, "env")
    msg = caplog.records[0].getMessage()
    assert VALID_TOKEN not in msg
    # `token=` is also a URL-credential name, so the value may carry either marker.
    assert "token=hf_***REDACTED***" in msg or "token=***REDACTED***" in msg
    assert "source=env" in msg


def test_passes_non_string_args(redactor_logger, caplog):
    """Numeric args must pass through unchanged — the filter must not raise
    on non-string types."""
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("value=%d count=%d", 42, 7)
    msg = caplog.records[0].getMessage()
    assert "value=42 count=7" == msg


def test_short_hf_string_not_redacted(redactor_logger, caplog):
    """Literal strings shorter than the 30-char tail (e.g. 'hf_short')
    are NOT real tokens and must NOT be redacted — that would clobber
    error messages, file paths, and arbitrary log content like `hf_hub` or
    `hf_pipeline_load`."""
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("using hf_hub for downloads, set hf_token please")
    msg = caplog.records[0].getMessage()
    assert "hf_hub" in msg
    assert "hf_token" in msg


def test_redacts_multiple_tokens(redactor_logger, caplog):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("old=%s new=%s", VALID_TOKEN, ANOTHER_TOKEN)
    msg = caplog.records[0].getMessage()
    assert VALID_TOKEN not in msg
    assert ANOTHER_TOKEN not in msg
    assert msg.count("hf_***REDACTED***") == 2


def test_install_filter_is_idempotent():
    """Calling install_redaction_filter() twice must not double-attach the
    same filter to the root logger."""
    from core.logging_filter import HFTokenRedactor, install_redaction_filter

    root = logging.getLogger()
    # Clean up any from prior tests.
    for f in list(root.filters):
        if isinstance(f, HFTokenRedactor):
            root.removeFilter(f)
    install_redaction_filter(root)
    install_redaction_filter(root)
    redactors = [f for f in root.filters if isinstance(f, HFTokenRedactor)]
    assert len(redactors) == 1


def test_routine_health_filter_only_drops_successful_liveness_access_lines():
    from core.logging_filter import RoutineHealthAccessFilter

    access_filter = RoutineHealthAccessFilter()

    def record(method="GET", path="/health", status=200):
        return logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1234", method, path, "1.1", status),
            None,
        )

    assert access_filter.filter(record()) is False
    assert access_filter.filter(record(path="/health?detail=1")) is False
    assert access_filter.filter(record(status=503)) is True
    assert access_filter.filter(record(method="POST")) is True
    assert access_filter.filter(record(path="/system/info")) is True


def test_asyncio_transport_filter_only_drops_expected_pipe_teardown():
    from core.logging_filter import RoutineAsyncioTransportFilter

    transport_filter = RoutineAsyncioTransportFilter()

    def record(message, error, level=logging.ERROR):
        return logging.LogRecord(
            "asyncio",
            level,
            __file__,
            1,
            message,
            (),
            (type(error), error, None),
        )

    callback = "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"
    assert transport_filter.filter(record(callback, ConnectionResetError(10054, "reset"))) is False
    assert transport_filter.filter(record(callback, BrokenPipeError(32, "broken pipe"))) is False
    assert transport_filter.filter(record("Task exception was never retrieved", BrokenPipeError())) is True
    assert transport_filter.filter(record(callback, RuntimeError("real callback failure"))) is True


def test_asyncio_transport_filter_drops_routine_socket_send_warning():
    from core.logging_filter import RoutineAsyncioTransportFilter

    transport_filter = RoutineAsyncioTransportFilter()
    record = logging.LogRecord(
        "asyncio",
        logging.WARNING,
        __file__,
        1,
        "socket.send() raised exception.",
        (),
        None,
    )
    assert transport_filter.filter(record) is False


def test_redacts_url_credentials_in_message_args_and_traceback(redactor_logger, caplog):
    import sys

    caplog.set_level(logging.DEBUG, logger="omnivoice.test_redactor")
    try:
        raise RuntimeError("GET http://u:hunter2secret@host/x?api_key=TOPSECRET99")
    except RuntimeError:
        redactor_logger.error("request %s", "/x?pin=424242&ok=1", exc_info=sys.exc_info())
    text = caplog.text
    for secret in ("TOPSECRET99", "424242", "hunter2secret"):
        assert secret not in text
    assert "pin=" in text and "ok=1" in text and "RuntimeError" in text


def test_install_attaches_to_uvicorn_error_logger():
    from core.logging_filter import HFTokenRedactor, install_redaction_filter

    # install_redaction_filter() also touches the root handlers, which include
    # pytest's session-wide capture handlers: restore them so the filter does
    # not leak into later tests' caplog output.
    owners = [logging.getLogger(), logging.getLogger("uvicorn.error"), *logging.getLogger().handlers]
    saved = [(owner, list(owner.filters)) for owner in owners]
    try:
        install_redaction_filter()
        install_redaction_filter()
        uvicorn_error = logging.getLogger("uvicorn.error")
        filters = [f for f in uvicorn_error.filters if isinstance(f, HFTokenRedactor)]
        assert len(filters) == 1
    finally:
        for owner, filters in saved:
            owner.filters[:] = filters


def test_a_credential_in_the_template_is_redacted_without_breaking_formatting(redactor_logger, caplog):
    caplog.set_level(logging.DEBUG, logger="omnivoice.test_redactor")
    redactor_logger.error("GET /x?pin=%s&ok=1", "424242")
    redactor_logger.error("GET /x?token=%(tok)s&ok=%(ok)s", {"tok": "SECRETVALUE9", "ok": 1})
    text = caplog.text
    assert "424242" not in text and "SECRETVALUE9" not in text
    assert text.count("ok=1") == 2
    assert "--- Logging error ---" not in text


def test_an_unrenderable_record_drops_its_raw_arguments():
    from core.logging_filter import HFTokenRedactor

    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "pin=%s %s", ("424242",), None)
    HFTokenRedactor().filter(record)
    assert record.args is None
    assert "424242" not in record.getMessage()


def test_uvicorn_access_lines_keep_their_arguments_with_the_query_scrubbed():
    from core.logging_filter import HFTokenRedactor, RoutineHealthAccessFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/ws/events?ws_ticket=vs_ws_abcdef123456&x=1", "1.1", 101), None,
    )
    HFTokenRedactor().filter(record)
    assert isinstance(record.args, tuple) and record.args[4] == 101
    assert "vs_ws_abcdef123456" not in record.getMessage()
    health = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/health", "1.1", 200), None,
    )
    HFTokenRedactor().filter(health)
    assert RoutineHealthAccessFilter().filter(health) is False


def test_install_attaches_to_uvicorn_access_logger():
    from core.logging_filter import HFTokenRedactor, install_redaction_filter

    owners = [logging.getLogger(), logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access"), *logging.getLogger().handlers]
    saved = [(owner, list(owner.filters)) for owner in owners]
    try:
        install_redaction_filter()
        assert any(isinstance(f, HFTokenRedactor) for f in logging.getLogger("uvicorn.access").filters)
    finally:
        for owner, filters in saved:
            owner.filters[:] = filters


def test_traceback_drops_env_secret_values_and_home_paths(redactor_logger, caplog, monkeypatch):
    import os
    import sys

    monkeypatch.setenv("VOICESTUDIO_TEST_API_TOKEN", "envsecretvalue123")
    caplog.set_level(logging.DEBUG, logger="omnivoice.test_redactor")
    home = os.path.expanduser("~")
    try:
        raise RuntimeError(f"failed with envsecretvalue123 at {home}/projects/private.wav")
    except RuntimeError:
        redactor_logger.error("boom", exc_info=sys.exc_info())
    assert "envsecretvalue123" not in caplog.text
    assert f"{home}/projects" not in caplog.text
    assert "RuntimeError" in caplog.text

# ── Provider keys the redactor used to miss ──────────────────────────────────
# A vendor that echoes the caller's key inside its own error text is why
# core.scrub.scrub_provider_error exists. That scrubbing was only applied to
# tracebacks, so the same key logged as a message reached the log file (and the
# diagnostics attached to a bug report) verbatim.
PROVIDER_KEYS = [
    ("openai", "sk-" + "proj-A1b2C3d4E5f6G7h8I9j0"),
    ("google", "AIza" + "B1c2D3e4F5g6H7i8J9k0L1m2N3o4P5q6R7s"),
    ("aws", "AKIA" + "Q2W3E4R5T6Y7U8I9J0"),
    ("github", "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"),
]


@pytest.mark.parametrize("label,key", PROVIDER_KEYS)
def test_redacts_provider_key_in_args(redactor_logger, caplog, label, key):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.warning("provider rejected the request: %s", f"invalid key {key}")
    message = caplog.records[0].getMessage()
    assert key not in message
    assert "provider rejected the request" in message


@pytest.mark.parametrize("label,key", PROVIDER_KEYS)
def test_redacts_provider_key_in_plain_message(redactor_logger, caplog, label, key):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.warning(f"translation failed: {key}")
    assert key not in caplog.records[0].getMessage()


def test_keeps_short_identifier_shapes(redactor_logger, caplog):
    """A short look-alike (`sk-learn`) is not a key: the shape needs 20+ chars."""
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("scikit uses sk-learn for the classifier")
    assert caplog.records[0].getMessage() == "scikit uses sk-learn for the classifier"


class _RecordingPattern:
    """Stand-in for one compiled credential pattern."""

    def __init__(self):
        self.calls = 0

    def sub(self, replacement, text):
        self.calls += 1
        return text.replace("sk-abcdefghijklmnopqrstuvwx", replacement)


def test_shape_pass_skips_text_without_a_prefix(monkeypatch):
    """The hot path must not run the shape regexes on ordinary log text."""
    from core import scrub

    pattern = _RecordingPattern()
    monkeypatch.setattr(scrub, "_TOKEN_PATTERNS", (pattern,))
    assert scrub.scrub_credential_shapes("plain log line with no credential") == (
        "plain log line with no credential"
    )
    assert pattern.calls == 0
    key = "sk-abcdefghijklmnopqrstuvwx"
    assert scrub.scrub_credential_shapes(f"key {key}") == f"key {scrub.REDACTED}"
    assert pattern.calls == 1


@pytest.mark.parametrize("sample", [
    "hf_" + "a" * 34,
    "github_pat_" + "a" * 22,
    "ghs_" + "a" * 32,
    "sk-" + "a" * 22,
    "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 8,
    "AIza" + "a" * 35,
    "xoxb-" + "a" * 12,
    "AKIA" + "A" * 16,
    "BEARER " + "a" * 20,
    "Bearer\t" + "a" * 20,
])
def test_the_prefix_gate_never_skips_a_pattern(sample):
    """Every shape the patterns match must pass the cheap gate first."""
    from core.scrub import _TOKEN_PATTERNS, scrub_credential_shapes

    assert any(pattern.search(sample) for pattern in _TOKEN_PATTERNS), sample
    assert sample not in scrub_credential_shapes(f"provider said: {sample}")


@pytest.mark.parametrize("token", ["BEARER " + "a1" * 10, "Bearer\t" + "b2" * 10])
def test_redacts_any_bearer_spelling_in_messages_and_args(redactor_logger, caplog, token):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.warning("upstream said: %s", token)
        redactor_logger.warning(f"upstream said: {token}")
    for record in caplog.records:
        assert token.split()[-1] not in record.getMessage()
