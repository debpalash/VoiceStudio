"""Data-independent error metadata safe for API and streaming responses."""
from __future__ import annotations

import logging
from typing import Any

_PROVIDER_DETAILS = {
    "auth": "Authentication failed. Check the provider API key.",
    "not_found": "Provider or model not found. Check the model and Base URL.",
    "rate_limit": "The provider rate limit was reached. Try again later.",
    "network": "The provider could not be reached. Check the connection and Base URL.",
    "config": "Configure the provider before using it.",
    "error": "The provider request failed. Try again.",
}


def provider_failure(kind: str) -> dict[str, str]:
    """Return a stable provider error class and remediation message."""
    safe_kind = kind if kind in _PROVIDER_DETAILS else "error"
    return {"kind": safe_kind, "detail": _PROVIDER_DETAILS[safe_kind]}


def stream_failure(code: str) -> dict[str, object]:
    """Return stable stream metadata selected only from an internal code."""
    failures: dict[str, dict[str, object]] = {
        "generation_busy": {
            "code": "generation_busy",
            "detail": "Generation capacity is busy. Try again shortly.",
            "retryable": True,
        },
        "generation_timeout": {
            "code": "generation_timeout",
            "detail": (
                "Generation exceeded the compute-time limit. The backend is "
                "still running; try a shorter passage, or raise the "
                "compute-time budget in Settings → Performance & Device."
            ),
            "retryable": True,
        },
        "invalid_request": {
            "code": "invalid_request",
            "detail": "The generation request could not be processed.",
            "retryable": False,
        },
        "generation_failed": {
            "code": "generation_failed",
            "detail": "Generation failed. Check the selected engine and try again.",
            "retryable": True,
        },
        "transcription_failed": {
            "code": "transcription_failed",
            "detail": "Transcription failed. Check the selected ASR engine and try again.",
            "retryable": True,
        },
        "transcription_memory": {
            "code": "transcription_memory",
            "detail": (
                "Transcription ran out of GPU memory. Close other GPU apps or "
                "Flush models, then try again; VoiceStudio will use CPU when "
                "the remaining GPU memory is too low."
            ),
            "retryable": True,
        },
        "transcription_timeout": {
            "code": "transcription_timeout",
            "detail": (
                "Transcription timed out while the backend is running. Increase "
                "OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S or select the "
                "faster-whisper-isolated engine, then try again."
            ),
            "retryable": True,
        },
    }
    return dict(failures.get(code, failures["generation_failed"]))


def stream_generation_failure(error: BaseException | object) -> dict[str, object]:
    """``generation_failed`` stream metadata, enriched with the actual cause.

    The bare "Generation failed. Check the selected engine and try again." is
    the floor for an *unrecognized* failure. When the private exception DOES
    classify to a known failure class — a corrupt model cache, an unreachable
    Hugging Face mirror, a missing ffmpeg/ffprobe, a Windows paging-file limit,
    a SOCKS/TLS proxy problem, … — the stable VoiceStudio-owned remediation for
    that class is appended so the user can self-diagnose instead of guessing
    which engine or which failure. This is the same enrichment the classic
    (non-streaming) ``/generate`` 500 already gets via
    :func:`public_exception_response`; the in-band streaming error frame
    replaces the global 500 handler for a streaming request and used to bypass
    it entirely (#1607).

    Only VoiceStudio-owned constants are copied — never a substring of
    ``error`` (Constitution I). Never raises: a diagnosis failure must not
    replace the failure being diagnosed.
    """
    payload = stream_failure("generation_failed")
    try:
        enriched = public_exception_response(error, fallback=str(payload["detail"]))
    except Exception:
        return payload
    hint = enriched.get("hint")
    if hint:
        payload["detail"] = enriched["detail"]
        payload["hint"] = hint
        topic = enriched.get("docs_topic")
        if topic:
            payload["docs_topic"] = topic
            try:
                from core import error_docs_map

                url = error_docs_map.ERROR_DOCS.get(topic, "")
            except Exception:
                url = ""
            if url:
                payload["docs_url"] = url
    return payload


def public_failure(
    logger: logging.Logger,
    log_message: str,
    error: BaseException | object,
    *,
    response: str,
    traceback: bool = False,
) -> str:
    """Log fixed failure metadata and return a fixed public failure message.

    ``response`` must be authored by VoiceStudio, never derived from ``error``.
    The helper intentionally does not attempt to redact exception text: a
    deny-list cannot cover arbitrary secrets, paths, source lines or nested
    tracebacks.
    """
    del traceback
    error_class = type(error).__name__ if isinstance(error, BaseException) else "Failure"
    logger.error("%s (class=%s; details withheld)", log_message, error_class)
    return response


def public_engine_health(ok: bool, diagnostic: Any) -> str:
    """Map an engine-owned health diagnostic to a stable response message."""
    del diagnostic
    return "Healthy" if ok else "Engine unavailable; check the backend log for details."


def public_exception_response(error: BaseException, *, fallback: str) -> dict[str, str]:
    """Return fixed remediation selected by a stable failure taxonomy.

    Classification may inspect the private diagnostic locally, but response
    values come exclusively from VoiceStudio-owned constants. No substring of
    ``error`` is copied into the payload.
    """
    from core.failure import classify, public_hint_for_topic

    try:
        topic = classify(str(error))
        hint = public_hint_for_topic(topic)
    except Exception:
        topic = ""
        hint = ""
    payload = {"detail": f"{fallback} {hint}".strip()}
    if topic and hint:
        payload.update({"docs_topic": topic, "hint": hint})
    return payload
