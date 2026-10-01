"""#874: a model download that fails because the CONFIGURED Hugging Face
mirror (HF_ENDPOINT, Settings → Models → Hugging Face mirror) is unreachable
must surface an actionable error that (1) names the mirror, (2) says it may be
down, (3) points at the setting AND says downloads pick up a mirror change
immediately (the download paths resolve the endpoint per call — the old hint
falsely claimed a restart was always required, dead-ending first-run users
whose only recovery path is switch-and-retry inside the wizard), and
(4) suggests the official endpoint when the model isn't cached — instead of
leaking the raw transformers message ("We couldn't connect to
'https://hf-mirror.com' …") as a bare 500 detail.

Fail-before/pass-after: before the fix `classify()` had no mirror class and
`build_failure()` / `append_hf_mirror_hint()` attached no hint to these
reasons. Also covers the #886 family: when the incomplete-cache auto-repair
fails, the surfaced message now names WHY (so a mirror outage / offline mode /
full disk stop reading identically).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from core import failure

# The exact transformers wording from issue #874 (mirror down, model not cached).
_TRANSFORMERS_874 = (
    "We couldn't connect to 'https://hf-mirror.com' to load the files, and "
    "couldn't find them in the cached files.\n"
    "Check your internet connection or see how to run the library in offline "
    "mode at 'https://huggingface.co/docs/transformers/installation#offline-mode'."
)

# huggingface_hub / requests shape: names the mirror HOST, not the full URL.
_HUB_CONN_ERROR = (
    "(MaxRetryError(\"HTTPSConnectionPool(host='hf-mirror.com', port=443): "
    "Max retries exceeded with url: /api/models/k2-fsa/OmniVoice (Caused by "
    "NewConnectionError('Failed to establish a new connection: "
    "[Errno 61] Connection refused'))\"))"
)


@pytest.fixture
def mirror_env(monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")


def test_transformers_connect_error_classifies_as_mirror(mirror_env):
    assert failure.classify(_TRANSFORMERS_874) == "HF_MIRROR_UNREACHABLE"


def test_hub_connection_error_classifies_by_host(mirror_env):
    assert failure.classify(_HUB_CONN_ERROR) == "HF_MIRROR_UNREACHABLE"


def test_hint_names_mirror_setting_retry_and_official(mirror_env):
    evt = failure.build_failure(
        OSError(_TRANSFORMERS_874), stage="model-load", include_diagnostic=False
    )
    assert evt["docs_topic"] == "HF_MIRROR_UNREACHABLE"
    hint = evt["hint"]
    assert "https://hf-mirror.com" in hint  # (1) names the configured mirror
    assert "may be down" in hint  # (2) says it may be down
    # (3) the setting path + the truth about when the change applies: downloads
    # resolve the endpoint per call, so switch-and-retry works WITHOUT a
    # restart — the old "applied when the app starts" claim dead-ended
    # first-run users, who can't reach Settings and would lose wizard progress.
    assert "Settings → Models → Hugging Face mirror" in hint
    assert "immediately" in hint
    assert "applied when the app starts" not in hint
    # Restart survives only as the fallback for import-time readers
    # (transformers-side model loads) when a retry still fails.
    assert "restart" in hint.lower()
    # (4) suggests the official endpoint for an un-cached model
    assert "Hugging Face (official)" in hint


def test_timeout_with_mirror_not_misclassified_as_video_network(mirror_env):
    # A bare "timed out" used to fall into VIDEO_DOWNLOAD_NETWORK, whose hint
    # talks about "the video server" — a model download naming the mirror host
    # must classify as the mirror class instead.
    reason = "HTTPSConnectionPool(host='hf-mirror.com', port=443): Read timed out."
    assert failure.classify(reason) == "HF_MIRROR_UNREACHABLE"


def test_no_class_without_configured_mirror(monkeypatch):
    # The same #874 reason with NO mirror configured: not this class (the
    # official endpoint being unreachable is plain connectivity, not a
    # switch-your-mirror problem).
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    from core import prefs

    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    assert failure.classify(_TRANSFORMERS_874) == ""
    assert failure.hf_mirror_hint(_TRANSFORMERS_874) == ""


def test_official_endpoint_is_not_a_mirror(monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co")
    assert failure.hf_mirror_hint(_TRANSFORMERS_874) == ""
    # Trailing slash normalizes away too.
    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co/")
    assert failure.hf_mirror_hint(_TRANSFORMERS_874) == ""


def test_non_hf_connectivity_error_gets_no_mirror_hint(mirror_env):
    # A random socket failure (e.g. a local LLM provider being down) while a
    # mirror happens to be configured must NOT get the mirror hint.
    assert failure.hf_mirror_hint("Connection refused by localhost:11434") == ""


def test_append_helper_appends_or_passes_through(mirror_env):
    # append_hf_mirror_hint is the 500-detail surface (main.py global handler)
    # and the model-install SSE surface (setup/download.py).
    out = failure.append_hf_mirror_hint(_TRANSFORMERS_874)
    assert out.startswith(_TRANSFORMERS_874)
    assert "Settings → Models → Hugging Face mirror" in out
    assert failure.append_hf_mirror_hint("some unrelated failure") == "some unrelated failure"


def test_journal_classifies_mirror_download_as_network_error(mirror_env):
    # Bug reports auto-attach the journal entry — #874's error was UNKNOWN.
    from core import error_journal

    assert error_journal.classify_exception(OSError(_TRANSFORMERS_874)) == "NETWORK_ERROR"


# ── #886 family: the incomplete-cache auto-repair failure names its cause ───


@pytest.fixture
def model_manager(monkeypatch):
    for mod_name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(mod_name), "__file__", None) is None:
            sys.modules.pop(mod_name, None)

    import services.model_manager as mm

    monkeypatch.setattr(mm, "_torch", None)
    monkeypatch.setattr(mm, "_OmniVoice", None)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setenv("OMNIVOICE_MODEL", "test/checkpoint")
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: SimpleNamespace(float16="float16", float32="float32"))
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")
    monkeypatch.setattr(mm, "_last_repair_error", "")
    return mm


_TRUNCATED = OSError(
    "test/checkpoint does not appear to have a file named pytorch_model.bin "
    "or model.safetensors"
)


def test_repair_records_why_it_failed(model_manager, monkeypatch):
    import huggingface_hub
    from services import hf_revisions

    monkeypatch.setitem(
        hf_revisions.CURATED_REVISIONS,
        "test/checkpoint",
        "a" * 40,
    )

    def boom(**kwargs):
        raise OSError(_TRANSFORMERS_874)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "0")
    monkeypatch.setenv("OMNIVOICE_MODEL_REPAIR_RETRIES", "1")
    assert model_manager._repair_model_cache("test/checkpoint") is False
    assert "hf-mirror.com" in model_manager._last_repair_error


def test_repair_failure_message_names_cause_and_mirror(model_manager, monkeypatch, mirror_env):
    """#886 family: 'could not be auto-repaired' used to drop the cause, so a
    mirror outage, offline mode, and a full disk all read identically. The
    message now carries the cause — and because the cause text is part of the
    surfaced error, the shared #874 mirror hint fires on it downstream."""
    monkeypatch.setattr(
        model_manager, "_repair_model_cache", lambda checkpoint, **kw: False
    )
    monkeypatch.setattr(
        model_manager, "_last_repair_error", f"OSError: {_TRANSFORMERS_874}"
    )

    class BrokenOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise _TRUNCATED

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: BrokenOmniVoice)

    with pytest.raises(RuntimeError) as exc_info:
        model_manager._load_model_sync()
    msg = str(exc_info.value)
    assert "incomplete" in msg  # the existing actionable class is preserved
    assert "the engine's Weights list in Model Catalogue" in msg
    assert "hf-mirror.com" in msg  # NEW: the cause is named
    # …and the surfaced text now carries enough signal for the shared mirror
    # hint to fire on the 500-detail surface (main.py appends it).
    assert failure.hf_mirror_hint(msg) != ""


def test_repair_failure_without_cause_keeps_legacy_message(model_manager, monkeypatch):
    """No recorded cause (e.g. tests/plugins stubbing repair) → the message is
    byte-compatible with the pre-#874 wording, no dangling clause."""
    monkeypatch.setattr(
        model_manager, "_repair_model_cache", lambda checkpoint, **kw: False
    )
    monkeypatch.setattr(model_manager, "_last_repair_error", "")

    class BrokenOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise _TRUNCATED

    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: BrokenOmniVoice)

    with pytest.raises(RuntimeError, match="interrupted download"):
        model_manager._load_model_sync()
