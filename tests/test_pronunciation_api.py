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


def test_saved_entry_transforms_generate_text(client):
    """The load-bearing assertion: a saved dictionary entry changes the exact
    text the generate path feeds the model. We call the same transform the route
    runs (services.pronunciation over the live DB) — no model load needed."""
    client.post("/pronunciation", json={"term": "GIF", "replacement": "jiff", "language": "*"})
    from services.pronunciation import apply_pronunciation, load_entries_from_db
    rows = load_entries_from_db()
    assert apply_pronunciation("show me a GIF", rows, "en") == "show me a jiff"


# ── Ordering: a backup/restore must not reshuffle precedence (#2552) ─────────
#
# ``import`` stamps every row of a batch with ONE ``time.time()``, so equal
# ``created_at`` is the normal case. Every reader collapses rows to
# ``{term: replacement}`` where the last row wins, so the tiebreak decides what
# a duplicate or case-variant term actually pronounces as. Two case variants of
# one term ("GIF"/"gif") collide in the IGNORECASE matcher, which makes the
# winner observable through the model-free ``/pronunciation/test`` dry run.


def _case_variant_pairs(count):
    """``count`` respelling pairs that differ ONLY by term case, so exactly one
    of each pair is the live pronunciation. Deliberately uses real terms the
    matcher treats as whole-word, IGNORECASE equal."""
    return {
        "entries": [
            entry
            for i in range(count)
            for entry in (
                {"term": f"Gif{i}", "replacement": f"first{i}", "language": "*", "enabled": True},
                {"term": f"gif{i}", "replacement": f"last{i}", "language": "*", "enabled": True},
            )
        ]
    }


def _dry_run_precedence(client, count):
    """The winning replacement for each pair, as synthesis resolves it."""
    out = {}
    for i in range(count):
        body = client.post("/pronunciation/test", json={"text": f"GIF{i}", "language": "en"}).json()
        out[i] = body["substituted"]
    return out


def test_import_keeps_the_last_typed_case_variant(client):
    """Importing two case variants leaves the LAST one live. With the random-``id``
    tiebreak this flipped per import (#2552); insertion order makes it stable."""
    client.post("/pronunciation/import", json=_case_variant_pairs(4))
    assert _dry_run_precedence(client, 4) == {i: f"last{i}" for i in range(4)}


def test_backup_restore_is_a_fixed_point(client):
    """Export → import(replace) must reproduce the same effective pronunciations.
    The old code exported tied rows in random-UUID order, so each round trip
    re-inserted them in yet another order and the winner moved."""
    client.post("/pronunciation/import", json=_case_variant_pairs(6))
    before = _dry_run_precedence(client, 6)
    assert before == {i: f"last{i}" for i in range(6)}

    backup = client.get("/pronunciation/export").json()["entries"]
    for _ in range(3):
        assert client.post("/pronunciation/import", json={"entries": backup, "replace": True}).json()["imported"] == len(backup)
        assert _dry_run_precedence(client, 6) == before


def test_export_writes_the_load_order(client):
    """The backup is a list, so its ORDER is the order a restore will insert —
    it has to be the order the live dictionary resolves in, or the restore is
    the shuffle. The last typed case variant is therefore the last one written."""
    client.post("/pronunciation/import", json=_case_variant_pairs(3))
    terms = [e["term"] for e in client.get("/pronunciation/export").json()["entries"]]
    assert terms == [t for i in range(3) for t in (f"Gif{i}", f"gif{i}")]


def test_list_test_and_synthesis_agree_on_order(client):
    """The three live readers must return one order, not three. ``/pronunciation/test``
    had no ORDER BY at all, so it could resolve a duplicate differently from the
    list the Settings panel renders and from the synthesis loader."""
    client.post("/pronunciation/import", json=_case_variant_pairs(8))
    listed = client.get("/pronunciation").json()
    assert [e["term"] for e in listed] == [t for i in range(8) for t in (f"Gif{i}", f"gif{i}")]

    from services.pronunciation import apply_pronunciation, load_entries_from_db
    rows = load_entries_from_db()
    assert [r["term"] for r in rows] == [e["term"] for e in listed]
    # And the synthesis transform resolves the same winner the dry run reports.
    assert apply_pronunciation("GIF0", rows, "en") == "last0"


def test_repeated_import_does_not_accumulate_duplicates(client):
    """A non-replacing import appends, so restore-with-merge keeps the incoming
    order at the end and the newest typed variant stays live."""
    client.post("/pronunciation/import", json={"entries": [
        {"term": "GIF", "replacement": "original", "language": "*", "enabled": True},
    ]})
    client.post("/pronunciation/import", json={"entries": [
        {"term": "gif", "replacement": "override", "language": "*", "enabled": True},
    ]})
    body = client.post("/pronunciation/test", json={"text": "a GIF", "language": "en"}).json()
    assert body["substituted"] == "a override"


def test_scoped_disabled_and_duplicate_rows_survive_a_round_trip(client):
    """The ordering fix must not disturb what each row MEANS: a scoped row stays
    scoped, a disabled row stays disabled, and an exact duplicate keeps its
    winner across a restore."""
    client.post("/pronunciation/import", json={"entries": [
        {"term": "Nevada", "replacement": "Nuh-VAD-uh", "language": "en", "enabled": True},
        {"term": "Nevada", "replacement": "wrong", "language": "*", "enabled": True},
        {"term": "RHS", "replacement": "parked", "language": "*", "enabled": False},
        {"term": "RHS", "replacement": "live", "language": "*", "enabled": True},
    ]})
    before = client.post("/pronunciation/test", json={"text": "Nevada and RHS", "language": "en"}).json()
    assert before["substituted"] == "Nuh-VAD-uh and live"  # scoped wins, disabled skipped

    backup = client.get("/pronunciation/export").json()["entries"]
    client.post("/pronunciation/import", json={"entries": backup, "replace": True})
    after = client.post("/pronunciation/test", json={"text": "Nevada and RHS", "language": "en"}).json()
    assert after["substituted"] == before["substituted"]

    restored = client.get("/pronunciation/export").json()["entries"]
    assert [(e["term"], e["language"], e["enabled"]) for e in restored] == [
        (e["term"], e["language"], e["enabled"]) for e in backup
    ]
