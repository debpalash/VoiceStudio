"""Pronunciation dictionary — migration 0008 + REST CRUD + apply-at-synth.

Three layers:
  * Migration 0008 upgrades a 0007-stamped DB, is idempotent, and converges to
    the same PRAGMA table_info as a fresh _BASE_SCHEMA install (dual-path).
  * The REST CRUD round-trips entries, validates IPA/CMU, and the /test dry-run
    substitutes with no model.
  * apply-at-synth: a saved entry actually transforms the text the generate path
    hands the model (proven by exercising the same module the route calls — no
    model load needed to assert the text transform).
"""
import os
import sqlite3
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root and root != "/" and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    assert os.path.isfile(os.path.join(root, "alembic.ini")), "alembic.ini not found"
    return root


def _run_alembic(direction, db_path, target="head"):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    (command.upgrade if direction == "upgrade" else command.downgrade)(cfg, target)


def _stamp(db_path, rev):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.stamp(cfg, rev)


def _tables(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _norm_default(d):
    """Strip alembic's cosmetic quoting so '1' and 1 compare equal."""
    if d is None:
        return None
    s = str(d).strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":
        s = s[1:-1]
    return s


# SQLite type affinities that are interchangeable (REAL == FLOAT, etc.); the
# anti-drift guard cares about column presence + semantic shape, not the exact
# DDL string alembic vs the hand-written _BASE_SCHEMA happen to emit.
_TYPE_AFFINITY = {"FLOAT": "REAL", "DOUBLE": "REAL", "INT": "INTEGER"}


def _shape(rows, pk_names):
    """(name, affinity-normalized type, notnull-or-PK, normalized default) per
    column — the semantic fingerprint two converged schemas must share."""
    out = []
    for _cid, name, ctype, notnull, dflt, pk in rows:
        t = _TYPE_AFFINITY.get((ctype or "").upper(), (ctype or "").upper())
        # A PRIMARY KEY column is NOT NULL in practice whether or not SQLite
        # flags notnull on it, so fold pk into the not-null bit.
        nn = 1 if (notnull or pk or name in pk_names) else 0
        out.append((name, t, nn, _norm_default(dflt)))
    return out


def _table_shape(db_path, table):
    with sqlite3.connect(str(db_path)) as conn:
        rows = list(conn.execute(f"PRAGMA table_info({table})"))
    pk = {r[1] for r in rows if r[5]}
    return _shape(rows, pk)


# ── migration 0008 ────────────────────────────────────────────────────────────


def test_migration_0008_creates_table(tmp_path):
    dbf = tmp_path / "pre.db"
    sqlite3.connect(str(dbf)).close()
    _stamp(str(dbf), "0007_rebuild_poisoned_design_instruct")
    _run_alembic("upgrade", str(dbf))
    assert "pronunciation_entries" in _tables(dbf)


def test_migration_0008_idempotent(tmp_path):
    dbf = tmp_path / "pre.db"
    sqlite3.connect(str(dbf)).close()
    _stamp(str(dbf), "0007_rebuild_poisoned_design_instruct")
    _run_alembic("upgrade", str(dbf))
    # Insert a row, re-run upgrade, row survives (no DROP/recreate).
    with sqlite3.connect(str(dbf)) as conn:
        conn.execute(
            "INSERT INTO pronunciation_entries (id, term, replacement, type, language, enabled, created_at) "
            "VALUES ('a', 'GIF', 'jiff', 'respelling', '*', 1, 1.0)"
        )
        conn.commit()
    _run_alembic("upgrade", str(dbf))  # no-op (guarded by sqlite_master)
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT replacement FROM pronunciation_entries WHERE id='a'").fetchone()[0] == "jiff"


def test_migration_0008_downgrade_drops_table(tmp_path):
    dbf = tmp_path / "pre.db"
    sqlite3.connect(str(dbf)).close()
    _stamp(str(dbf), "0007_rebuild_poisoned_design_instruct")
    _run_alembic("upgrade", str(dbf))
    _run_alembic("downgrade", str(dbf), target="0007_rebuild_poisoned_design_instruct")
    assert "pronunciation_entries" not in _tables(dbf)


def test_migration_and_base_schema_converge(tmp_path, monkeypatch):
    """A migrated DB and a fresh _BASE_SCHEMA install have identical table shape
    (the dual-path discipline — fresh installs and upgrades can't drift)."""
    # migrated path: a 0007-era DB (pre-0008) upgraded to head.
    mig = tmp_path / "mig.db"
    sqlite3.connect(str(mig)).close()
    _stamp(str(mig), "0007_rebuild_poisoned_design_instruct")
    _run_alembic("upgrade", str(mig))
    mig_info = _table_shape(mig, "pronunciation_entries")

    # fresh-install path via _BASE_SCHEMA
    sys.path.insert(0, os.path.join(_repo_root(), "backend"))
    from core.db import _BASE_SCHEMA
    fresh = tmp_path / "fresh.db"
    with sqlite3.connect(str(fresh)) as conn:
        conn.executescript(_BASE_SCHEMA)
    base_info = _table_shape(fresh, "pronunciation_entries")

    assert mig_info == base_info, f"schema drift: migration={mig_info} base={base_info}"


def test_existing_data_dir_upgrades_cleanly(tmp_path, monkeypatch):
    """A pre-0008 DB with real rows in other tables upgrades without data loss.

    The DB is created the way fresh installs are (``_BASE_SCHEMA`` makes the
    tables) and stamped at 0007 to simulate an existing v0.3.x user DB that has
    not yet seen 0008. Upgrading to head adds the new table; old rows survive.
    """
    dbf = tmp_path / "userdata.db"
    sys.path.insert(0, os.path.join(_repo_root(), "backend"))
    from core.db import _BASE_SCHEMA
    with sqlite3.connect(str(dbf)) as conn:
        conn.executescript(_BASE_SCHEMA)
        # Simulate a pre-0008 DB: drop the new table so 0008 has work to do.
        conn.execute("DROP TABLE IF EXISTS pronunciation_entries")
        conn.execute("INSERT INTO voice_profiles (id, name, created_at) VALUES ('p1', 'Morgan', 1.0)")
        conn.commit()
    _stamp(str(dbf), "0007_rebuild_poisoned_design_instruct")
    _run_alembic("upgrade", str(dbf))  # to head (0008)
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT name FROM voice_profiles WHERE id='p1'").fetchone()[0] == "Morgan"
        assert "pronunciation_entries" in {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


# ── REST CRUD + dry-run + apply-at-synth (main-importing — CI) ────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    import importlib
    for m in ("core.config", "core.db"):
        if m in sys.modules:
            importlib.reload(importlib.import_module(m))
    import core.db as _db
    _db.init_db()
    import main as _main
    importlib.reload(_main)
    from fastapi.testclient import TestClient
    try:
        yield TestClient(_main.app, client=("127.0.0.1", 50000))
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("core.config"))
        _restored_db = importlib.reload(importlib.import_module("core.db"))
        importlib.reload(_main)
        # Re-create the schema on the RESTORED data dir. The reload rebinds
        # DB_PATH back but never re-runs init_db(), so without this the module
        # is left pointing at a schema-less DB — which corrupts any later test
        # that reuses the reloaded core.db / main.app (the #932 router-smoke
        # leak; this closes the class at its source). init_db() is idempotent.
        _restored_db.init_db()


def test_crud_roundtrip(client):
    assert client.get("/pronunciation").json() == []
    r = client.post("/pronunciation", json={"term": "GIF", "replacement": "jiff"})
    assert r.status_code == 200
    eid = r.json()["id"]
    assert r.json()["scope"] == "*" and r.json()["enabled"] is True

    listed = client.get("/pronunciation").json()
    assert len(listed) == 1 and listed[0]["term"] == "GIF"

    r2 = client.put(f"/pronunciation/{eid}", json={"replacement": "JIFF", "enabled": False})
    assert r2.status_code == 200 and r2.json()["replacement"] == "JIFF" and r2.json()["enabled"] is False

    assert client.delete(f"/pronunciation/{eid}").json()["deleted"] is True
    assert client.delete(f"/pronunciation/{eid}").json()["deleted"] is False
    assert client.get("/pronunciation").json() == []


def test_server_mode_pronunciation_mutations_require_api_key(client, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    remote = TestClient(client.app, client=("172.17.0.1", 50000))

    assert remote.get("/pronunciation").status_code == 200
    assert remote.post(
        "/pronunciation", json={"term": "GIF", "replacement": "jiff"}
    ).status_code == 403
    assert remote.post(
        "/pronunciation/import", json={"entries": [], "replace": True}
    ).status_code == 403


def test_create_rejects_blank_term(client):
    assert client.post("/pronunciation", json={"term": "   "}).status_code == 400


def test_create_normalizes_language_to_prefix(client):
    r = client.post("/pronunciation", json={"term": "x", "replacement": "y", "language": "en-US"})
    assert r.json()["language"] == "en"


def test_language_scope_saved_as_language_id(client):
    """#2542: names resolve through the bundled map and 3-letter ids survive."""
    def saved(language):
        return client.post(
            "/pronunciation", json={"term": "x", "replacement": "y", "language": language}
        ).json()["language"]

    assert saved("Spanish") == "es"
    assert saved("kbt") == "kbt"
    assert saved("es_MX") == "es"
    assert saved("Auto") == "*"
    eid = client.post("/pronunciation", json={"term": "z", "replacement": "w"}).json()["id"]
    assert client.put(f"/pronunciation/{eid}", json={"language": "Estonian"}).json()["language"] == "et"
    imported = client.post("/pronunciation/import", json={"replace": True, "entries": [
        {"term": "GIF", "replacement": "jiff", "language": "Spanish"},
    ]})
    assert imported.status_code == 200
    assert client.get("/pronunciation/export").json()["entries"][0]["language"] == "es"


def test_dry_run_matches_picker_names_not_shared_prefixes(client):
    client.post("/pronunciation", json={"term": "GIF", "replacement": "jiff", "language": "es"})

    def spoken(language):
        return client.post(
            "/pronunciation/test", json={"text": "GIF", "language": language}
        ).json()["substituted"]

    assert spoken("es") == "jiff"
    assert spoken("Spanish") == "jiff"
    assert spoken("Estonian") == "GIF"


def test_ipa_validation_rejects_bracket_garbage(client):
    r = client.post("/pronunciation", json={"term": "x", "replacement": "[bad]", "type": "ipa"})
    assert r.status_code == 400


def test_ipa_validation_accepts_real_ipa(client):
    r = client.post("/pronunciation", json={"term": "nevada", "replacement": "nɛˈvædə", "type": "ipa"})
    assert r.status_code == 200


def test_cmu_validation(client):
    assert client.post("/pronunciation", json={"term": "x", "replacement": "N AH0 V", "type": "cmu"}).status_code == 200
    assert client.post("/pronunciation", json={"term": "x", "replacement": "not cmu!!", "type": "cmu"}).status_code == 400


def test_test_endpoint_substitutes_without_model(client):
    client.post("/pronunciation", json={"term": "GIF", "replacement": "jiff", "language": "*"})
    r = client.post("/pronunciation/test", json={"text": "a GIF and [[a|bee]]", "language": "en"})
    body = r.json()
    assert body["substituted"] == "a jiff and bee"
    assert body["changed"] is True


def test_import_export_roundtrip(client):
    payload = {"entries": [
        {"term": "GIF", "replacement": "jiff", "type": "respelling", "language": "*", "enabled": True},
        {"term": "Nevada", "replacement": "Nuh-VAD-uh", "type": "respelling", "language": "en", "enabled": True},
    ]}
    assert client.post("/pronunciation/import", json=payload).json()["imported"] == 2
    exported = client.get("/pronunciation/export").json()["entries"]
    assert {e["term"] for e in exported} == {"GIF", "Nevada"}
    # replace=true clears first
    assert client.post("/pronunciation/import", json={"entries": [], "replace": True}).json()["replaced"] is True
    assert client.get("/pronunciation/export").json()["entries"] == []


def test_duplicate_precedence_survives_backup_restore(client, monkeypatch):
    """#2552: one import stamps every row with the same created_at, so the
    random id must not decide which duplicate wins. Ids are forced to sort in
    reverse of insertion, which is one ordering a random draw can produce."""
    import itertools
    import types

    import api.routers.pronunciation as router

    countdown = itertools.count(999_999, -1)
    monkeypatch.setattr(router, "uuid", types.SimpleNamespace(
        uuid4=lambda: f"{next(countdown):012d}"))
    entries = [
        {"term": "GIF", "replacement": "first"},
        {"term": "gif", "replacement": "last"},
        {"term": "SQL", "replacement": "sequel", "language": "en"},
        {"term": "SQL", "replacement": "ess-cue-ell", "language": "en"},
    ]
    client.post("/pronunciation/import", json={"entries": entries, "replace": True})

    def spoken():
        return client.post(
            "/pronunciation/test", json={"text": "GIF SQL", "language": "en"}
        ).json()["substituted"]

    assert spoken() == "last ess-cue-ell"
    listed = [e["replacement"] for e in client.get("/pronunciation").json()]
    assert listed == ["first", "last", "sequel", "ess-cue-ell"]
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    assert apply_pronunciation("GIF SQL", load_entries_from_db(), "en") == spoken()

    for _ in range(2):
        backup = client.get("/pronunciation/export").json()["entries"]
        assert [e["replacement"] for e in backup] == listed
        client.post("/pronunciation/import", json={"entries": backup, "replace": True})
        assert spoken() == "last ess-cue-ell"


def test_saved_entry_transforms_generate_text(client):
    """The load-bearing assertion: a saved dictionary entry changes the exact
    text the generate path feeds the model. We call the same transform the route
    runs (services.pronunciation over the live DB) — no model load needed."""
    client.post("/pronunciation", json={"term": "GIF", "replacement": "jiff", "language": "*"})
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    rows = load_entries_from_db()
    assert apply_pronunciation("show me a GIF", rows, "en") == "show me a jiff"
