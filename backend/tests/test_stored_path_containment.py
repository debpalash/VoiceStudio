"""Persisted file names and client upload names stay inside their folders.

Voice-profile rows (``ref_audio_path`` / ``locked_audio_path``), gallery rows
(``audio_path``) and call rows (``recording_path``) are read back from SQLite
and joined onto a data folder; upload names contribute an extension to a
server-generated stem. Valid values keep their exact historical spelling;
anything that resolves outside the folder is treated as absent.
"""
from __future__ import annotations

import io
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.path_security import contained_join, upload_suffix


# ── helpers ────────────────────────────────────────────────────────────────


def test_contained_join_keeps_valid_spellings(tmp_path):
    root = tmp_path / "voices"
    root.mkdir()
    assert contained_join(root, "abc.wav") == os.path.join(str(root), "abc.wav")
    absolute = str(root / "abc.wav")
    assert contained_join(root, absolute) == absolute


def test_contained_join_accepts_rows_written_through_a_symlinked_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    stored = str(link / "abc.wav")
    assert contained_join(link, stored) == stored


@pytest.mark.parametrize("value", [
    "", None, "../outside.wav", "..", "a/../../outside.wav", "..\\outside.wav", 42,
])
def test_contained_join_rejects_values_outside_the_root(tmp_path, value):
    root = tmp_path / "voices"
    root.mkdir()
    assert contained_join(root, value) is None


def test_contained_join_rejects_absolute_paths_elsewhere(tmp_path):
    root = tmp_path / "voices"
    root.mkdir()
    assert contained_join(root, str(tmp_path / "outside.wav")) is None
    assert contained_join(root, str(tmp_path / "voices-other" / "x.wav")) is None


@pytest.mark.parametrize("name, expected", [
    ("clip.wav", ".wav"), ("clip.WEBM", ".WEBM"), ("archive.tar.mp3", ".mp3"),
    ("blob", ""), ("", ""), (None, ""),
])
def test_upload_suffix_keeps_plain_extensions(name, expected):
    assert upload_suffix(name) == expected


@pytest.mark.parametrize("name", [
    "clip.wav:stream", "clip.w\\..\\x", "clip.wav\x00", "clip.wa v", "clip." + "a" * 17,
    "dir.d/clip", "clip.", "C:\\a.b\\clip",
])
def test_upload_suffix_rejects_unusable_extensions(name):
    assert upload_suffix(name) is None


# ── voice-profile rows ─────────────────────────────────────────────────────


def _profile_row(**overrides):
    row = {
        "kind": "clone", "is_locked": 0, "locked_audio_path": "",
        "ref_audio_path": "", "ref_text": "hello", "instruct": "",
        "seed": None, "language": "Auto", "vd_states": None,
    }
    row.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"? AS {k}" for k in row)
    return conn.execute(f"SELECT {cols}", tuple(row.values())).fetchone()


@pytest.fixture
def escaped_voice(tmp_path, monkeypatch):
    """A real file one level above VOICES_DIR, so os.path.isfile succeeds on
    the escaped spelling and only containment can refuse it."""
    voices = tmp_path / "voices"
    voices.mkdir()
    (tmp_path / "outside.wav").write_bytes(b"RIFF")
    (voices / "ok.wav").write_bytes(b"RIFF")
    return str(voices)


@pytest.mark.parametrize("field", ["ref_audio_path", "locked_audio_path"])
def test_generation_resolver_ignores_rows_outside_voices(escaped_voice, monkeypatch, field):
    from api.routers import generation

    monkeypatch.setattr(generation, "VOICES_DIR", escaped_voice)
    row = _profile_row(**{field: "../outside.wav", "is_locked": int(field == "locked_audio_path")})
    assert generation._resolve_profile_conditioning(row)["ref_audio_path"] is None

    ok = _profile_row(**{field: "ok.wav", "is_locked": int(field == "locked_audio_path")})
    assert generation._resolve_profile_conditioning(ok)["ref_audio_path"] == os.path.join(
        escaped_voice, "ok.wav"
    )


@contextmanager
def _fake_db(row):
    class _Conn:
        def execute(self, *_a, **_k):
            return self

        def fetchone(self):
            return row

    yield _Conn()


def _patch_db(monkeypatch, row):
    from core import db

    monkeypatch.setattr(db, "db_conn", lambda: _fake_db(row))


def test_batch_voice_refuses_rows_outside_voices(escaped_voice, monkeypatch):
    from api.routers import batch
    from core import config

    monkeypatch.setattr(config, "VOICES_DIR", escaped_voice)
    _patch_db(monkeypatch, _profile_row(ref_audio_path="../outside.wav"))
    with pytest.raises(ValueError):
        batch._batch_voice("p1")

    _patch_db(monkeypatch, _profile_row(ref_audio_path="ok.wav"))
    assert batch._batch_voice("p1")["ref_audio"] == os.path.join(escaped_voice, "ok.wav")


