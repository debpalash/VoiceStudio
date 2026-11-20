"""`save_state` must write only the fields its caller provides.

Rewriting every column from a snapshot read in a separate connection loses a
concurrent writer's change. The cloud-sync engine writes ``needs_update`` and
``full_pull`` on its own paths while a Settings toggle writes ``choices`` and
``enabled``, so whichever commits second used to republish its stale copy of the
other's field -- silently, because the dropped field only means a collection is
never pulled again.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from core import db as core_db  # noqa: E402
from services.cloud_sync.store import SyncStore  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    return SyncStore()


def test_each_field_survives_a_separate_write(store):
    store.save_state("org-1", needs_update={"voices": 3})
    store.save_state("org-1", full_pull=["voices", "settings"])
    store.save_state("org-1", enabled=True)

    state = store.state("org-1")
    assert state is not None
    assert state.needs_update == {"voices": 3}
    assert sorted(state.full_pull) == ["settings", "voices"]
    assert state.enabled is True


def test_two_writers_that_read_before_either_writes_both_survive(store, monkeypatch):
    """The real interleave: each writer reads the row before either commits.

    ``save_state`` reads its snapshot, then writes. Gating the read makes two
    threads hold snapshots taken before either write, which is the window the old
    whole-row update lost a field in.
    """
    # Seed the row before gating: the setup write must not wait on a barrier only
    # the two racing writers will reach.
    store.save_state("org-2", enabled=True)

    barrier = threading.Barrier(2)
    real_state = store.state
    gate = {"reads": 0}
    lock = threading.Lock()

    def gated_state(org):  # noqa: ANN001
        snapshot = real_state(org)
        with lock:
            gate["reads"] += 1
            hold = gate["reads"] <= 2
        if hold:
            barrier.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(store, "state", gated_state)

    def write_needs_update() -> None:
        store.save_state("org-2", needs_update={"a": 2})

    def write_full_pull() -> None:
        store.save_state("org-2", full_pull=["a"])

    threads = [threading.Thread(target=write_needs_update), threading.Thread(target=write_full_pull)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    monkeypatch.setattr(store, "state", real_state)
    final = store.state("org-2")
    assert final is not None
    assert final.needs_update == {"a": 2}, "the needs_update write was lost"
    assert final.full_pull == ["a"], "the full_pull write was lost"
    assert final.enabled is True, "the untouched field changed"


def test_cursor_only_writes_leave_the_other_fields_alone(store):
    store.save_state("org-3", enabled=True, needs_update={"x": 1}, cursor=5)
    store.save_state("org-3", cursor=9)

    state = store.state("org-3")
    assert state is not None
    assert (state.cursor, state.enabled, state.needs_update) == (9, True, {"x": 1})


def test_an_unknown_field_is_rejected(store):
    with pytest.raises(ValueError) as excinfo:
        store.save_state("org-4", not_a_column=1)
    assert "not_a_column" in str(excinfo.value)


def test_a_fieldless_call_is_a_no_op(store):
    assert store.save_state("org-5") is not None
    assert store.state("org-5") == store.state("org-5")


def test_only_the_provided_columns_are_written(store, monkeypatch, tmp_path):
    """No caller string reaches the SQL text, and no extra column is touched."""
    seen: list[str] = []
    real_conn = core_db.db_conn
    path = str(tmp_path / "omnivoice.db")

    def traced():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.set_trace_callback(seen.append)
        return connection

    monkeypatch.setattr(core_db, "db_conn", traced)
    store.save_state("org-6", needs_update={"k": 1})
    monkeypatch.setattr(core_db, "db_conn", real_conn)

    statements = [s for s in seen if "INSERT INTO cloud_sync_state" in s]
    assert statements, f"no insert captured; saw {seen}"
    statement = statements[-1]
    assert "needs_update_json" in statement
    assert "full_pull_json" not in statement, (
        "a field the caller did not provide is still being written"
    )
    assert "choices_json" not in statement
