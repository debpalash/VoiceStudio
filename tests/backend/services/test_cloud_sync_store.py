"""Local sync tables (desktop-app-guide §4) and their store."""
import os
import sqlite3

import pytest

from core import db as core_db
from services.cloud_sync.store import SyncStore

TABLES = ("cloud_sync_state", "cloud_sync_items", "cloud_sync_outbox", "cloud_sync_local")


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    return root


def _upgrade(db_path: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _old_database(path: str) -> None:
    """A database as 0013 left it: today's base schema without the sync tables."""
    from alembic import command
    from alembic.config import Config

    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
        for table in TABLES:
            conn.execute(f"DROP TABLE {table}")
    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.stamp(cfg, "0013_generation_history_design_recipe")


def _columns(db_path: str, table: str) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return [tuple(r[1:4]) for r in conn.execute(f"PRAGMA table_info({table})")]


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    return SyncStore()


def test_migration_adds_the_tables_to_an_old_database_and_keeps_its_data(tmp_path):
    path = str(tmp_path / "old.db")
    _old_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('keep', 'me', 1)")
    _upgrade(path, "head")
    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute("SELECT value FROM settings WHERE key='keep'").fetchone()[0] == "me"
    assert set(TABLES) <= names


def test_fresh_install_schema_matches_the_migration(tmp_path):
    migrated = str(tmp_path / "migrated.db")
    _old_database(migrated)
    _upgrade(migrated, "head")
    fresh = str(tmp_path / "fresh.db")
    with sqlite3.connect(fresh) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    for table in TABLES:
        assert _columns(fresh, table) == _columns(migrated, table), table


def test_outbox_keeps_one_row_per_item_and_a_new_change_gets_a_new_key(store):
    first = store.queue("Org1", "settings", "appearance", "put")
    again = store.queue("Org1", "settings", "appearance", "delete")
    rows = store.outbox("Org1")
    assert len(rows) == 1
    # A new local change is a new logical write: same row, new key and generation.
    assert again.idempotency_key != first.idempotency_key
    assert again.gen == first.gen + 1
    assert rows[0].op == "delete"
    assert 16 <= len(first.idempotency_key) <= 128


def test_dropping_a_row_that_changed_meanwhile_keeps_it(store):
    row = store.queue("Org1", "settings", "appearance", "put")
    store.queue("Org1", "settings", "appearance", "put")  # edited while the push was in flight
    assert store.drop_outbox(row.id, gen=row.gen) is False
    assert len(store.outbox("Org1")) == 1


def test_a_renewed_key_differs_and_drop_removes_the_row(store):
    row = store.queue("Org1", "settings", "review", "put")
    renewed = store.renew_key(row.id)
    assert renewed != row.idempotency_key
    store.drop_outbox(row.id)  # no generation: unconditional
    assert store.outbox("Org1") == []


def test_page_items_and_cursor_are_saved_together(store):
    store.save_state("Org1", device_id="Dv1", enabled=True, choices={"settings": True})
    item = {"collection": "settings", "item_id": "appearance", "revision": 57, "deleted": False,
            "schema_version": 1, "content": {"values": {"theme": "dark"}}}
    store.save_page("Org1", [item], cursor=60)
    assert store.state("Org1").cursor == 60
    base = store.item("Org1", "settings", "appearance")
    assert (base.revision, base.content) == (57, {"values": {"theme": "dark"}})


def test_renderer_mirror_reports_only_values_applied_from_sync(store):
    store.set_local("voicestudio.locale", "en", origin="renderer")
    seq = store.local_seq()
    store.set_local("voicestudio.locale", "de", origin="sync")
    assert store.get_local("voicestudio.locale") == "de"
    assert store.local_since(seq) == [(seq + 1, "voicestudio.locale", "de")]
    assert store.local_since(seq + 1) == []


def test_state_survives_a_new_store_instance(store):
    store.save_state("Org1", device_id="Dv1", enabled=True, choices={"settings": True})
    again = SyncStore().state("Org1")
    assert (again.device_id, again.enabled, again.choices) == ("Dv1", True, {"settings": True})
