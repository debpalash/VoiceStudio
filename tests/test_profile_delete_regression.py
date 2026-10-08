import sqlite3

import pytest


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from core import db
    from api.routers import profiles
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path))
    db.init_db()
    names = ["ref.wav", "locked.wav", "consent.wav", "voice.portrait.jpg"]
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name,ref_audio_path,locked_audio_path,consent_audio_path) VALUES('voice','Voice',?,?,?)", names[:3])
        conn.execute("INSERT INTO generation_history(id,profile_id) VALUES('history','voice')")
    return tmp_path, names


@pytest.mark.parametrize("table,operation", [("voice_profiles", "DELETE"), ("generation_history", "UPDATE")])
def test_rejected_delete_keeps_assets_and_history(profile, table, operation):
    from core import db
    from api.routers import profiles
    root, names = profile
    with db.db_conn() as conn:
        conn.execute(f"CREATE TRIGGER reject_delete BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test write failure"):
        profiles.delete_profile("voice")
    assert [(root / name).read_bytes() for name in names] == [name.encode() for name in names]
    with db.db_conn() as conn:
        assert conn.execute("SELECT profile_id FROM generation_history").fetchone()[0] == "voice"
        assert conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] == 1


def test_committed_delete_cleans_all_assets(profile):
    from core import db
    from api.routers import profiles
    root, names = profile
    assert profiles.delete_profile("voice") == {"deleted": "voice"}
    assert all(not (root / name).exists() for name in names)
    with db.db_conn() as conn:
        assert conn.execute("SELECT profile_id FROM generation_history").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] == 0


def test_cleanup_error_after_commit_does_not_stop_other_assets(profile, monkeypatch, caplog):
    from core import db
    from api.routers import profiles
    root, names = profile
    real_remove = profiles.os.remove
    def remove(path):
        if str(path) == str(root / "ref.wav"):
            raise PermissionError("busy")
        return real_remove(path)
    monkeypatch.setattr(profiles.os, "remove", remove)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(profiles.router)
    response = TestClient(app).delete("/profiles/voice")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "profile record was deleted" in detail
    assert "cleanup is incomplete" in detail and "ref.wav" in detail
    assert str(root) not in detail
    assert str(root / "ref.wav") in caplog.text
    assert (root / "ref.wav").exists()
    assert all(not (root / name).exists() for name in names[1:])
    with db.db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] == 0


def test_commit_failure_keeps_assets_and_history(profile):
    from core import db
    from api.routers import profiles
    root, names = profile
    with db.db_conn() as conn:
        conn.execute("CREATE TABLE dependent_voice (profile_id TEXT REFERENCES voice_profiles(id) DEFERRABLE INITIALLY DEFERRED)")
        conn.execute("INSERT INTO dependent_voice VALUES ('voice')")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        profiles.delete_profile("voice")
    assert [(root / name).read_bytes() for name in names] == [name.encode() for name in names]
    with db.db_conn() as conn:
        assert conn.execute("SELECT profile_id FROM generation_history").fetchone()[0] == "voice"
        assert conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] == 1


def test_busy_nested_portrait_reports_confined_relative_location(profile, monkeypatch, caplog):
    from core import db
    from api.routers import profiles
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    root, names = profile
    nested = root / "portraits" / "voice.portrait.jpg"
    nested.parent.mkdir()
    (root / names[-1]).rename(nested)
    try:
        (root / names[-1]).symlink_to(nested)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    real_remove = profiles.os.remove
    def remove(path):
        if str(path) == str(nested):
            raise PermissionError("busy nested portrait")
        return real_remove(path)
    monkeypatch.setattr(profiles.os, "remove", remove)
    app = FastAPI()
    app.include_router(profiles.router)
    response = TestClient(app).delete("/profiles/voice")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "profile record was deleted" in detail and "cleanup is incomplete" in detail
    assert str(nested.relative_to(root)) in detail
    assert str(root) not in detail
    assert str(nested) in caplog.text
    assert nested.read_bytes() == names[-1].encode()
    assert all(not (root / name).exists() for name in names[:-1])
    with db.db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] == 0
        assert conn.execute("SELECT profile_id FROM generation_history").fetchone()[0] is None


def test_cleanup_log_redacts_home_prefix_and_keeps_asset_suffix(profile, monkeypatch, caplog):
    from api.routers import profiles
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    root, names = profile
    voices = root / "Users" / "cleanup-test" / "voices"
    voices.mkdir(parents=True)
    for name in names:
        (root / name).rename(voices / name)
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    # Treat this real temporary directory as the process home on every OS.
    # A nested Users component alone is not a Windows home-directory shape.
    real_expanduser = profiles.os.path.expanduser
    monkeypatch.setattr(profiles.os.path, "expanduser", lambda path: str(voices.parent) if path == "~" else real_expanduser(path))
    real_remove = profiles.os.remove
    def remove(path):
        if str(path) == str(voices / "ref.wav"):
            raise PermissionError("busy")
        return real_remove(path)
    monkeypatch.setattr(profiles.os, "remove", remove)
    app = FastAPI()
    app.include_router(profiles.router)
    assert TestClient(app).delete("/profiles/voice").status_code == 500
    logged = caplog.text.replace("\\", "/")
    assert "Users/cleanup-test" not in logged
    assert "voices/ref.wav" in logged
