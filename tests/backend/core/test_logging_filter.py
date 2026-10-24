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


@pytest.mark.parametrize("json_output", [False, True])
def test_rendered_output_redacts_nested_values_and_tracebacks(json_output):
    import io
    import json
    from core.logging_filter import install_redaction_filter

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            return json.dumps({
                "message": record.getMessage(),
                "exception": self.formatException(record.exc_info) if record.exc_info else None,
            })

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.Logger("isolated-token-output", logging.DEBUG)
    logger.addHandler(handler)
    install_redaction_filter(logger)
    install_redaction_filter(logger)
    try:
        raise ValueError("download rejected " + VALID_TOKEN)
    except ValueError:
        logger.exception("context=%s count=%d", {"nested": [VALID_TOKEN]}, 7)
    output = stream.getvalue()
    assert VALID_TOKEN not in output
    assert "hf_***REDACTED***" in output
    assert "ValueError" in output
    assert "count=7" in output
    if json_output:
        assert "ValueError" in json.loads(output)["exception"]


def test_nested_redaction_preserves_mapping_formatting(redactor_logger, caplog):
    with caplog.at_level(logging.INFO, logger=redactor_logger.name):
        redactor_logger.info("value=%(value)s count=%(count)d", {"value": [VALID_TOKEN], "count": 7})
    message = caplog.records[0].getMessage()
    assert VALID_TOKEN not in message
    assert "count=7" in message


def test_main_json_logging_setup_keeps_final_redaction():
    """Execute the actual logging-only startup code without loading ML engines."""
    import ast
    import io
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from core.logging_filter import install_redaction_filter

    source = Path(__file__).resolve().parents[3] / "backend" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    formatter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_JsonFormatter")
    setup = next(node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "_json_logs")
    stream = io.StringIO()
    logger = logging.Logger("isolated-main-json", logging.DEBUG)
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    install_redaction_filter(logger)
    namespace = {"logging": logging}
    exec(compile(ast.Module(body=[formatter], type_ignores=[]), str(source), "exec"), namespace)
    namespace.update(logging=SimpleNamespace(getLogger=lambda: logger), _json_logs=True, install_redaction_filter=lambda: install_redaction_filter(logger))
    exec(compile(ast.Module(body=[setup], type_ignores=[]), str(source), "exec"), namespace)
    try:
        raise ValueError(VALID_TOKEN)
    except ValueError:
        logger.exception("failed")
    output = json.loads(stream.getvalue())
    assert VALID_TOKEN not in output["exc"]
    assert "ValueError" in output["exc"]
    assert output["msg"] == "failed"


def test_access_handler_replacement_preserves_token_redaction():
    import io
    from uvicorn.logging import AccessFormatter
    from core.logging_filter import HFTokenRedactor, install_access_log_filter

    logger = logging.Logger("owned.uvicorn.access", level=logging.INFO)
    install_access_log_filter(logger)
    # Uvicorn installs/replaces its access handler after main's initial setup.
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(AccessFormatter(
        fmt='%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        use_colors=False,
    ))
    logger.handlers = [handler]
    logger.info('%s - "%s %s HTTP/%s" %d', '127.0.0.1:1', 'GET',
                '/missing?token=' + VALID_TOKEN, '1.1', 404)
    assert VALID_TOKEN not in output.getvalue()
    assert '/missing?token=hf_***REDACTED***' in output.getvalue()
    assert '404' in output.getvalue()
    install_access_log_filter(logger)
    assert sum(isinstance(f, HFTokenRedactor) for f in logger.filters) == 1
    logger.info('%s - "%s %s HTTP/%s" %d', '127.0.0.1:1', 'GET', '/health', '1.1', 200)
    assert '/health' not in output.getvalue()
