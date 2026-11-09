"""Consent attestation and the rule that an unattested voice is never pushed (S2 prep)."""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import db as core_db
from services.cloud_sync.voices import voice_sync_content


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
        conn.execute(
            "INSERT INTO voice_profiles (id, name, ref_audio_path, ref_text, language, seed, kind, created_at, sync_id)"
            " VALUES ('ab12cd34', 'Warm narrator', '/home/me/voices/ab12cd34.wav', 'The quick brown fox', 'en',"
            " 1234, 'clone', 1759312800, 'Sy9xC7vB5nM3qW1e')"
        )
    from api.routers import profiles

    app = FastAPI()
    app.include_router(profiles.router)
    return TestClient(app, client=("127.0.0.1", 5000))


def _row(conn_path):
    with sqlite3.connect(conn_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM voice_profiles WHERE id='ab12cd34'").fetchone())


def test_attesting_records_the_basis_and_time(client):
    response = client.post("/profiles/ab12cd34/attestation", json={"basis": "speaker_permission"})
    assert response.status_code == 200
    body = response.json()
    assert body["consent_basis"] == "speaker_permission"
    assert body["consent_attested_at"].endswith("Z")
    assert _row(core_db.DB_PATH)["consent_basis"] == "speaker_permission"


def test_an_unknown_basis_or_voice_is_refused(client):
    assert client.post("/profiles/ab12cd34/attestation", json={"basis": "trust_me"}).status_code == 422
    assert client.post("/profiles/nope/attestation", json={"basis": "own_voice"}).status_code == 404


def test_clearing_the_attestation(client):
    client.post("/profiles/ab12cd34/attestation", json={"basis": "own_voice"})
    assert client.delete("/profiles/ab12cd34/attestation").status_code == 200
    row = _row(core_db.DB_PATH)
    assert (row["consent_basis"], row["consent_attested_at"]) == ("", None)


def test_an_unattested_voice_has_no_sync_content(client):
    assert voice_sync_content(_row(core_db.DB_PATH)) is None


def test_attested_voice_content_matches_the_collection_and_holds_no_local_detail(client):
    client.post("/profiles/ab12cd34/attestation", json={"basis": "own_voice"})
    content = voice_sync_content(_row(core_db.DB_PATH))
    assert content["name"] == "Warm narrator"
    assert content["kind"] == "clone" and content["language"] == "en" and content["seed"] == 1234
    assert content["ref_text"] == "The quick brown fox"
    assert content["created_at"] == "2025-10-01T10:00:00Z"
    assert content["consent"]["attested"] is True
    assert content["consent"]["basis"] == "own_voice"
    assert content["consent"]["attested_at"].endswith("Z")
    flat = repr(content)
    assert "ab12cd34" not in flat and "/home/me" not in flat


def test_a_recorded_own_voice_consent_counts_as_attested(client):
    with sqlite3.connect(core_db.DB_PATH) as conn:
        conn.execute(
            "UPDATE voice_profiles SET verified_own_voice=1, consent_text='I confirm this is my voice.',"
            " consent_recorded_at=1759312800 WHERE id='ab12cd34'"
        )
    content = voice_sync_content(_row(core_db.DB_PATH))
    assert content["consent"] == {
        "attested": True, "attested_at": "2025-10-01T10:00:00Z", "basis": "own_voice",
        "consent_text": "I confirm this is my voice.",
    }


def test_replacing_the_reference_audio_clears_the_sync_consent(client):
    """A new speaker needs a new confirmation: the old one must not carry over."""
    from api.routers import profiles

    client.post("/profiles/ab12cd34/attestation", json={"basis": "speaker_permission"})
    with sqlite3.connect(core_db.DB_PATH) as conn:
        current = conn.execute("SELECT ref_audio_path FROM voice_profiles WHERE id='ab12cd34'").fetchone()[0]
        conn.execute(profiles._REPLACE_AUDIO_SQL, ("new.wav", "new text", None, None, None, None, "ab12cd34", current))
    row = _row(core_db.DB_PATH)
    assert (row["consent_basis"], row["consent_attested_at"]) == ("", None)
    assert voice_sync_content(row) is None
