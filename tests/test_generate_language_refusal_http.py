"""POST /generate: how a refused language is reported, by where the language came from."""
import importlib
import json
import os
import uuid

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

KOKORO_STYLE = (
    "Kokoro model (demo) doesn't support language='{lang}'. Kokoro supports: "
    "Chinese, English, French. Pick one of those, leave language as 'Auto', "
    "or switch to a multilingual engine."
)
GENERIC_STYLE = "Invalid language code. Supported languages: ar (Arabic), da (Danish), de (German)"
OK_LANGS = {"English", "French", "Chinese"}


def _engine_class(refusal_template):
    tts = importlib.import_module("services.tts_backend")

    class _Refuser(tts.TTSBackend):
        id = "lang-refuser"
        display_name = "Language Refuser (test)"
        applies_own_mastering = False
        gpu_compat = ("cpu",)
        seen: list = []

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).seen.append(kw.get("language"))
            lang = kw.get("language")
            if refusal_template is not None and lang not in OK_LANGS and lang is not None:
                raise ValueError(refusal_template.format(lang=lang))
            return torch.zeros(1, 24000)

    return _Refuser


@pytest.fixture(autouse=True)
def _isolated_outputs(tmp_path, monkeypatch):
    import core.config as cfg
    import api.routers.generation as gen

    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(cfg, "OUTPUTS_DIR", str(out))
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(out))

    async def _no_default_model():
        raise AssertionError("default model must not be loaded")

    monkeypatch.setattr(gen, "get_model", _no_default_model)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def make_profile():
    from core import db

    db.init_db()
    inserted = []

    def make(language):
        profile_id = f"lang-{uuid.uuid4().hex[:10]}"
        with db.db_conn() as conn:
            conn.execute(
                "INSERT INTO voice_profiles (id, name, language, kind, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, "Language fixture", language, "clone", 0.0),
            )
        inserted.append(profile_id)
        return profile_id

    yield make
    with db.db_conn() as conn:
        for profile_id in inserted:
            conn.execute("DELETE FROM generation_history WHERE profile_id=?", (profile_id,))
            conn.execute("DELETE FROM voice_profiles WHERE id=?", (profile_id,))


def _install(monkeypatch, engine):
    monkeypatch.setitem(importlib.import_module("services.tts_backend")._REGISTRY, engine.id, engine)
    engine.seen.clear()


def _detail_text(resp):
    d = resp.json()["detail"]
    return d["message"] if isinstance(d, dict) else d


def test_profile_sourced_refusal_blames_the_profile(client, monkeypatch, make_profile):
    eng = _engine_class(KOKORO_STYLE)
    _install(monkeypatch, eng)
    pid = make_profile("Persian")
    r = client.post("/generate", data={"text": "salam", "profile_id": pid, "engine": eng.id})
    assert r.status_code == 400
    msg = _detail_text(r)
    assert "voice profile" in msg.lower() and "Persian" in msg
    assert "does not override" in msg
    assert "Kokoro supports:" in msg
    assert msg.count("Engine's own message:") == 1
    assert eng.seen == ["Persian"]


def test_generic_refusal_from_profile_is_not_duplicated(client, monkeypatch, make_profile):
    eng = _engine_class(GENERIC_STYLE)
    _install(monkeypatch, eng)
    pid = make_profile("Persian")
    r = client.post("/generate", data={"text": "salam", "profile_id": pid, "engine": eng.id})
    assert r.status_code == 400
    msg = _detail_text(r)
    assert msg.count("Engine's own message:") == 1
    assert msg.count("switch engine in Model Catalogue") == 1
    assert msg.count("Invalid language code") == 1


def test_explicit_language_keeps_the_engine_message(client, monkeypatch, make_profile):
    eng = _engine_class(KOKORO_STYLE)
    _install(monkeypatch, eng)
    pid = make_profile("English")
    r = client.post("/generate", data={
        "text": "salam", "profile_id": pid, "engine": eng.id, "language": "Persian"})
    assert r.status_code == 400
    msg = _detail_text(r)
    assert "doesn't support language='Persian'" in msg
    assert "voice profile" not in msg.lower()
    assert "does not override" not in msg


def test_supported_profile_language_succeeds(client, monkeypatch, make_profile):
    eng = _engine_class(KOKORO_STYLE)
    _install(monkeypatch, eng)
    pid = make_profile("English")
    r = client.post("/generate", data={"text": "hello", "profile_id": pid, "engine": eng.id})
    assert r.status_code == 200, r.text
    assert eng.seen == ["English"]


def test_unrelated_value_error_passes_through(client, monkeypatch, make_profile):
    eng = _engine_class("Reference clip is shorter than 3 seconds.")
    _install(monkeypatch, eng)
    pid = make_profile("Persian")
    r = client.post("/generate", data={"text": "salam", "profile_id": pid, "engine": eng.id})
    assert r.status_code == 400
    msg = _detail_text(r)
    assert "shorter than 3 seconds" in msg
    assert "voice profile" not in msg.lower()


def test_streamed_profile_refusal_emits_terminal_error_frame(client, monkeypatch, make_profile):
    eng = _engine_class(KOKORO_STYLE)
    _install(monkeypatch, eng)
    pid = make_profile("Persian")
    frames = []
    with client.stream("POST", "/generate", data={
            "text": "salam", "profile_id": pid, "engine": eng.id, "stream": "true"}) as r:
        assert r.status_code == 200
        frames = [json.loads(line) for line in r.iter_lines() if line.strip()]
    errors = [f for f in frames if f.get("type") == "error"]
    assert len(errors) == 1
    err = errors[0]
    assert err["code"] == "profile_language_rejected"
    assert err["language"] == "Persian"
    assert err["retryable"] is False
    assert err["terminal"] is True
    assert not any(f.get("type") == "done" for f in frames)
