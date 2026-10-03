"""Files are removed/replaced only after the database change that owns them
commits (same class as #2483, for sibling history/lock/consent paths)."""

import os
import sqlite3

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


# ── generation history ──────────────────────────────────────────────────────

@pytest.fixture
def history(tmp_path, monkeypatch):
    import api.routers.generation as gen

    dbf = tmp_path / "takes.db"

    def connect():
        conn = sqlite3.connect(str(dbf))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setitem(gen.ensure_schema.__globals__, "get_db", connect)
    gen.ensure_schema()
    outdir = tmp_path / "outputs"
    outdir.mkdir()
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(outdir))
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: 1)
    wavs = []
    with connect() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO generation_history (id, text, mode, audio_path, created_at) "
                "VALUES (?, 't', 'clone', ?, ?)", (f"t{i}", f"t{i}.wav", float(i)),
            )
            wav = outdir / f"t{i}.wav"
            wav.write_bytes(b"RIFFfake")
            wavs.append(wav)
        # A deferred FK that every history delete violates: the DELETE
        # statement succeeds but the COMMIT is rejected.
        conn.execute(
            "CREATE TABLE guard (h TEXT REFERENCES generation_history(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
        conn.executemany("INSERT INTO guard VALUES (?)", [("t0",), ("t1",), ("t2",)])
    return gen, dbf, wavs


def test_delete_single_take_keeps_wav_when_commit_fails(history):
    gen, dbf, wavs = history
    with pytest.raises(sqlite3.IntegrityError):
        gen.delete_single_history("t0")
    assert all(w.exists() for w in wavs)


def test_clear_history_keeps_wavs_when_commit_fails(history):
    gen, dbf, wavs = history
    with pytest.raises(sqlite3.IntegrityError):
        gen.clear_history()
    assert all(w.exists() for w in wavs)


def test_prune_keeps_wavs_when_commit_fails(history):
    gen, dbf, wavs = history
    with pytest.raises(sqlite3.IntegrityError):
        gen._prune_history_over_cap()
    assert all(w.exists() for w in wavs)


def test_history_deletes_still_remove_wavs_on_success(history):
    gen, dbf, wavs = history
    with sqlite3.connect(str(dbf)) as conn:
        conn.execute("DROP TABLE guard")
    assert gen._prune_history_over_cap() == 2  # cap 1
    assert [w.exists() for w in wavs] == [False, False, True]
    gen.delete_single_history("t2")
    assert not wavs[2].exists()


# ── profile lock / unlock / consent ─────────────────────────────────────────

@pytest.fixture
def profile(tmp_path, monkeypatch):
    from core import db
    from api.routers import profiles

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path))
    db.init_db()
    (tmp_path / "voice_locked.wav").write_bytes(b"old-locked")
    (tmp_path / "voice_consent.wav").write_bytes(b"old-consent")
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles(id,name,locked_audio_path,is_locked,consent_audio_path,"
            "consent_text,verified_own_voice) VALUES('voice','V','voice_locked.wav',1,"
            "'voice_consent.wav','old text',1)"
        )
        conn.execute(
            "CREATE TRIGGER reject_update BEFORE UPDATE ON voice_profiles "
            "BEGIN SELECT RAISE(ABORT, 'test write failure'); END"
        )
    return tmp_path


def test_unlock_keeps_locked_take_when_update_fails(profile):
    import asyncio
    from api.routers import profiles

    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        asyncio.run(profiles.unlock_profile("voice"))
    assert (profile / "voice_locked.wav").read_bytes() == b"old-locked"


def test_unlock_removes_locked_take_after_commit(profile):
    import asyncio
    from core import db
    from api.routers import profiles

    with db.db_conn() as conn:
        conn.execute("DROP TRIGGER reject_update")
    asyncio.run(profiles.unlock_profile("voice"))
    assert not (profile / "voice_locked.wav").exists()


def test_rerecord_consent_failure_keeps_previous_recording(profile):
    import asyncio
    import io
    from starlette.datastructures import UploadFile
    from api.routers import profiles

    for ext in ("webm", "wav"):  # extension change and in-place overwrite
        upload = UploadFile(io.BytesIO(b"x" * 2000), filename=f"c.{ext}")
        with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
            asyncio.run(profiles.record_consent("voice", upload, "new text"))
        assert (profile / "voice_consent.wav").read_bytes() == b"old-consent"
        assert sorted(p.name for p in profile.iterdir() if "consent" in p.name) == ["voice_consent.wav"]


def test_rerecord_consent_replaces_in_place_after_commit(profile):
    import asyncio
    import io
    from core import db
    from starlette.datastructures import UploadFile
    from api.routers import profiles

    with db.db_conn() as conn:
        conn.execute("DROP TRIGGER reject_update")
    upload = UploadFile(io.BytesIO(b"n" * 2000), filename="c.wav")
    asyncio.run(profiles.record_consent("voice", upload, "new text"))
    assert (profile / "voice_consent.wav").read_bytes() == b"n" * 2000
    assert not (profile / "voice_consent.wav.part").exists()


def test_lock_failure_keeps_previous_locked_take(profile, monkeypatch):
    import asyncio
    from core import db
    from api.routers import profiles

    outputs = profile / "outputs"
    outputs.mkdir()
    (outputs / "take.wav").write_bytes(b"new-take")
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO generation_history(id, text, audio_path) VALUES('h','t','take.wav')"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        asyncio.run(profiles.lock_profile("voice", history_id="h", seed=1))
    assert (profile / "voice_locked.wav").read_bytes() == b"old-locked"
    assert not (profile / "voice_locked.wav.part").exists()


def test_first_lock_failure_leaves_no_orphan_take(profile, monkeypatch):
    import asyncio
    from core import db
    from api.routers import profiles

    (profile / "voice_locked.wav").unlink()
    outputs = profile / "outputs"
    outputs.mkdir()
    (outputs / "take.wav").write_bytes(b"new-take")
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO generation_history(id, text, audio_path) VALUES('h','t','take.wav')"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        asyncio.run(profiles.lock_profile("voice", history_id="h", seed=1))
    assert sorted(p.name for p in profile.iterdir() if "locked" in p.name) == []


def test_successful_relock_installs_new_take_and_retains_prior_version(profile, monkeypatch):
    import asyncio
    from core import db
    from api.routers import profiles

    outputs = profile / "outputs"
    outputs.mkdir()
    (outputs / "take.wav").write_bytes(b"new-take")
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    with db.db_conn() as conn:
        conn.execute("DROP TRIGGER reject_update")
        conn.execute(
            "INSERT INTO generation_history(id, text, audio_path) VALUES('h','t','take.wav')"
        )
    result = asyncio.run(profiles.lock_profile("voice", history_id="h", seed=1))
    current = result["locked_audio_path"]
    assert current != "voice_locked.wav"
    assert (profile / current).read_bytes() == b"new-take"
    assert (profile / "voice_locked.wav").read_bytes() == b"old-locked"
    assert sorted(p.name for p in profile.iterdir() if "locked" in p.name) == sorted([current, "voice_locked.wav"])


def test_install_staged_restores_previous_file(tmp_path):
    from api.routers import profiles

    target = tmp_path / "a.wav"
    target.write_bytes(b"old")
    staged = tmp_path / "a.wav.part"
    staged.write_bytes(b"new")
    restore, finalize = profiles._install_staged(str(staged), str(target))
    assert target.read_bytes() == b"new"
    restore()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav"]
