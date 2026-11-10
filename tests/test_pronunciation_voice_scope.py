"""Per-voice pronunciation dictionaries (#3873): precedence, isolation between
voices, API scope, deleting a voice, and the 0018 migration."""
import os
import sqlite3

import pytest

from services.pronunciation import (
    apply_pronunciation,
    entries_for_language,
    inert_entries_for_language,
)


def _row(term, repl, language="*", profile_id="", **kw):
    return {"term": term, "replacement": repl, "type": kw.get("type", "respelling"),
            "language": language, "enabled": 1, "profile_id": profile_id}


ROWS = [
    _row("dance", "dahns"),                                    # global
    _row("dance", "dans-en", language="en"),                   # language
    _row("dance", "darnce", profile_id="A"),                   # voice A
    _row("dance", "dahnce-en", language="en", profile_id="A"),  # voice A + en
    _row("path", "parth", profile_id="A"),
    _row("path", "pahth", profile_id="B"),
]


def test_precedence_global_language_voice_voice_language():
    assert entries_for_language(ROWS, None)["dance"] == "dahns"
    assert entries_for_language(ROWS, "en")["dance"] == "dans-en"
    assert entries_for_language(ROWS, None, "A")["dance"] == "darnce"
    assert entries_for_language(ROWS, "en", "A")["dance"] == "dahnce-en"
    # A voice row for another language does not apply, the voice row still does.
    assert entries_for_language(ROWS, "de", "A")["dance"] == "darnce"


def test_voices_are_isolated_and_no_voice_sees_none():
    a = entries_for_language(ROWS, "en", "A")
    b = entries_for_language(ROWS, "en", "B")
    assert a["path"] == "parth" and b["path"] == "pahth"
    assert b["dance"] == "dans-en"  # B has no dance entry: language wins
    assert "path" not in entries_for_language(ROWS, "en")  # no voice -> no voice rows
    assert "path" not in entries_for_language(ROWS, "en", "C")


def test_voice_case_variant_beats_language_entry():
    rows = [_row("GIF", "jiff"), _row("gif", "gihf", profile_id="A")]
    assert apply_pronunciation("a GIF", rows, None, profile_id="A") == "a gihf"
    assert apply_pronunciation("a GIF", rows, None) == "a jiff"


def test_without_profile_scope_output_is_unchanged():
    legacy = [{k: v for k, v in r.items() if k != "profile_id"} for r in ROWS[:2]]
    assert entries_for_language(legacy, "en") == {"dance": "dans-en"}
    assert apply_pronunciation("dance now", legacy, "en") == "dans-en now"


def test_inert_entries_respect_voice():
    rows = [_row("x", "", profile_id="A", type="ipa"), _row("y", "", type="ipa")]
    assert [e["term"] for e in inert_entries_for_language(rows, None, "A")] == ["x", "y"]
    assert [e["term"] for e in inert_entries_for_language(rows, None)] == ["y"]


# ── DB + API ────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db as core_db

    monkeypatch.setattr(core_db, "DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path))
    core_db.init_db()
    with core_db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name) VALUES('A','A'),('B','B')")
    return core_db


