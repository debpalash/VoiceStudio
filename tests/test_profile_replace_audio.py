"""Replacing a saved clone's reference clip in place (#2282)."""

import io
import os
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def wav_bytes(frames: int = 4000, rate: int = 16000) -> bytes:
    data = io.BytesIO()
    with wave.open(data, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x01\x00" * frames)
    return data.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    voices = tmp_path / "voices"
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    transcribed = []

    async def fake_transcribe(path):
        transcribed.append(path)
        return "auto transcript"

    monkeypatch.setattr(profiles, "_auto_transcribe_reference", fake_transcribe)
    events = []
    monkeypatch.setattr(profiles.event_bus, "emit", lambda *a: events.append(a))
    app = FastAPI()
    app.include_router(profiles.router)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        data={"name": "Scarlet", "ref_text": "old words", "instruct": "female"},
        files={"ref_audio": ("voice.wav", wav_bytes(), "audio/wav")},
    ).json()
    return client, profiles, db, voices, created, transcribed, events


def replace(client, profile_id, name="new.wav", body=None, text=None):
    data = {"ref_text": text} if text is not None else {}
    return client.put(
        f"/profiles/{profile_id}/audio",
        data=data,
        files={"ref_audio": (name, body if body is not None else wav_bytes(6000), "audio/wav")},
    )


def test_replace_writes_new_versioned_file_and_removes_old(env):
    client, _profiles, _db, voices, created, transcribed, events = env
    old_name = created["ref_audio_path"]
    assert created["audio_url"].startswith(f"/profiles/{created['id']}/audio?v=")

    response = replace(client, created["id"], text="  brand new words  ")

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["id"] == created["id"]
    assert updated["name"] == "Scarlet" and updated["instruct"] == "female"
    assert updated["ref_text"] == "brand new words"
    assert transcribed == []
    new_name = updated["ref_audio_path"]
    # A new filename is the cache key every engine/chapter cache uses.
    assert new_name != old_name
    assert new_name.startswith(f"{created['id']}-") and new_name.endswith(".wav")
    assert updated["audio_url"] != created["audio_url"]
    assert (voices / new_name).read_bytes() == wav_bytes(6000)
    assert not (voices / old_name).exists()
    assert not list(voices.glob("*.part"))
    served = client.get(f"/profiles/{created['id']}/audio")
    assert served.status_code == 200 and served.content == wav_bytes(6000)
    assert events[-1] == ("profiles", {"action": "updated", "id": created["id"]})
    assert client.get("/profiles").json()[0]["audio_url"] == updated["audio_url"]


def test_blank_transcript_is_auto_transcribed_not_carried_over(env):
    client, _profiles, _db, voices, created, transcribed, _events = env
    updated = replace(client, created["id"], name="take.FLAC", text="   ").json()
    assert updated["ref_text"] == "auto transcript"
    assert updated["ref_audio_path"].endswith(".flac")
    assert transcribed == [str(voices / updated["ref_audio_path"])]


def test_replace_clears_lock_and_consent(env):
    client, _profiles, db, voices, created, _transcribed, _events = env
    pid = created["id"]
    (voices / f"{pid}_locked.wav").write_bytes(wav_bytes())
    (voices / f"{pid}_consent.wav").write_bytes(wav_bytes())
    with db.db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET locked_audio_path=?, is_locked=1, seed=7, "
            "verified_own_voice=1, consent_text='I consent', consent_audio_path=?, "
            "consent_recorded_at=1.0 WHERE id=?",
            (f"{pid}_locked.wav", f"{pid}_consent.wav", pid),
        )

    updated = replace(client, pid, text="words").json()

    assert updated["locked_audio_path"] == "" and not updated["is_locked"]
    assert updated["seed"] is None
    assert not updated["verified_own_voice"]
    assert updated["consent_text"] == "" and updated["consent_audio_path"] == ""
    assert updated["consent_recorded_at"] is None
    assert not (voices / f"{pid}_locked.wav").exists()
    assert not (voices / f"{pid}_consent.wav").exists()
    # The served clip is the new reference, not the discarded locked take.
    assert client.get(f"/profiles/{pid}/audio").content == wav_bytes(6000)


def test_design_profile_is_refused(env):
    client, _profiles, db, _voices, created, _transcribed, _events = env
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET kind='design' WHERE id=?", (created["id"],))
    assert replace(client, created["id"]).status_code == 409


@pytest.mark.parametrize("name", ["evil.exe", "noext", "clip.wav.php"])
def test_unsupported_extension_is_refused(env, name):
    client, _profiles, _db, voices, created, _transcribed, _events = env
    before = sorted(p.name for p in voices.iterdir())
    assert replace(client, created["id"], name=name).status_code == 415
    assert sorted(p.name for p in voices.iterdir()) == before


def test_undecodable_payload_is_refused_and_cleaned(env, monkeypatch):
    client, profiles, _db, voices, created, _transcribed, _events = env

    async def no_audio(_path):
        return False

    monkeypatch.setattr(profiles, "_ffprobe_has_audio", no_audio)
    before = sorted(p.name for p in voices.iterdir())
    response = replace(client, created["id"], body=b"not audio at all" * 200)
    assert response.status_code == 422
    assert sorted(p.name for p in voices.iterdir()) == before
    assert client.get(f"/profiles/{created['id']}").json()["ref_audio_path"] == created[
        "ref_audio_path"
    ]


def test_too_short_payload_is_refused(env):
    client, _profiles, _db, _voices, created, _transcribed, _events = env
    assert replace(client, created["id"], body=b"RIFF").status_code == 422


def test_missing_profile_is_404(env):
    client, *_ = env
    assert replace(client, "nope1234").status_code == 404
    assert replace(client, "bad.id").status_code == 404


def test_db_failure_rolls_back_new_file_and_keeps_old(env, monkeypatch):
    client, profiles, db, voices, created, _transcribed, _events = env
    real_conn = db.db_conn

    class FailingUpdate:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.lstrip().startswith("UPDATE voice_profiles SET ref_audio_path"):
                raise RuntimeError("disk full")
            return self._conn.execute(sql, *args)

    from contextlib import contextmanager

    @contextmanager
    def failing_conn():
        with real_conn() as conn:
            yield FailingUpdate(conn)

    monkeypatch.setattr(profiles, "db_conn", failing_conn)
    before = sorted(p.name for p in voices.iterdir())
    with pytest.raises(RuntimeError):
        replace(client, created["id"], text="words")
    assert sorted(p.name for p in voices.iterdir()) == before
    monkeypatch.setattr(profiles, "db_conn", real_conn)
    assert client.get(f"/profiles/{created['id']}").json()["ref_audio_path"] == created[
        "ref_audio_path"
    ]


def test_shared_reference_file_is_not_deleted(env):
    client, _profiles, db, voices, created, _transcribed, _events = env
    old_name = created["ref_audio_path"]
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, ref_audio_path, kind) VALUES ('twin0001', 'Twin', ?, 'clone')",
            (old_name,),
        )
    assert replace(client, created["id"], text="words").status_code == 200
    assert (voices / old_name).exists()


def test_create_profile_still_auto_transcribes_blank_transcript(env):
    client, _profiles, _db, voices, _created, transcribed, _events = env
    made = client.post(
        "/profiles",
        data={"name": "Blank"},
        files={"ref_audio": ("voice.wav", wav_bytes(), "audio/wav")},
    ).json()
    assert made["ref_text"] == "auto transcript"
    assert transcribed[-1] == os.path.join(str(voices), made["ref_audio_path"])
