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

    monkeypatch.setattr(profiles, "_ffmpeg_decodes", no_audio)
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


WEBM_JUNK = b"\x1aE\xdf\xa3" * 400  # EBML magic, no decodable stream


def header_only_wav() -> bytes:
    """A valid WAV header (padded past the size floor) with no sample frames."""
    import struct

    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    junk = b"\x00" * 1200
    body = (
        b"WAVE"
        + b"JUNK" + struct.pack("<I", len(junk)) + junk
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", 0)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_header_without_frames_is_refused(env, monkeypatch):
    client, _profiles, _db, voices, created, _transcribed, _events = env
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)
    before = sorted(p.name for p in voices.iterdir())
    assert replace(client, created["id"], body=header_only_wav()).status_code == 422
    assert sorted(p.name for p in voices.iterdir()) == before


def test_unverifiable_clip_is_refused_when_ffmpeg_missing(env, monkeypatch):
    client, _profiles, _db, voices, created, _transcribed, _events = env
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)
    before = sorted(p.name for p in voices.iterdir())
    assert replace(client, created["id"], name="take.webm", body=WEBM_JUNK).status_code == 422
    assert sorted(p.name for p in voices.iterdir()) == before


def test_unverifiable_clip_is_refused_when_ffmpeg_fails(env, monkeypatch):
    client, _profiles, _db, _voices, created, _transcribed, _events = env

    async def broken_spawn(*_args, **_kwargs):
        raise OSError("ffmpeg crashed")

    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("services.ffmpeg_utils.spawn_subprocess", broken_spawn)
    assert replace(client, created["id"], name="take.webm", body=WEBM_JUNK).status_code == 422