def test_api_scope_roundtrip_and_delete_voice(db):
    from fastapi import HTTPException

    from api.routers import pronunciation as api
    from api.routers import profiles

    api.create_entry(api.PronEntry(term="dance", replacement="dahns"))
    api.create_entry(api.PronEntry(term="path", replacement="parth", profile_id="A"))
    api.create_entry(api.PronEntry(term="path", replacement="pahth", profile_id="B"))
    with pytest.raises(HTTPException) as exc:
        api.create_entry(api.PronEntry(term="x", replacement="y", profile_id="nope"))
    assert exc.value.status_code == 404

    assert [e["term"] for e in api.list_entries(None)] == ["dance"]  # legacy view
    assert [e["replacement"] for e in api.list_entries("A")] == ["parth"]
    assert api.export_entries("B")["entries"][0]["replacement"] == "pahth"

    out = api.test_substitution(api.PronTestRequest(text="path", profile_id="A"))
    assert out["substituted"] == "parth"
    assert api.test_substitution(api.PronTestRequest(text="path"))["substituted"] == "path"

    # replace=True clears only the target scope
    api.import_entries(api.PronImportRequest(
        entries=[api.PronEntry(term="path", replacement="PAR")], replace=True, profile_id="A"))
    assert [e["replacement"] for e in api.list_entries("A")] == ["PAR"]
    assert [e["replacement"] for e in api.list_entries("B")] == ["pahth"]
    assert [e["term"] for e in api.list_entries(None)] == ["dance"]
    api.import_entries(api.PronImportRequest(
        entries=[api.PronEntry(term="cat", replacement="kat")], replace=True))
    assert [e["replacement"] for e in api.list_entries("B")] == ["pahth"]  # untouched

    profiles.delete_profile("A")
    assert api.list_entries("A") == []
    assert [e["replacement"] for e in api.list_entries("B")] == ["pahth"]
    assert [e["term"] for e in api.list_entries(None)] == ["cat"]


# ── migration 0018 ──────────────────────────────────────────────────────────


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root != os.path.dirname(root) and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    return root


def _upgrade(db_path, target="head"):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def test_migration_adds_profile_id_and_keeps_existing_entries(tmp_path):
    dbf = str(tmp_path / "old.db")
    with sqlite3.connect(dbf) as conn:
        conn.executescript("""
            CREATE TABLE voice_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                ref_audio_path TEXT, ref_text TEXT DEFAULT '', instruct TEXT DEFAULT '',
                language TEXT DEFAULT 'Auto', locked_audio_path TEXT DEFAULT '', seed INTEGER,
                is_locked INTEGER DEFAULT 0, personality TEXT DEFAULT '', description TEXT DEFAULT '',
                is_demo INTEGER DEFAULT 0, created_at REAL);
        """)
    _upgrade(dbf, "0017_job_events_unique_seq")
    with sqlite3.connect(dbf) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pronunciation_entries)")}
        assert "profile_id" not in cols
        conn.execute(
            "INSERT INTO pronunciation_entries (id, term, replacement, type, language, enabled, created_at) "
            "VALUES ('a','GIF','jiff','respelling','*',1,1.0)")
    _upgrade(dbf)
    _upgrade(dbf)  # idempotent
    with sqlite3.connect(dbf) as conn:
        assert conn.execute(
            "SELECT term, replacement, profile_id FROM pronunciation_entries").fetchall() == [
            ("GIF", "jiff", "")]


def test_reconcile_adds_column_where_alembic_cannot_run(tmp_path):
    from core import db as core_db

    conn = sqlite3.connect(str(tmp_path / "r.db"))
    conn.executescript("""
        CREATE TABLE pronunciation_entries (id TEXT PRIMARY KEY, term TEXT NOT NULL,
            replacement TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'respelling',
            language TEXT NOT NULL DEFAULT '*', enabled INTEGER NOT NULL DEFAULT 1, created_at REAL);
        INSERT INTO pronunciation_entries (id, term, replacement) VALUES ('a','x','y');
    """)
    core_db._reconcile_additive_columns(conn)
    assert conn.execute("SELECT profile_id FROM pronunciation_entries").fetchone() == ("",)


def test_voice_overlay_keeps_distinct_unicode_spellings():
    rows = [_row("STRASSE", "shtrah-seh"), _row("Straße", "shtrah-seh-ess", profile_id="A")]
    assert set(entries_for_language(rows, None, "A")) == {"STRASSE", "Straße"}


def test_voice_case_variant_still_replaces_language_entry():
    rows = [_row("GIF", "jif"), _row("gif", "gif-v", profile_id="A")]
    assert entries_for_language(rows, None, "A") == {"gif": "gif-v"}