def test_audiobook_and_stream_resolvers_ignore_rows_outside_voices(escaped_voice, monkeypatch):
    from api.routers import audiobook, tts_stream
    from core import config

    monkeypatch.setattr(config, "VOICES_DIR", escaped_voice)
    _patch_db(monkeypatch, _profile_row(ref_audio_path="../outside.wav"))
    assert audiobook._resolve_voice("p1")["ref_audio"] is None
    assert tts_stream.build_stream_kwargs({"voice": "p1", "text": "hi"}).get("ref_audio") is None

    _patch_db(monkeypatch, _profile_row(ref_audio_path="ok.wav"))
    expected = os.path.join(escaped_voice, "ok.wav")
    assert audiobook._resolve_voice("p1")["ref_audio"] == expected
    assert tts_stream.build_stream_kwargs({"voice": "p1", "text": "hi"})["ref_audio"] == expected


# ── call recordings ────────────────────────────────────────────────────────


def test_call_recording_path_stays_in_the_calls_folder(tmp_path, monkeypatch):
    from core import config
    from services.telephony import calls

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    (tmp_path / "calls").mkdir()
    inside = tmp_path / "calls" / "c1.wav"
    inside.write_bytes(b"RIFF")
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"RIFF")

    _patch_db(monkeypatch, {"recording_path": str(outside)})
    assert calls.recording_path("c1") is None
    _patch_db(monkeypatch, {"recording_path": str(inside)})
    assert calls.recording_path("c1") == str(inside)


# ── gallery rows and uploads ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def gallery_client():
    from api.routers import gallery
    from core.db import init_db

    init_db()
    gallery._init_gallery_db()
    app = FastAPI()
    app.include_router(gallery.router)
    return TestClient(app)


def _gallery_row(audio_path: str) -> str:
    from core.db import db_conn

    voice_id = f"g{uuid.uuid4().hex[:7]}"
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_gallery
               (id, name, character, category, source_type, source_url, audio_path,
                duration, description, tags, created_at)
               VALUES (?, 'n', 'c', 'import', 'upload', '', ?, 1.0, '', '[]', ?)""",
            (voice_id, audio_path, time.time()),
        )
    return voice_id


def test_gallery_does_not_serve_or_delete_files_outside_its_folder(gallery_client, tmp_path):
    outside = tmp_path / "keep.wav"
    outside.write_bytes(b"RIFF keep")

    voice_id = _gallery_row(str(outside))
    assert gallery_client.get(f"/gallery/voices/{voice_id}/preview").status_code == 404
    deleted = gallery_client.delete(f"/gallery/voices/{voice_id}")
    assert deleted.status_code == 200
    other = _gallery_row(str(outside))
    batch = gallery_client.post("/gallery/voices/batch-delete", json={"ids": [other]})
    assert batch.json()["deleted"] == 1
    assert outside.read_bytes() == b"RIFF keep"


def test_gallery_preview_still_serves_its_own_files(gallery_client):
    from api.routers import gallery

    gallery.VOICE_GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    path = gallery.VOICE_GALLERY_DIR / f"{uuid.uuid4().hex[:8]}.wav"
    path.write_bytes(b"RIFF ok")
    voice_id = _gallery_row(str(path))
    response = gallery_client.get(f"/gallery/voices/{voice_id}/preview")
    assert response.status_code == 200
    assert response.content == b"RIFF ok"


def test_gallery_upload_rejects_unusable_extensions(gallery_client):
    from api.routers import gallery

    before = set(Path(gallery.VOICE_GALLERY_DIR).glob("*"))
    response = gallery_client.post(
        "/gallery/upload",
        data={"name": "x"},
        files={"audio": ("clip.wav:stream", io.BytesIO(b"RIFF" + bytes(64)), "audio/wav")},
    )
    assert response.status_code == 415
    assert set(Path(gallery.VOICE_GALLERY_DIR).glob("*")) == before


# ── profile uploads ────────────────────────────────────────────────────────


@pytest.fixture
def profiles_client(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    app = FastAPI()
    app.include_router(profiles.router)
    with TestClient(app) as client:
        yield client, tmp_path


def test_profile_upload_rejects_unusable_extensions(profiles_client):
    client, tmp_path = profiles_client
    response = client.post("/profiles", data={"name": "Scarlet"}, files={
        "ref_audio": ("voice.wav:stream", b"RIFF" + bytes(2000), "audio/wav"),
    })
    assert response.status_code == 415
    assert client.get("/profiles").json() == []
    voices = tmp_path / "voices"
    assert not voices.exists() or not any(voices.iterdir())
