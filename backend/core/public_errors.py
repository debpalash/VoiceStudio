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
    if isinstance(error, BaseException):
        # The exception's TYPE NAME, never its message. Two failures that both
        # render the floor message "Generation failed. Check the selected
        # engine and try again." are indistinguishable in an auto-filed report,
        # so every unclassified streaming failure arrives as the same issue and
        # none of them can be triaged (#1800). A class name is VoiceStudio-safe
        # by the same reasoning that already puts it on the wire as
        # `error_class` in the dub routes and on the analytics allowlist: it is
        # a Python type, not user text, and no substring of `error` is copied.
        payload["error_class"] = type(error).__name__
    try:
        from core.failure import is_terminal_failure_topic

        enriched = public_exception_response(error, fallback=str(payload["detail"]))
        terminal = is_terminal_failure_topic(enriched.get("docs_topic"))
    except Exception:
        return payload
    hint = enriched.get("hint")
    if hint:
        payload["detail"] = enriched["detail"]
        payload["hint"] = hint
        topic = enriched.get("docs_topic")
        if topic:
            payload["docs_topic"] = topic
            # #2177: a build mismatch, a device selection or an OS policy does
            # not change between two renders of the same text. Marking these
            # terminal stops the floor message's "try again" from being the
            # only advice, and stops the client re-rendering the whole passage
            # on the classic path to reach the identical failure.
            if terminal:
                payload["terminal"] = True
                payload["retryable"] = False
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

    Every caller is a CONTEXT-FREE surface — the global 500 handler, the
    streaming generate error frame, the dub GPU-OOM 503 — so the topic is
    filtered through ``failure._CONTEXT_FREE_HINT_CLASSES`` before its hint is
    attached. Without that filter a topic whose trigger is a generic phrase
    stamps a confidently wrong remediation on an unrelated failure: #1943 is a
    macOS mlx-audio TTS 500 that came back advising the user that "the
    connection to the video server dropped mid-download", because
    VIDEO_DOWNLOAD_NETWORK triggers on a bare "timed out" / "connection
    reset". The allowlist already existed and already named that class as the
    example of what must not appear here; only :func:`failure.append_hint`
    honoured it, and this helper replaced ``append_hint`` on the 500 path
    without carrying the rule across.

    HF_MIRROR_UNREACHABLE is allowed alongside it: its hint is dynamic (it
    names the configured mirror) and its trigger requires that a mirror is
    configured at all, so it cannot fire on an unrelated failure (#874).
    """
    from core.failure import _CONTEXT_FREE_HINT_CLASSES, classify, public_hint_for_topic

    try:
        topic = classify(str(error))
        if topic and topic not in _CONTEXT_FREE_HINT_CLASSES and topic != "HF_MIRROR_UNREACHABLE":
            topic = ""
        hint = public_hint_for_topic(topic) if topic else ""
    except Exception:
        topic = ""
        hint = ""
    payload = {"detail": f"{fallback} {hint}".strip()}
    if topic and hint:
        payload.update({"docs_topic": topic, "hint": hint})
    return payload


# ── Engine model load / download failure (#2298) ─────────────────────────────
# An adapter engine fetches its weights lazily on first use, so /generate and
# /v1/audio/speech warm it under the model-LOAD budget before the generate
# clock starts (#1033). That warm-up translated only a TIMEOUT into an
# actionable reply. A download that FAILED — the mirror unreachable, DNS gone,
# the connection refused, the body truncated, the cache corrupt — escaped the
# route and reached the global 500 handler, which could only say "VoiceStudio
# hit an internal error": it named neither the engine nor the fact that a model
# download was the thing that broke. #2298 is that report (KittenTTS, first
# generate, an unreachable mirror). A download that stalls and a download that
# fails are the same event to the user, so they now get the same treatment.


def model_load_failure(engine_id: str, error: BaseException) -> dict[str, object]:
    """Fixed, actionable metadata for an engine whose model would not load.

    Opens with the same sentence shape as the load-TIMEOUT sibling on both
    routes, then appends whatever the shared taxonomy can say about THIS
    failure — an unreachable mirror points at the mirror setting, a corrupt
    cache names the repair — reusing ``public_exception_response`` so the
    context-free hint allowlist (#1943) applies here too.

    Only VoiceStudio-owned constants reach the payload; no substring of
    ``error`` is copied (Constitution I). The exception's TYPE NAME rides along
    as ``error_class`` so two load failures that render the same sentence are
    still distinguishable in an auto-filed report (the #1800 reasoning).

    Never raises: a diagnosis failure must not replace the failure being
    diagnosed.
    """
    engine = (str(engine_id) if engine_id else "").strip() or "the selected engine"
    base = (
        f"TTS engine '{engine}' could not load its model. On a first run this is "
        f"the weight download failing rather than generation — check the engine's "
        f"Weights list in Model Catalogue, then retry."
    )
    try:
        payload: dict[str, object] = dict(
            public_exception_response(error, fallback=base)
        )
    except Exception:  # pragma: no cover — diagnosis must never mask the fault
        payload = {"detail": base}
    try:
        if isinstance(error, BaseException):
            payload["error_class"] = type(error).__name__
    except Exception:  # pragma: no cover
        pass
    return payload