def test_stalled_ffmpeg_check_is_killed_and_reaped(env, monkeypatch):
    """A hung decode is bounded, killed and reaped, never left running."""
    import asyncio

    client, profiles, _db, _voices, created, _transcribed, _events = env
    seen = {"killed": False, "reaped": False}

    class Stalled:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            seen["killed"] = True

        async def wait(self):
            seen["reaped"] = True
            self.returncode = -9
            return -9

    async def spawn(*_args, **_kwargs):
        return Stalled()

    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("services.ffmpeg_utils.spawn_subprocess", spawn)
    monkeypatch.setattr(profiles, "_DECODE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(profiles, "_DECODE_SEMAPHORE", None)
    assert replace(client, created["id"], name="take.webm", body=WEBM_JUNK).status_code == 422
    assert seen == {"killed": True, "reaped": True}


def test_ffmpeg_check_does_not_queue_on_export_slots(tmp_path, monkeypatch):
    """Saturated dub-export slots must not block a reference check."""
    import asyncio

    from api.routers import profiles
    from services import ffmpeg_utils

    if not ffmpeg_utils.find_ffmpeg():
        pytest.skip("ffmpeg unavailable")
    monkeypatch.setattr(profiles, "_DECODE_SEMAPHORE", None)
    good = tmp_path / "good.wav"
    good.write_bytes(wav_bytes())

    async def check():
        # Every export slot is taken for the whole check.
        monkeypatch.setattr(ffmpeg_utils, "_FFMPEG_SEMAPHORE", asyncio.Semaphore(0))
        return await asyncio.wait_for(profiles._ffmpeg_decodes(str(good)), 20)

    assert asyncio.run(check()) is True


def test_reference_decodes_are_capped(monkeypatch):
    """At most _DECODE_CONCURRENCY decodes run at once; the rest wait."""
    import asyncio

    from api.routers import profiles

    monkeypatch.setattr(profiles, "_DECODE_SEMAPHORE", None)
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    live = {"now": 0, "peak": 0}

    async def run():
        release = asyncio.Event()

        class Proc:
            returncode = None

            async def communicate(self):
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
                await release.wait()
                live["now"] -= 1
                self.returncode = 0
                return b"\x00\x01", b""

        async def spawn(*_args, **_kwargs):
            return Proc()

        monkeypatch.setattr("services.ffmpeg_utils.spawn_subprocess", spawn)
        checks = [asyncio.create_task(profiles._ffmpeg_decodes("clip.webm")) for _ in range(5)]
        for _ in range(20):
            await asyncio.sleep(0)
        assert live["now"] == profiles._DECODE_CONCURRENCY
        release.set()
        return await asyncio.gather(*checks)

    assert asyncio.run(run()) == [True] * 5
    assert live["peak"] == profiles._DECODE_CONCURRENCY


def test_cancelled_check_still_kills_and_reaps(monkeypatch):
    import asyncio

    from api.routers import profiles

    monkeypatch.setattr(profiles, "_DECODE_SEMAPHORE", None)
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    seen = {"killed": False, "reaped": False}

    class Stalled:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            seen["killed"] = True

        async def wait(self):
            seen["reaped"] = True
            self.returncode = -9
            return -9

    async def spawn(*_args, **_kwargs):
        return Stalled()

    monkeypatch.setattr("services.ffmpeg_utils.spawn_subprocess", spawn)

    async def run():
        task = asyncio.create_task(profiles._ffmpeg_decodes("clip.webm"))
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert seen == {"killed": True, "reaped": True}


def test_legacy_null_kind_profile_is_replaced(env):
    client, _profiles, db, _voices, created, _transcribed, _events = env
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET kind=NULL WHERE id=?", (created["id"],))
    response = replace(client, created["id"], text="words")
    assert response.status_code == 200, response.text
    assert response.json()["ref_audio_path"] != created["ref_audio_path"]


def test_ffmpeg_decode_needs_real_samples(tmp_path):
    import asyncio

    from api.routers import profiles
    from services.ffmpeg_utils import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg unavailable")
    good = tmp_path / "good.wav"
    good.write_bytes(wav_bytes())
    bad = tmp_path / "bad.webm"
    bad.write_bytes(WEBM_JUNK)
    assert asyncio.run(profiles._ffmpeg_decodes(str(good))) is True
    assert asyncio.run(profiles._ffmpeg_decodes(str(bad))) is False


def test_oversized_upload_is_refused_and_cleaned(env, monkeypatch):
    client, profiles, _db, voices, created, _transcribed, _events = env
    monkeypatch.setattr(profiles, "_MAX_REF_AUDIO_BYTES", 5000)
    monkeypatch.setattr(profiles, "_UPLOAD_CHUNK", 1024)
    before = sorted(p.name for p in voices.iterdir())
    assert replace(client, created["id"], text="words").status_code == 413
    assert sorted(p.name for p in voices.iterdir()) == before


def test_non_wav_reference_is_served_with_its_media_type(env):
    import soundfile as sf

    client, _profiles, _db, _voices, created, _transcribed, _events = env
    flac = io.BytesIO()
    sf.write(flac, [((i * 7919) % 2000 - 1000) / 2000 for i in range(16000)], 16000, format="FLAC")
    response = replace(client, created["id"], name="take.flac", body=flac.getvalue(), text="w")
    assert response.status_code == 200, response.text
    served = client.get(f"/profiles/{created['id']}/audio")
    assert served.status_code == 200
    assert served.headers["content-type"] == "audio/flac"


def test_profile_edits_save_with_the_clip(env):
    client, _profiles, _db, _voices, created, _transcribed, _events = env
    response = client.put(
        f"/profiles/{created['id']}/audio",
        data={"ref_text": "words", "name": "  Crimson ", "language": "French", "instruct": "male"},
        files={"ref_audio": ("new.wav", wav_bytes(6000), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert (updated["name"], updated["language"], updated["instruct"]) == (
        "Crimson", "French", "male",
    )


def test_failed_replacement_leaves_profile_edits_unsaved(env, monkeypatch):
    client, profiles, _db, _voices, created, _transcribed, _events = env

    async def no_audio(_path):
        return False

    monkeypatch.setattr(profiles, "_ffmpeg_decodes", no_audio)
    response = client.put(
        f"/profiles/{created['id']}/audio",
        data={"name": "Crimson", "instruct": "male"},
        files={"ref_audio": ("new.webm", WEBM_JUNK, "audio/webm")},
    )
    assert response.status_code == 422
    kept = client.get(f"/profiles/{created['id']}").json()
    assert (kept["name"], kept["instruct"]) == ("Scarlet", "female")


def test_blank_name_is_refused_before_upload(env):
    client, _profiles, _db, voices, created, _transcribed, _events = env
    before = sorted(p.name for p in voices.iterdir())
    response = client.put(
        f"/profiles/{created['id']}/audio",
        data={"name": "   "},
        files={"ref_audio": ("new.wav", wav_bytes(6000), "audio/wav")},
    )
    assert response.status_code == 400
    assert sorted(p.name for p in voices.iterdir()) == before


def test_concurrent_replacements_leave_no_orphan(env, monkeypatch):
    import asyncio

    import httpx

    client, profiles, _db, voices, created, _transcribed, _events = env
    pid = created["id"]

    async def slow_transcribe(_path):
        await asyncio.sleep(0.05)
        return "auto transcript"

    monkeypatch.setattr(profiles, "_auto_transcribe_reference", slow_transcribe)
    app = FastAPI()
    app.include_router(profiles.router)

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return await asyncio.gather(*(
                http.put(
                    f"/profiles/{pid}/audio",
                    files={"ref_audio": (f"take{i}.wav", wav_bytes(6000 + i), "audio/wav")},
                )
                for i in range(2)
            ))

    responses = asyncio.run(race())
    assert [r.status_code for r in responses] == [200, 200]
    final = client.get(f"/profiles/{pid}").json()["ref_audio_path"]
    assert sorted(p.name for p in voices.iterdir() if p.name.startswith(pid)) == [final]


def test_clip_changed_elsewhere_is_a_conflict_and_cleaned(env, monkeypatch):
    client, profiles, db, voices, created, _transcribed, _events = env
    pid = created["id"]

    async def rival_writes(_path):
        with db.db_conn() as conn:
            conn.execute("UPDATE voice_profiles SET ref_audio_path='rival.wav' WHERE id=?", (pid,))
        return "auto transcript"

    monkeypatch.setattr(profiles, "_auto_transcribe_reference", rival_writes)
    before = sorted(p.name for p in voices.iterdir())
    assert replace(client, pid).status_code == 409
    assert sorted(p.name for p in voices.iterdir()) == before
    assert client.get(f"/profiles/{pid}").json()["ref_audio_path"] == "rival.wav"
