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
    assert "hf_***REDACTED***" in msg
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

    install_redaction_filter()
    install_redaction_filter()
    filters = [f for f in logging.getLogger("uvicorn.error").filters if isinstance(f, HFTokenRedactor)]
    assert len(filters) == 1
