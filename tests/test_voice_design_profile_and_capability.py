"""Voice Design: edits must reach the engine, and only engines that can design
are asked to (Discord reports).

1. Once a designed voice was saved or selected, every take sent its
   ``profile_id``. The backend then cloned the profile's saved sample, so new
   attributes, gender and seed barely mattered ("always a female voice"). A
   request whose instruct or seed differs from the design profile's now
   designs from the request; an unchanged one still re-renders the saved
   voice from its sample.
2. Design could start on engines that need a reference clip (IndexTTS2, MOSS
   v1.5, dots, Confucius4, Supertonic, GGUF) and failed inside the engine.
   The catalogue now exposes ``supports_voice_design`` and ``/generate``
   refuses a design request on those engines with a 422.
"""
import importlib
import os
import uuid

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _gen():
    return importlib.import_module("api.routers.generation")


def _tts():
    return importlib.import_module("services.tts_backend")


def _design_row(**overrides):
    row = {
        "kind": "design", "instruct": "male, low pitch", "vd_states": None,
        "seed": 42, "ref_audio_path": "design-sample.wav", "ref_text": "Sample.",
        "is_locked": 0, "locked_audio_path": "", "language": "Auto",
    }
    row.update(overrides)
    return row


# ── 1. an edited design must not clone the saved sample ─────────────────────


def test_unchanged_design_rerenders_from_its_saved_sample():
    cond = _gen()._resolve_profile_conditioning(
        _design_row(), instruct="low pitch, male", seed=42,
    )
    assert cond["ref_audio_path"] and cond["ref_audio_path"].endswith("design-sample.wav")
    assert not cond["diverged"]


def test_omitted_instruct_and_seed_mean_the_profiles():
    cond = _gen()._resolve_profile_conditioning(_design_row())
    assert cond["ref_audio_path"]
    assert cond["instruct"] == "male, low pitch"
    assert cond["seed"] == 42


def test_a_changed_instruct_designs_from_the_request():
    cond = _gen()._resolve_profile_conditioning(
        _design_row(), instruct="female, high pitch", seed=42,
    )
    assert cond["ref_audio_path"] is None
    assert cond["instruct"] == "female, high pitch"
    assert cond["diverged"]


def test_a_changed_seed_designs_from_the_request_with_the_profiles_instruct():
    cond = _gen()._resolve_profile_conditioning(_design_row(), seed=7)
    assert cond["ref_audio_path"] is None
    assert cond["instruct"] == "male, low pitch"
    assert cond["seed"] == 7


def test_an_explicit_seed_designs_when_the_profile_has_none():
    row = _design_row(seed=None)
    cond = _gen()._resolve_profile_conditioning(row, seed=7)
    assert cond["ref_audio_path"] is None
    assert cond["seed"] == 7
    assert cond["diverged"]
    # An omitted seed still means the profile's, so the sample re-renders.
    assert not _gen()._resolve_profile_conditioning(row)["diverged"]


def test_a_locked_design_take_is_not_cloned_once_edited():
    row = _design_row(is_locked=1, locked_audio_path="locked.wav")
    assert _gen()._resolve_profile_conditioning(row)["ref_audio_path"].endswith("locked.wav")
    assert _gen()._resolve_profile_conditioning(row, instruct="female")["ref_audio_path"] is None


def test_a_clone_profile_keeps_its_reference_with_a_style_instruct():
    row = _design_row(kind="clone", instruct="", ref_audio_path="clip.wav")
    cond = _gen()._resolve_profile_conditioning(row, instruct="whisper", seed=9)
    assert cond["ref_audio_path"].endswith("clip.wav")


# ── 2. design-capability gate ───────────────────────────────────────────────


def test_catalogue_reports_voice_design_support():
    entries = {e["id"]: e for e in _tts().list_backends(include_hidden=True)}
    assert entries["omnivoice"]["supports_voice_design"] is True
    assert entries["voxcpm2"]["supports_voice_design"] is True
    for bid in ("indextts2", "moss-tts-v15", "dots-tts", "confucius4-tts",
                "supertonic3", "omnivoice-gguf", "gpt-sovits", "moss-tts-nano"):
        if bid in entries:
            assert entries[bid]["supports_voice_design"] is False, bid
    for bid, entry in entries.items():
        assert entry["supports_voice_design"] in (True, False, None), bid


