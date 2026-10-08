"""#2617: /v1/audio/speech accepts a voice-profile NAME as ``voice``.

OpenAI-protocol clients (Open WebUI, n8n, Home Assistant, …) take a free-text
voice field; before this, VoiceStudio only resolved a profile by its UUID, so
typing the name the user sees in the app silently fell through to an engine
preset (the engine's default voice). Contract pinned here:

* an exact profile id still wins, even over a profile NAMED like that id;
* a name matches case-insensitively and ignores surrounding whitespace;
* a name shared by several profiles is a 409 ``ambiguous_voice`` that lists
  the matching ids (never an arbitrary pick);
* OpenAI voice names and ``default`` keep their built-in meaning even if a
  profile is named that way;
* an unknown string is still forwarded as an engine preset;
* GET /v1/audio/voices says which profile names are usable as ``voice``.
"""
from __future__ import annotations

import importlib
import os
import uuid

import pytest
import torch
from fastapi.testclient import TestClient

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts_mod():
    return importlib.import_module("services.tts_backend")


@pytest.fixture()
def engine(monkeypatch):
    class _Engine(_tts_mod().TTSBackend):
        id = "voice-by-name-engine"
        display_name = "Voice-by-name engine (test)"
        gpu_compat = ("cpu",)
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

        def generate(self, text, **kw):
            type(self).calls.append(kw)
            return torch.zeros(1, 2400)

    _Engine.calls = []
    inst = _Engine()
    tts = _tts_mod()
    monkeypatch.setitem(tts._REGISTRY, _Engine.id, _Engine)
    monkeypatch.setattr(tts, "get_active_tts_backend", lambda: inst)
    monkeypatch.setattr(tts, "get_engine_instance_for", lambda _id: inst)
    from services import engine_routing

    async def _profile(*_a, **_k):
        return {"routing_status": "native", "routing_reason": ""}

    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", _profile)
    return _Engine


@pytest.fixture()
def client():
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def profiles():
    """Insert profiles with a per-test unique name stem; delete them after."""
    from core.db import db_conn, init_db

    init_db()
    stem = f"vbn-{uuid.uuid4().hex[:8]}"
    created: list[str] = []

    def add(name: str, *, pid: str | None = None, ref_text: str = "") -> str:
        pid = pid or str(uuid.uuid4())
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO voice_profiles (id, name, kind, created_at, ref_text, ref_audio_path) "
                "VALUES (?, ?, 'clone', ?, ?, ?)",
                (pid, name, float(len(created)), ref_text, f"{pid}.wav"),
            )
        created.append(pid)
        return pid

    add.stem = stem  # type: ignore[attr-defined]
    yield add
    with db_conn() as conn:
        for pid in created:
            conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


def _speak(client, voice):
    return client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "voice": voice, "input": "Hi.", "response_format": "wav"},
    )


def test_profile_name_resolves_case_insensitively(client, engine, profiles):
    name = f"Narrator {profiles.stem}"
    pid = profiles(name, ref_text="reference words")
    for spelled in (name, name.upper(), f"  {name.lower()}  "):
        res = _speak(client, spelled)
        assert res.status_code == 200, res.text
        kw = engine.calls[-1]
        assert kw["ref_audio"].endswith(f"{pid}.wav")
        assert kw["ref_text"] == "reference words"
        assert "voice" not in kw


def test_voice_object_name_resolves_too(client, engine, profiles):
    name = f"Object {profiles.stem}"
    pid = profiles(name)
    res = _speak(client, {"id": name.lower()})
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{pid}.wav")


def test_duplicate_names_are_a_409_listing_ids(client, engine, profiles):
    name = f"Twin {profiles.stem}"
    a = profiles(name)
    b = profiles(name.upper())
    res = _speak(client, name)
    assert res.status_code == 409, res.text
    err = res.json()["error"]
    assert err["code"] == "ambiguous_voice"
    assert err["param"] == "voice"
    assert a in err["message"] and b in err["message"]
    assert res.json()["detail"]["matching_ids"] == [a, b]
    assert engine.calls == []  # refused before any generate


def test_id_wins_over_a_profile_named_like_that_id(client, engine, profiles):
    target = profiles(f"Target {profiles.stem}")
    profiles(target)  # a second profile whose NAME is the first one's id
    res = _speak(client, target)
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{target}.wav")


def test_openai_alias_and_default_keep_their_meaning(client, engine, profiles):
    profiles("Alloy")
    profiles("DEFAULT")
    # Any spelling of an alias or "default" is the engine default: never a
    # profile match, and never forwarded to the engine as a preset name.
    for voice in ("alloy", "default", "Alloy", " NOVA ", "Default", {"id": "Shimmer"}):
        res = _speak(client, voice)
        assert res.status_code == 200, res.text
        kw = engine.calls[-1]
        assert "ref_audio" not in kw, voice
        assert "voice" not in kw, voice


def test_unknown_voice_still_forwards_as_engine_preset(client, engine, profiles):
    res = _speak(client, f"preset-{profiles.stem}")
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["voice"] == f"preset-{profiles.stem}"
    assert "ref_audio" not in engine.calls[-1]


def test_voice_list_marks_name_addressable_profiles(client, profiles):
    unique = profiles(f"Solo {profiles.stem}")
    dup_a = profiles(f"Pair {profiles.stem}")
    dup_b = profiles(f"pair {profiles.stem}")
    reserved = profiles("Nova")
    voices = {v["voice_id"]: v for v in client.get("/v1/audio/voices").json()["voices"]}
    assert voices[unique]["addressable_by_name"] is True
    assert voices[unique]["name"] == f"Solo {profiles.stem}"
    assert voices[dup_a]["addressable_by_name"] is False
    assert voices[dup_b]["addressable_by_name"] is False
    assert voices[reserved]["addressable_by_name"] is False
    assert "addressable_by_name" not in voices["nova"]  # the alias entry itself
