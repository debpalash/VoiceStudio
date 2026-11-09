"""Local writes announce themselves to the sync engine, except writes applied from sync."""
import sqlite3

import pytest

from core import change_hooks, prefs
from core import db as core_db
from services import settings_store


@pytest.fixture
def seen(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    events = []
    unsubscribe = change_hooks.subscribe(lambda source, key: events.append((source, key)))
    yield events
    unsubscribe()


def test_each_prefs_write_announces_its_key_once(seen):
    prefs.set_("generation_history_cap", 50)
    prefs.update_mapping("performance_profile", {"a": 1})
    prefs.delete("generation_history_cap")
    assert seen == [
        ("prefs", "generation_history_cap"),
        ("prefs", "performance_profile"),
        ("prefs", "generation_history_cap"),
    ]


def test_settings_table_text_writes_announce_their_key(seen):
    settings_store.set_text("llm.active_provider", "openai")
    settings_store.clear_text("llm.active_provider")
    assert seen == [("settings", "llm.active_provider"), ("settings", "llm.active_provider")]


def test_writes_applied_from_sync_are_silent(seen):
    with change_hooks.from_sync():
        prefs.set_("generation_history_cap", 10)
        settings_store.set_text("llm.active_provider", "openai")
        change_hooks.notify("local", "voicestudio.locale")
    assert seen == []
    prefs.set_("generation_history_cap", 11)
    assert seen == [("prefs", "generation_history_cap")]


def test_a_failing_listener_never_breaks_the_write(seen):
    unsubscribe = change_hooks.subscribe(lambda *_: 1 / 0)
    try:
        prefs.set_("generation_history_cap", 12)
    finally:
        unsubscribe()
    assert prefs.get("generation_history_cap") == 12
    assert seen == [("prefs", "generation_history_cap")]
