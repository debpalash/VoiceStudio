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


def test_imported_clone_keeps_prose_on_freeform_engine(client, engine, profiles):
    """A legacy imported clone's style prose is not reduced to taxonomy tags."""
    from core.db import db_conn
    from services.voice_design_profiles import profile_instruct
    from omnivoice.utils.voice_design import heal_design_instruct

    pid = profiles(f"Imported clone {profiles.stem}")
    original = "Speak gently, with airy breathiness and an intimate, tired voice."
    with db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET instruct=? WHERE id=?", (original, pid),
        )
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (pid,)).fetchone()

    assert row["kind"] == "clone"
    assert profile_instruct(row, "voxcpm2") == original
    assert profile_instruct(row, "omnivoice") == heal_design_instruct(original, None)
    assert profile_instruct(row) == heal_design_instruct(original, None)
    assert profile_instruct(row, "omnivoice") != original

    engine.instruct_vocabulary = "freeform"
    try:
        res = _speak(client, pid)
        assert res.status_code == 200, res.text
        assert engine.calls[-1]["instruct"] == original
        assert engine.calls[-1]["ref_audio"].endswith(f"{pid}.wav")

        # A tag-restricted backend continues receiving only known taxonomy.
        engine.instruct_vocabulary = "tags"
        res = _speak(client, pid)
        assert res.status_code == 200, res.text
        assert engine.calls[-1].get("instruct", "") == heal_design_instruct(original, None)
    finally:
        engine.instruct_vocabulary = "tags"


def test_native_profile_keeps_full_description_in_openai_speech(client, engine, profiles):
    """OpenAI-compatible synthesis uses the authoring engine's voice recipe."""
    import json
    from core.db import db_conn

    engine.instruct_vocabulary = "freeform"
    pid = profiles(f"Native {profiles.stem}")
    desc = "A warm, breathy adult voice with intimate cinematic delivery."
    with db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET kind='design', instruct='female', "
            "design_recipe=?, vd_states=? WHERE id=?",
            (json.dumps({
                "description": desc, "picks": {"Pitch": "low pitch"},
                "engine": engine.id,
            }), json.dumps({"Gender": "female"}), pid),
        )
    try:
        res = _speak(client, pid)
        assert res.status_code == 200, res.text
        assert engine.calls[-1]["instruct"] == desc + ", low pitch"
    finally:
        engine.instruct_vocabulary = "tags"


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
    impostor = profiles(target)  # a second profile whose NAME is the first one's id
    res = _speak(client, target)
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{target}.wav")
    voices = {v["voice_id"]: v for v in client.get("/v1/audio/voices").json()["voices"]}
    assert voices[target]["addressable_by_name"] is True
    assert voices[impostor]["addressable_by_name"] is False


def test_id_shaped_names_follow_exact_id_precedence(client, engine, profiles):
    # A catalogue name shaped like an id, while no profile carries that id,
    # selects its named profile (exact and case-variant spellings).
    named = profiles("abcdef12")
    for spelled in ("abcdef12", "ABCDEF12"):
        res = _speak(client, spelled)
        assert res.status_code == 200, res.text
        assert engine.calls[-1]["ref_audio"].endswith(f"{named}.wav"), spelled
    # Once a profile owns that exact id, the exact spelling selects the
    # owner while the case-variant still resolves by name.
    owner = profiles("Id Owner", pid="abcdef12")
    res = _speak(client, "abcdef12")
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{owner}.wav")
    res = _speak(client, "ABCDEF12")
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{named}.wav")
    voices = {v["voice_id"]: v for v in client.get("/v1/audio/voices").json()["voices"]}
    assert voices[named]["addressable_by_name"] is False
    assert voices[owner]["addressable_by_name"] is True
    # A second normalized name makes both flags false; nonexact requests
    # are a 409, while the exact id still wins.
    twin = profiles("ABCDEF12")
    res = _speak(client, "abcdef12")
    assert res.status_code == 200, res.text
    assert engine.calls[-1]["ref_audio"].endswith(f"{owner}.wav")
    n_calls = len(engine.calls)
    res = _speak(client, "ABCDEF12")
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "ambiguous_voice"
    assert res.json()["detail"]["matching_ids"] == [named, twin]
    assert len(engine.calls) == n_calls  # refused before any generate
    voices = {v["voice_id"]: v for v in client.get("/v1/audio/voices").json()["voices"]}
    assert voices[named]["addressable_by_name"] is False
    assert voices[twin]["addressable_by_name"] is False


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
    self_pid = f"{profiles.stem}-selfid"
    profiles(self_pid, pid=self_pid)  # named after its own id: still addressable
    case_target = profiles(f"Case {profiles.stem}")
    case_variant = profiles(case_target.upper())  # differs from an id by case: still by name
    whitespace_variant = profiles(f" {unique} ")  # differs from another id by whitespace: still by name
    voices = {v["voice_id"]: v for v in client.get("/v1/audio/voices").json()["voices"]}
    assert voices[unique]["addressable_by_name"] is True
    assert voices[unique]["name"] == f"Solo {profiles.stem}"
    assert voices[dup_a]["addressable_by_name"] is False
    assert voices[dup_b]["addressable_by_name"] is False
    assert voices[reserved]["addressable_by_name"] is False
    assert voices[self_pid]["addressable_by_name"] is True
    assert voices[case_variant]["addressable_by_name"] is True
    assert voices[whitespace_variant]["addressable_by_name"] is True
    assert "addressable_by_name" not in voices["nova"]  # the alias entry itself


def test_voice_list_entries_carry_an_openai_style_id(client, profiles):
    """#2679: Open WebUI reads ``{v['id']: v['name'] for v in data['voices']}``
    from ``/audio/voices``. Without ``id`` the lookup raised KeyError and the
    client fell back to OpenAI's stock voices, hiding every profile."""
    profile = profiles(f"Listed {profiles.stem}")
    voices = client.get("/v1/audio/voices").json()["voices"]
    assert voices and all(v["id"] == v["voice_id"] for v in voices)
    by_id = {v["id"]: v["name"] for v in voices}
    assert by_id[profile] == f"Listed {profiles.stem}"
    assert by_id["nova"] == "Nova"
