"""Concurrent Settings edits must preserve independently updated voice fields."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading

import pytest


@pytest.fixture
def bindings(tmp_path, monkeypatch):
    from services import mcp_bindings

    path = tmp_path / "bindings.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE mcp_client_bindings (client_id TEXT PRIMARY KEY, "
                     "label TEXT, profile_id TEXT, default_engine TEXT, "
                     "last_seen_at REAL, created_at REAL)")

    @contextmanager
    def connect():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(mcp_bindings, "db_conn", connect)
    return mcp_bindings, connect


@pytest.mark.parametrize("existing", [True, False])
def test_concurrent_partial_upserts_preserve_both_edits(bindings, monkeypatch, existing):
    service, connect = bindings
    if existing:
        service.upsert_binding("agent", label="before", profile_id="before-voice")
    first_write, second_write = threading.Event(), threading.Event()
    actor = threading.local()

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=()):
            write = sql.startswith(("INSERT", "UPDATE", "BEGIN IMMEDIATE"))
            if actor.name == "second" and write:
                # Signal admission before SQLite can block on the first writer.
                second_write.set()
            if actor.name == "first" and sql.startswith(("INSERT", "UPDATE")):
                first_write.set()
                assert second_write.wait(5), "second writer never reached SQLite"
            return self.conn.execute(sql, params)

    @contextmanager
    def overlapping_connect():
        with connect() as conn:
            yield Connection(conn)

    monkeypatch.setattr(service, "db_conn", overlapping_connect)

    def update(name, **fields):
        actor.name = name
        return service.upsert_binding("agent", **fields)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, "first", label="new label")
        assert first_write.wait(5), "first writer never reached SQLite"
        second = pool.submit(update, "second", profile_id="new voice")
        assert first.result(timeout=10)["label"] == "new label"
        assert second.result(timeout=10)["profile_id"] == "new voice"
    monkeypatch.setattr(service, "db_conn", connect)
    row = service.get_binding("agent")
    assert row["label"] == "new label"
    assert row["profile_id"] == "new voice"


def test_omitted_fields_preserve_values_and_empty_fields_clear_them(bindings):
    service, _ = bindings
    original = service.upsert_binding(" agent ", label="named", profile_id="voice", default_engine="engine")
    updated = service.upsert_binding("agent", label="renamed")
    assert updated["profile_id"] == "voice"
    assert updated["default_engine"] == "engine"
    assert updated["created_at"] == original["created_at"]
    cleared = service.upsert_binding("agent", profile_id="", default_engine="")
    assert cleared["profile_id"] is None
    assert cleared["default_engine"] is None
    assert cleared["label"] == "renamed"


def test_failed_update_rolls_back_the_entire_binding(bindings):
    service, connect = bindings
    before = service.upsert_binding("agent", label="before", profile_id="voice")
    with connect() as conn:
        conn.execute("CREATE TRIGGER reject_edit BEFORE UPDATE ON mcp_client_bindings "
                     "BEGIN SELECT RAISE(ABORT, 'edit rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="edit rejected"):
        service.upsert_binding("agent", label="after", profile_id="after-voice")
    assert service.get_binding("agent") == before
