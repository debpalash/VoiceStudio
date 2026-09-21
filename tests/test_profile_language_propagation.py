"""Non-English correctness: a voice profile's stored language must drive
generation, and the longform path must not hardcode language=None.

  * #533 — POST /generate with a profile_id but no request language must thread
    the *profile's* language into the engine (German archetype → German output,
    not English). An explicit request language, including Auto, wins.
  * #505 (B2) — the audiobook/longform synth callable hardcoded language=None,
    so each chunk re-autodetected and a non-English clone drifted. The synth
    must now carry the resolved language.

The engine layer is stubbed (no real model loads), matching
``tests/test_generate_engine.py``.
"""
import importlib
import os
import uuid

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _make_fake_engine(engine_id="fake-lang-engine", gpu_compat=("cpu",)):
    _compat = gpu_compat

    class _FakeEngine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Fake Lang Engine (test)"
        applies_own_mastering = False
        gpu_compat = _compat
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 24000)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def _init_db():
    from core.db import init_db

    init_db()


@pytest.fixture()
def german_profile(_init_db):
    """Insert a clone profile whose stored language is 'German', then remove it.

    No ref_audio_path on disk → the resolver leaves ref_audio None, so the
    engine still runs (the test asserts on the threaded `language`, not audio)."""
    from core.db import db_conn

    pid = f"vp-de-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, language, kind, created_at) "
            "VALUES (?,?,?,?,?)",
            (pid, "German Narrator", "German", "clone", 0.0),
        )
    yield pid
    with db_conn() as conn:
        # A successful /generate inserts a generation_history row FK-referencing
        # the profile — clear dependents before the profile itself.
        conn.execute("DELETE FROM generation_history WHERE profile_id=?", (pid,))
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


# ── #533: profile language drives /generate ──────────────────────────────────


def test_generate_uses_profile_language_when_request_unset(client, monkeypatch, german_profile):
    """Request omits language → the profile's stored 'German' reaches the engine
    (not None). Before the fix generation.py never read row['language']."""
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    res = client.post("/generate", data={
        "text": "Guten Tag", "profile_id": german_profile, "engine": fake.id,
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1
    _, kw = fake.calls[0]
    # The engine receives the profile's language string; the model's
    # _resolve_language maps 'German' → 'de'. Critically: NOT None.
    assert kw.get("language") == "German"


def test_generate_explicit_language_overrides_profile(client, monkeypatch, german_profile):
    """An explicit non-Auto request language wins over the profile language."""
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    res = client.post("/generate", data={
        "text": "Hello", "profile_id": german_profile, "engine": fake.id,
        "language": "en",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1
    _, kw = fake.calls[0]
    assert kw.get("language") == "en"  # request wins, not 'German'


@pytest.mark.parametrize("stream", [False, True])
def test_generate_explicit_auto_overrides_profile(client, monkeypatch, german_profile, stream):
    """Auto uses the target script, not the saved reference language."""
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    res = client.post("/generate", data={
        "text": "Bonjour", "profile_id": german_profile, "engine": fake.id,
        "language": "Auto", "stream": str(stream).lower(),
    })

    assert res.status_code == 200, res.text
    _, kw = fake.calls[0]
    assert kw.get("language") is None


# ── #505 (B2): longform synth carries the language, never hardcoded None ──────


def test_resolve_default_language_request_wins():
    from api.routers.audiobook import _resolve_default_language

    assert _resolve_default_language("ja", None) == "ja"
    assert _resolve_default_language("ja", "anything") == "ja"


def test_resolve_default_language_falls_back_to_profile(german_profile):
    from api.routers.audiobook import _resolve_default_language

    # No request language → the profile's stored language drives it.
    assert _resolve_default_language(None, german_profile) == "German"
    assert _resolve_default_language("Auto", german_profile) == "German"


def test_resolve_default_language_none_when_nothing(_init_db):
    from api.routers.audiobook import _resolve_default_language

    assert _resolve_default_language(None, None) is None
    assert _resolve_default_language("Auto", None) is None


def test_build_synth_threads_language_into_generic_engine(monkeypatch):
    """The generic-engine synth callable must pass the resolved language to
    backend.generate — before the fix it hardcoded language=None (#505 B2)."""
    import api.routers.audiobook as ab

    captured = {}

    class _Backend:
        sample_rate = 24000

        def generate(self, text, **kw):
            captured["language"] = kw.get("language")
            return torch.zeros(1, 24000)

    monkeypatch.setattr(ab, "_resolve_voice",
                        lambda pid: {"ref_audio": None, "ref_text": None,
                                     "instruct": None, "seed": None})
    monkeypatch.setattr("services.tts_backend.active_backend_id", lambda: "fake")
    # _Backend is not OmniVoiceBackend → _build_synth takes the generic path.
    monkeypatch.setattr("services.tts_backend.get_backend_class", lambda eid: _Backend)

    info = ab._build_synth(default_voice="vp1", language="ja")
    assert info["mode"] == "generic"
    info["synth"]("こんにちは", "vp1")
    assert captured["language"] == "ja"  # NOT None