def _engine(design, engine_id, *, cloning=True):
    class _Fake(_tts().TTSBackend):
        id = engine_id
        display_name = "Reference-only engine (test)"
        supports_voice_design = design
        supports_cloning = cloning
        applies_own_mastering = False
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

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append(kw)
            return torch.zeros(1, 2400)

    _Fake.calls = []
    return _Fake


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def profiles():
    from core.db import db_conn, init_db

    init_db()
    created = []

    def make(kind, *, sample=False, instruct="male, low pitch", seed=42):
        import soundfile as sf
        from core.config import VOICES_DIR

        pid = f"vd-{uuid.uuid4().hex[:8]}"
        rel = None
        if sample:
            rel = f"{pid}.wav"
            os.makedirs(VOICES_DIR, exist_ok=True)
            sf.write(os.path.join(VOICES_DIR, rel), [0.0] * 24000, 24000)
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO voice_profiles (id, name, kind, instruct, seed, "
                "ref_audio_path, ref_text, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, "Narrator", kind, instruct, seed, rel, "Hello there.", 0.0),
            )
        created.append((pid, rel))
        return pid

    yield make
    from core.config import VOICES_DIR

    with db_conn() as conn:
        for pid, rel in created:
            conn.execute("DELETE FROM generation_history WHERE profile_id=?", (pid,))
            conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))
            if rel:
                try:
                    os.remove(os.path.join(VOICES_DIR, rel))
                except OSError:
                    pass


@pytest.mark.parametrize("field", [{"instruct": "male"}, {"design_recipe": '{"description":"deep","picks":{}}'}])
def test_design_request_on_an_engine_that_cannot_design_is_a_clear_422(client, monkeypatch, field):
    fake = _engine(False, "fake-ref-only-design")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, **field})
    assert res.status_code == 422, res.text
    assert "can't design" in res.json()["detail"]
    assert fake.calls == []


def test_design_profile_without_a_sample_is_refused_too(client, monkeypatch, profiles):
    fake = _engine(False, "fake-ref-only-profile")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    pid = profiles("design")
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "profile_id": pid})
    assert res.status_code == 422, res.text


def test_plain_tts_and_cloning_a_design_sample_stay_allowed(client, monkeypatch, profiles):
    fake = _engine(False, "fake-ref-only-allowed")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    assert client.post("/generate", data={"text": "Hello", "engine": fake.id}).status_code == 200
    pid = profiles("design", sample=True)
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "profile_id": pid})
    assert res.status_code == 200, res.text
    assert fake.calls[-1].get("ref_audio")


@pytest.mark.parametrize("design", [False, None])
def test_a_saved_sample_is_refused_on_an_engine_that_cannot_clone(
    client, monkeypatch, profiles, design
):
    # KittenTTS / Supertonic-3 ignore ref_audio: re-rendering a saved voice
    # there would silently speak with a preset voice instead.
    fake = _engine(design, f"fake-preset-only-{design}", cloning=False)
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    for kind in ("design", "clone"):
        pid = profiles(kind, sample=True)
        res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "profile_id": pid})
        assert res.status_code == 422, (kind, res.text)
        assert "can't use reference audio" in res.json()["detail"]
    upload = client.post(
        "/generate", data={"text": "Hello", "engine": fake.id},
        files={"ref_audio": ("ref.wav", b"RIFF0000WAVE", "audio/wav")},
    )
    assert upload.status_code == 422, upload.text
    assert fake.calls == []
    assert client.post("/generate", data={"text": "Hello", "engine": fake.id}).status_code == 200


def test_preset_only_engines_declare_they_cannot_clone():
    entries = {e["id"]: e for e in _tts().list_backends(include_hidden=True)}
    for bid in ("kittentts", "supertonic3", "sherpa-onnx"):
        if bid in entries:
            assert entries[bid]["supports_cloning"] is False, bid


def test_undeclared_engines_may_still_design(client, monkeypatch):
    fake = _engine(None, "fake-undeclared")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "instruct": "male"})
    assert res.status_code == 200, res.text


def test_an_edited_design_reaches_the_engine_and_is_not_filed_under_the_profile(
    client, monkeypatch, profiles
):
    from core.db import db_conn

    fake = _engine(True, "fake-designer")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    pid = profiles("design", sample=True)

    same = client.post("/generate", data={
        "text": "Hello", "engine": fake.id, "profile_id": pid,
        "instruct": "male, low pitch", "seed": "42",
    })
    assert same.status_code == 200, same.text
    assert fake.calls[-1].get("ref_audio")

    edited = client.post("/generate", data={
        "text": "Hello", "engine": fake.id, "profile_id": pid,
        "instruct": "female", "seed": "42",
    })
    assert edited.status_code == 200, edited.text
    assert not fake.calls[-1].get("ref_audio")
    assert fake.calls[-1].get("instruct") == "female"
    with db_conn() as conn:
        takes = conn.execute(
            "SELECT instruct FROM generation_history WHERE profile_id=?", (pid,)
        ).fetchall()
    assert [t["instruct"] for t in takes] == ["male, low pitch"]
