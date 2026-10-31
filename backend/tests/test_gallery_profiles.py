"""Gallery-import profile materialization contracts."""
from __future__ import annotations

import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import gallery
from core.db import db_conn, init_db


@pytest.fixture(scope="module")
def client():
    init_db()
    gallery._init_gallery_db()
    app = FastAPI()
    app.include_router(gallery.router)
    # Loopback peer so the router's require_consumer / require_admin gates
    # (which reject a non-loopback, credential-less TestClient host) let these
    # handler tests through. Authorization itself is asserted separately in
    # test_gallery_authorization below.
    return TestClient(app, client=("127.0.0.1", 50000))


def _gallery_voice(
    suffix: str = ".wav", content: bytes = b"RIFF imported voice",
) -> tuple[str, Path]:
    voice_id = f"g{uuid.uuid4().hex[:7]}"
    path = gallery.VOICE_GALLERY_DIR / f"{voice_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_gallery
               (id, name, character, category, source_type, source_url, audio_path,
                duration, description, tags, created_at)
               VALUES (?, ?, ?, 'import', 'youtube', ?, ?, 5.0, ?, '[]', ?)""",
            (
                voice_id, "Imported narrator", "Video title is not an instruct",
                "https://example.invalid/source", str(path),
                "Source URL/notes are not a spoken transcript", time.time(),
            ),
        )
    return voice_id, path


def test_save_as_profile_keeps_import_metadata_out_of_tts_fields(client):
    voice_id, _ = _gallery_voice()
    response = client.post(
        f"/gallery/voices/{voice_id}/save-as-profile",
        params={"profile_name": "Reusable import"},
    )
    assert response.status_code == 200
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (response.json()["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert row["personality"] == f"gallery:{voice_id}"
    assert row["ref_text"] == ""
    assert row["instruct"] == ""
    assert row["description"] == "Source URL/notes are not a spoken transcript"


def test_to_profile_uses_live_schema_and_clone_metadata(client):
    voice_id, _ = _gallery_voice()
    response = client.post(f"/gallery/voices/{voice_id}/to-profile")
    assert response.status_code == 200
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (response.json()["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert row["personality"] == f"gallery:{voice_id}"
    assert row["ref_text"] == row["instruct"] == ""
    assert row["description"] == "Source URL/notes are not a spoken transcript"


def test_both_import_routes_share_one_idempotent_profile(client, monkeypatch):
    emitted = []
    monkeypatch.setattr(
        gallery.event_bus, "emit", lambda topic, payload: emitted.append((topic, payload)),
    )
    voice_id, _ = _gallery_voice()
    first = client.post(
        f"/gallery/voices/{voice_id}/save-as-profile",
        params={"profile_name": "One reusable profile"},
    )
    repeated = client.post(
        f"/gallery/voices/{voice_id}/save-as-profile",
        params={"profile_name": "Ignored duplicate name"},
    )
    alternate = client.post(f"/gallery/voices/{voice_id}/to-profile")

    assert first.status_code == repeated.status_code == alternate.status_code == 200
    assert {
        first.json()["profile_id"],
        repeated.json()["profile_id"],
        alternate.json()["profile_id"],
    } == {first.json()["profile_id"]}
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_profiles WHERE personality=?",
            (f"gallery:{voice_id}",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "One reusable profile"
    assert rows[0]["kind"] == "clone" and rows[0]["vd_states"] is None
    assert emitted[-1] == (
        "profiles", {"action": "updated", "id": first.json()["profile_id"]},
    )


def test_gallery_profile_repairs_a_missing_copy_without_duplication(client):
    voice_id, source = _gallery_voice()
    first = client.post(f"/gallery/voices/{voice_id}/to-profile")
    assert first.status_code == 200
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (first.json()["profile_id"],),
        ).fetchone()
    copied = Path(gallery.VOICES_DIR) / row["ref_audio_path"]
    copied.unlink()

    repaired = client.post(f"/gallery/voices/{voice_id}/to-profile")

    assert repaired.status_code == 200
    assert repaired.json()["profile_id"] == first.json()["profile_id"]
    assert copied.read_bytes() == source.read_bytes()


def test_gallery_profile_does_not_rewrite_a_namespaced_import_collision(client):
    voice_id, source = _gallery_voice()
    collision_id = f"c{uuid.uuid4().hex[:7]}"
    personality = f"gallery:{voice_id}"
    collision_name = gallery._gallery_profile_audio_filename(collision_id, source)
    collision_audio = Path(gallery.VOICES_DIR) / collision_name
    collision_audio.parent.mkdir(parents=True, exist_ok=True)
    collision_audio.write_bytes(b"user-owned audio")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "description, kind, vd_states, is_locked, verified_own_voice, created_at) "
            "VALUES (?, 'User profile', ?, '', '', 'Auto', NULL, ?, "
            "'user-owned metadata', 'clone', NULL, 0, 0, ?)",
            (collision_id, collision_name, personality, time.time()),
        )

    response = client.post(f"/gallery/voices/{voice_id}/to-profile")

    assert response.status_code == 200
    assert response.json()["profile_id"] != collision_id
    with db_conn() as conn:
        collision = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (collision_id,),
        ).fetchone()
        created = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (response.json()["profile_id"],),
        ).fetchone()
    assert collision["description"] == "user-owned metadata"
    assert collision_audio.read_bytes() == b"user-owned audio"
    assert created["personality"] == personality


def _part_files() -> set[Path]:
    return set(Path(gallery.VOICES_DIR).glob("*.part")) | set(
        Path(gallery.VOICES_DIR).glob(".*.part")
    )


def test_audio_copy_never_holds_the_db_write_lock(client, monkeypatch):
    """The bulk file copy must happen BEFORE the BEGIN IMMEDIATE transaction.

    While the copy runs, another backend writer takes (and releases) SQLite's
    write lock. If materialization copied inside its own write transaction,
    this concurrent writer would hit `database is locked` and the test fails.
    """
    from core.config import DB_PATH

    voice_id, _ = _gallery_voice()
    real_copy2 = shutil.copy2
    concurrent_writes = []

    def copy_and_probe(src, dst, **kwargs):
        probe = sqlite3.connect(DB_PATH, timeout=0.5)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute(
                "UPDATE voice_gallery SET category = category WHERE id = ?",
                (voice_id,),
            )
            probe.commit()
            concurrent_writes.append(True)
        finally:
            probe.close()
        return real_copy2(src, dst, **kwargs)

    monkeypatch.setattr(gallery.shutil, "copy2", copy_and_probe)

    response = client.post(f"/gallery/voices/{voice_id}/to-profile")

    assert response.status_code == 200
    assert concurrent_writes == [True]
    assert _part_files() == set()


def test_failed_copy_leaves_no_temp_droppings_or_profile_row(client, monkeypatch):
    """A copy that dies mid-write must not leave .part files or a DB row."""
    voice_id, _ = _gallery_voice()

    def exploding_copy(src, dst, **kwargs):
        Path(dst).write_bytes(b"partial bytes")
        raise OSError("disk full mid-copy")

    monkeypatch.setattr(gallery.shutil, "copy2", exploding_copy)

    with pytest.raises(OSError, match="disk full mid-copy"):
        client.post(f"/gallery/voices/{voice_id}/to-profile")

    assert _part_files() == set()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_profiles WHERE personality = ?",
            (f"gallery:{voice_id}",),
        ).fetchall()
    assert rows == []


def test_gallery_preview_serves_outputs_file_without_root_relative_redirect(client):
    voice_id, source = _gallery_voice()

    response = client.get(
        f"/gallery/voices/{voice_id}/preview", follow_redirects=False,
    )

    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.content == source.read_bytes()


def test_gallery_preview_preserves_non_wav_content_type(client):
    voice_id, _ = _gallery_voice(".mp3", b"ID3 imported voice")

    response = client.get(f"/gallery/voices/{voice_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"


def test_gallery_denies_non_loopback_clients_without_credentials(monkeypatch):
    """The real gates, without any fixture override: a non-loopback caller with
    no consumer credential (default TestClient host, server mode unset) is
    rejected on both reads and mutations."""
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    init_db()
    gallery._init_gallery_db()
    app = FastAPI()
    app.include_router(gallery.router)
    with TestClient(app) as anonymous:
        assert anonymous.get("/gallery/voices").status_code == 403
        assert anonymous.delete("/gallery/voices/some-id").status_code == 403
        assert anonymous.post("/gallery/voices/batch-delete", json={"ids": []}).status_code == 403
