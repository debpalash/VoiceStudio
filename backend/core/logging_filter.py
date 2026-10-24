"""HuggingFace token redaction filter for Python's logging system.

Mitigates threat T-01-02 (information disclosure via logs). The filter is
attached to the root logger so every emit — backend Python, uvicorn,
fastapi, pyannote, transformers, faster-whisper, etc. — sees the
substitution before any handler formats the record.

The regex `hf_[A-Za-z0-9]{30,}` is conservative on purpose:
  - Real HF tokens are at least 36 characters total (`hf_` + 33+ chars)
  - We keep the 30-char minimum so legitimate non-token strings like
    `hf_hub`, `hf_pipeline_load`, `hf_token_file_path` are NOT clobbered
  - Returning True from `filter()` always lets the record through — we
    only rewrite, never drop
"""
from __future__ import annotations

import logging
import re

REDACTED = "hf_***REDACTED***"

# At least 30 alphanumerics following `hf_` — covers all real HF tokens
# (typically 36-40 chars total) while leaving short matches like `hf_hub`
# alone so the log stays useful for debugging.
_HF_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")


class HFTokenRedactor(logging.Filter):
    """logging.Filter that rewrites HF token substrings in `record.msg`
    and `record.args` before the formatter sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Rewrite the format string itself if it's a str.
        try:
            if isinstance(record.msg, str):
                record.msg = _HF_TOKEN_RE.sub(REDACTED, record.msg)

            # Rewrite per-argument so the formatted final message also
            # comes out clean. logger.info("token=%s", tok).
            if record.args:
                if isinstance(record.args, tuple):
                    new_args = tuple(
                        _HF_TOKEN_RE.sub(REDACTED, a) if isinstance(a, str) else a
                        for a in record.args
                    )
                    record.args = new_args
                elif isinstance(record.args, dict):
                    record.args = {
                        k: (_HF_TOKEN_RE.sub(REDACTED, v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
            # Containers and custom objects stringify only during interpolation.
            # Redact that rendered message without changing numeric formatting.
            message = record.getMessage()
            redacted = _HF_TOKEN_RE.sub(REDACTED, message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            # Never let the filter break the log pipeline. If anything
            # unexpected happens, just let the record pass through —
            # erring on the side of the log being noisy, not silent.
            pass
        return True


class _RedactingFormatter(logging.Formatter):
    """Preserve the handler's formatter, then redact its complete output."""

    def __init__(self, delegate: logging.Formatter | None):
        super().__init__()
        self.delegate = delegate or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return _HF_TOKEN_RE.sub(REDACTED, self.delegate.format(record))


class RoutineHealthAccessFilter(logging.Filter):
    """Drop only successful routine liveness access lines.

    The desktop supervisor probes every two seconds. Startup/not-ready responses
    and every other request remain visible, while the steady-state 200 line no
    longer consumes the small rotating diagnostic log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if not isinstance(args, tuple) or len(args) < 5:
                return True
            _client, method, path, _http_version, status = args[:5]
            return not (
                method == "GET"
                and str(path).partition("?")[0] == "/health"
                and int(status) == 200
            )
        except (TypeError, ValueError):
            return True


class RoutineAsyncioTransportFilter(logging.Filter):
    """Drop only expected socket-close noise from asyncio's transport layer."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            if record.levelno == logging.WARNING and "socket.send() raised exception" in message:
                return False
            exception = record.exc_info[1] if record.exc_info else None
            return not (
                message.startswith(
                    "Exception in callback _ProactorBasePipeTransport._call_connection_lost"
                )
                and isinstance(exception, (BrokenPipeError, ConnectionResetError))
            )
        except Exception:
            return True


def install_redaction_filter(root_logger: logging.Logger | None = None) -> None:
    """Attach a single HFTokenRedactor to the root logger and to every
    existing handler, with final-output redaction. Idempotent — repeated calls do not stack up duplicate
    filters."""
    target = root_logger or logging.getLogger()
    if not any(isinstance(f, HFTokenRedactor) for f in target.filters):
        target.addFilter(HFTokenRedactor())
    # Handlers each have their own filter list. Attach the same redactor
    # to every existing handler so even handler-formatted output is clean.
    for handler in list(target.handlers):
        if not any(isinstance(f, HFTokenRedactor) for f in handler.filters):
            handler.addFilter(HFTokenRedactor())
        # Tracebacks, stack info, and custom formatter fields are rendered only
        # after filters run. Keep their formatting while redacting the final text.
        if not isinstance(handler.formatter, _RedactingFormatter):
            handler.setFormatter(_RedactingFormatter(handler.formatter))


def install_access_log_filter(logger: logging.Logger | None = None) -> None:
    """Install health suppression and token redaction on the access logger."""
    target = logger or logging.getLogger("uvicorn.access")
    install_redaction_filter(target)
    if not any(isinstance(item, RoutineHealthAccessFilter) for item in target.filters):
        target.addFilter(RoutineHealthAccessFilter())


def install_asyncio_transport_filter(logger: logging.Logger | None = None) -> None:
    """Install the expected transport-close filter on asyncio once."""
    target = logger or logging.getLogger("asyncio")
    if not any(isinstance(item, RoutineAsyncioTransportFilter) for item in target.filters):
        target.addFilter(RoutineAsyncioTransportFilter())
