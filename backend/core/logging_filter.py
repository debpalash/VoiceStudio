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

from core.scrub import scrub_url_credentials

REDACTED = "hf_***REDACTED***"

# At least 30 alphanumerics following `hf_` — covers all real HF tokens
# (typically 36-40 chars total) while leaving short matches like `hf_hub`
# alone so the log stays useful for debugging.
_HF_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")
_TRACEBACK_FORMATTER = logging.Formatter()

# Uvicorn logs an unhandled request exception, with its traceback, on
# `uvicorn.error`, and every request line, query string included, on
# `uvicorn.access`. It configures their handlers itself and they do not
# propagate to the root handlers; a filter on the logger survives that config.
_UVICORN_ERROR_LOGGERS = ("uvicorn.error", "uvicorn.access")


def _redact(text: str) -> str:
    return scrub_url_credentials(_HF_TOKEN_RE.sub(REDACTED, text))


class HFTokenRedactor(logging.Filter):
    """logging.Filter that rewrites HF token substrings and URL credentials
    (`?api_key=`, `?pin=`, `user:pw@host`) in `record.msg`, `record.args` and
    the formatted traceback before the formatter sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if not record.args:
                if isinstance(record.msg, str):
                    record.msg = _redact(record.msg)
            else:
                self._redact_args(record)

            # A traceback embeds the exception message, which can carry the
            # request URL (`RuntimeError(str(request.url))`). Formatters reuse
            # a cached `exc_text`, so formatting it once here and scrubbing it
            # keeps the raw message out of every handler.
            if record.exc_info and record.exc_info[0] is not None:
                if not record.exc_text:
                    record.exc_text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
                record.exc_text = _redact(record.exc_text)
        except Exception:
            # Never let the filter break the log pipeline. If anything
            # unexpected happens, just let the record pass through —
            # erring on the side of the log being noisy, not silent.
            pass
        return True


    @staticmethod
    def _redact_args(record: logging.LogRecord) -> None:
        """Scrub a record that is formatted from a template and arguments.

        Redacting the template itself could turn a placeholder into the marker
        (`?token=%(tok)s`) and break formatting, so the arguments are scrubbed
        one by one, which keeps them for formatters that read them (Uvicorn's
        access line, the routine-health filter). If the rendered line still
        carries a credential, because the template held one (`?pin=%s` with
        the PIN as the argument), the record collapses to the scrubbed line.
        A record that cannot be rendered never keeps its raw arguments.
        """
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                k: (_redact(v) if isinstance(v, str) else v) for k, v in record.args.items()
            }
        if isinstance(record.msg, str):
            record.msg = _HF_TOKEN_RE.sub(REDACTED, record.msg)
        try:
            rendered = record.getMessage()
        except Exception:
            record.msg, record.args = _redact(str(record.msg)), None
            return
        cleaned = _redact(rendered)
        if cleaned != rendered:
            record.msg, record.args = cleaned, None


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
    existing handler. Idempotent — repeated calls do not stack up duplicate
    filters."""
    target = root_logger or logging.getLogger()
    if not any(isinstance(f, HFTokenRedactor) for f in target.filters):
        target.addFilter(HFTokenRedactor())
    # Handlers each have their own filter list. Attach the same redactor
    # to every existing handler so even handler-formatted output is clean.
    for handler in list(target.handlers):
        if not any(isinstance(f, HFTokenRedactor) for f in handler.filters):
            handler.addFilter(HFTokenRedactor())
    if root_logger is None:
        for name in _UVICORN_ERROR_LOGGERS:
            uvicorn_logger = logging.getLogger(name)
            if not any(isinstance(f, HFTokenRedactor) for f in uvicorn_logger.filters):
                uvicorn_logger.addFilter(HFTokenRedactor())


def install_access_log_filter(logger: logging.Logger | None = None) -> None:
    """Install the routine-health filter on Uvicorn's access logger once."""
    target = logger or logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RoutineHealthAccessFilter) for item in target.filters):
        target.addFilter(RoutineHealthAccessFilter())


def install_asyncio_transport_filter(logger: logging.Logger | None = None) -> None:
    """Install the expected transport-close filter on asyncio once."""
    target = logger or logging.getLogger("asyncio")
    if not any(isinstance(item, RoutineAsyncioTransportFilter) for item in target.filters):
        target.addFilter(RoutineAsyncioTransportFilter())
