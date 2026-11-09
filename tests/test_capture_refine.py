"""
Tests for the opt-in local-LLM refinement on the REST `/transcribe`
endpoint (the MCP/CLI/file-upload surface).

Contract mirrors the live-dictation socket (capture_ws):
  * refinement is OFF unless the caller passes a truthy `refine` flag
    (backward-compatible — existing MCP/CLI callers keep raw-only output);
  * the raw `text` is ALWAYS present;
  * `refined_text` appears ONLY when the LLM actually changed the text.

The ASR backend and the refinement call are both stubbed — this tests the
endpoint's wiring, not transcription or LLM quality. maybe_refine is patched
**at its source module** (`services.refinement.maybe_refine`) because the
endpoint imports it lazily inside the handler; patching a bound name would
miss once the route-shape fixtures purge `services.*` from sys.modules.
"""
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

# These tests exercise the endpoint's wiring with a stubbed backend and assume
# ASR weights are installed — neutralize the no-ASR preflight (which otherwise
# answers a typed 409 asr_model_missing in the hermetic no-HF-cache test env;
# the preflight has its own suite: tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


class _StubBackend:
    id = "stub"

    def transcribe(self, _path, **_kw):
        return {
            "text": "um so like the meeting is at 3pm you know",
            "segments": [
                {"start": 0.0, "end": 2.0,
                 "text": "um so like the meeting is at 3pm you know"},
            ],
            "language": "en",
        }


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    # Both ASR getters resolve to the same in-process stub so neither mode
    # touches a model. Patched at source — the handler imports them lazily.
    monkeypatch.setattr(
        "services.asr_backend.get_capture_asr_backend", lambda: _StubBackend())
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda: _StubBackend())

    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def _post(client, **data):
    return client.post(
        "/transcribe",
        files={"audio": ("a.wav", b"\x00" * 32000, "audio/wav")},
        data=data,
    )


def test_rest_polishes_text_like_ws_socket(client):
    """P1 parity regression: the REST /transcribe `text` is polished (leading
    capital + terminal punctuation) exactly like the WS `final`. Before the fix
    REST leaked the raw recognizer string (e.g. "um … you know") while the WS
    returned "Um … you know." — the widget's POST fallback and MCP/CLI callers
    saw different text than live dictation."""
    body = _post(client).json()
    assert body["text"][0].isupper()
    assert body["text"].endswith(".")
    # Segments stay raw (their verbatim recognition / timing is the contract).
    assert body["segments"][0]["text"] == "um so like the meeting is at 3pm you know"


def test_refined_text_is_also_polished(client, monkeypatch):
    """`refined_text` is polished too, so both surfaced strings read as typed
    text (an LLM that forgets the trailing period still yields a clean final)."""
    monkeypatch.setattr(
        "services.refinement.maybe_refine",
        lambda _t: "so the meeting is at 3pm")  # lowercase, no period

    body = _post(client, refine="true").json()
    assert body["refined_text"] == "So the meeting is at 3pm."


def test_no_refine_flag_returns_raw_only(client, monkeypatch):
    # maybe_refine must never even be called when the flag is absent.
    called = {"n": 0}

    def _boom(_t):
        called["n"] += 1
        return "SHOULD NOT APPEAR"

    monkeypatch.setattr("services.refinement.maybe_refine", _boom)

    r = _post(client)
    assert r.status_code == 200
    body = r.json()
    # REST now polishes the final exactly like the WS socket (leading capital +
    # terminal punctuation) — no more raw "um so like…" leaking to the widget
    # fallback / MCP / CLI. Without dictation=true the words stay as spoken.
    assert body["text"] == "Um so like the meeting is at 3pm you know."
    assert "refined_text" not in body
    assert called["n"] == 0


def test_refine_flag_adds_refined_text_when_changed(client, monkeypatch):
    monkeypatch.setattr(
        "services.refinement.maybe_refine",
        lambda _t: "So the meeting is at 3pm.")

    r = _post(client, refine="true")
    assert r.status_code == 200
    body = r.json()
    # The (polished) raw text is preserved …
    assert body["text"] == "Um so like the meeting is at 3pm you know."
    # … and the cleaned text rides alongside it (already terminal-punctuated,
    # so polish is idempotent here).
    assert body["refined_text"] == "So the meeting is at 3pm."


def test_refine_noop_omits_refined_text(client, monkeypatch):
    # maybe_refine returning None (no LLM / off / failure) must leave the
    # response raw-only — no empty/echoed refined_text key.
    monkeypatch.setattr("services.refinement.maybe_refine", lambda _t: None)

    r = _post(client, refine="1")
    assert r.status_code == 200
    assert "refined_text" not in r.json()


def test_refine_identical_result_omits_refined_text(client, monkeypatch):
    # If the LLM returns the same string, don't add a redundant key.
    monkeypatch.setattr(
        "services.refinement.maybe_refine",
        lambda t: t)

    r = _post(client, refine="yes")
    assert r.status_code == 200
    assert "refined_text" not in r.json()


@pytest.mark.parametrize("flag,expect_refine", [
    ("true", True), ("1", True), ("auto", True), ("on", True), ("YES", True),
    ("false", False), ("0", False), ("off", False), ("", False),
])
def test_flag_parsing(client, monkeypatch, flag, expect_refine):
    monkeypatch.setattr(
        "services.refinement.maybe_refine", lambda _t: "CLEANED")
    r = _post(client, refine=flag)
    assert r.status_code == 200
    assert ("refined_text" in r.json()) is expect_refine


def test_dictation_callers_get_dictation_cleanup(client):
    # The hotkey-dictation POST fallback types the same cleaned text as the
    # socket: the English hesitation "um" goes.
    body = _post(client, dictation="true").json()
    assert body["text"] == "So like the meeting is at 3pm you know."


def test_reference_mode_keeps_spoken_words_even_for_dictation(client, monkeypatch):
    # A voice-clone reference transcript must match the audio word for word.
    monkeypatch.setattr(
        "services.asr_backend.load_active_asr_backend", lambda *_a, **_k: _StubBackend())
    body = _post(client, dictation="true", mode="reference").json()
    assert body["text"].startswith("Um so like")
