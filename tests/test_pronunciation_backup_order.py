"""Dictionary backups retain chronological and same-import precedence."""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.pronunciation import router
    from core import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "pronunciation.db"))
    db.init_db()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, client=("127.0.0.1", 50000)) as native_client:
        yield native_client


def seed_bulk_import_rows(last_term):
    from core.db import db_conn
    # Actual UUID prefixes captured from a native public bulk import. A single
    # import assigns both rows the same timestamp; UUID ordering is unrelated
    # to their order in the authored JSON dictionary.
    with db_conn() as conn:
        conn.executemany("INSERT INTO pronunciation_entries "
                         "(id,term,replacement,type,language,enabled,created_at) VALUES (?,?,?,?,?,?,?)", [
            ("8ee4ed4d-dc6", "GIF", "first", "respelling", "*", 1, 1000.0),
            ("41eb97e3-95c", last_term, "last", "respelling", "*", 1, 1000.0),
        ])


@pytest.mark.parametrize("last_term", ["gif", "GIF"])
def test_preview_and_synthesis_loader_agree_on_last_imported_entry(client, last_term):
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    seed_bulk_import_rows(last_term)
    assert client.post("/pronunciation/test", json={"text": "GIF"}).json()["substituted"] == "last"
    assert apply_pronunciation("GIF", load_entries_from_db()) == "last"


@pytest.mark.parametrize("last_term", ["gif", "GIF"])
def test_dictionary_list_and_export_keep_bulk_import_order(client, last_term):
    seed_bulk_import_rows(last_term)
    assert [e["replacement"] for e in client.get("/pronunciation").json()] == ["first", "last"]
    assert [e["replacement"] for e in client.get("/pronunciation/export").json()["entries"]] == ["first", "last"]


@pytest.mark.parametrize("last_term", ["gif", "GIF"])
def test_repeated_backup_restore_keeps_pronunciation_precedence(client, last_term):
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    seed_bulk_import_rows(last_term)
    before = client.post("/pronunciation/test", json={"text": "GIF"}).json()["substituted"]
    assert before == "last"
    for _ in range(3):
        backup = client.get("/pronunciation/export").json()
        assert client.post("/pronunciation/import", json={**backup, "replace": True}).json()["imported"] == 2
        assert client.post("/pronunciation/test", json={"text": "GIF"}).json()["substituted"] == before
        assert apply_pronunciation("GIF", load_entries_from_db()) == before


def test_creation_time_still_precedes_insertion_tie_break(client):
    from core.db import db_conn
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    with db_conn() as conn:
        conn.executemany("INSERT INTO pronunciation_entries "
                         "(id,term,replacement,type,language,enabled,created_at) VALUES (?,?,?,?,?,?,?)", [
            ("a", "GIF", "newer", "respelling", "*", 1, 20.0),
            ("b", "gif", "older", "respelling", "*", 1, 10.0),
        ])
    assert apply_pronunciation("GIF", load_entries_from_db()) == "newer"
    assert client.post("/pronunciation/test", json={"text": "GIF"}).json()["substituted"] == "newer"
    assert [e["replacement"] for e in client.get("/pronunciation/export").json()["entries"]] == ["older", "newer"]


def test_unique_global_scoped_and_disabled_entries_roundtrip(client):
    entries = [{"term": "GIF", "replacement": "global", "language": "*"},
               {"term": "GIF", "replacement": "Spanish", "language": "es"},
               {"term": "WAV", "replacement": "disabled", "language": "*", "enabled": False}]
    assert client.post("/pronunciation/import", json={"entries": entries, "replace": True}).status_code == 200
    for _ in range(3):
        assert client.post("/pronunciation/test", json={"text": "GIF WAV", "language": "es"}).json()["substituted"] == "Spanish WAV"
        assert client.post("/pronunciation/test", json={"text": "GIF WAV", "language": "en"}).json()["substituted"] == "global WAV"
        backup = client.get("/pronunciation/export").json()
        assert client.post("/pronunciation/import", json={**backup, "replace": True}).status_code == 200
