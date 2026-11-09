"""What syncs and where it lives locally (sync-collections.md, S1)."""
import sqlite3

import pytest

from core import db as core_db
from core import prefs
from services import settings_store
from services.cloud_sync import allowlist
from services.cloud_sync.store import SyncStore


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    return SyncStore()


def test_every_item_reads_and_writes_back_what_it_read(local):
    local.set_local("voicestudio.theme.v2", "dark", origin="renderer")
    local.set_local("voicestudio.locale", "en", origin="renderer")
    prefs.set_("generation_history_cap", 40)
    prefs.set_("dictation.mode", "push")
    settings_store.set_text("llm.active_provider", "openai")
    settings_store.set_text("llm.model.openai", "gpt-x")
    for item in allowlist.ITEMS:
        values = allowlist.read_values(item.collection, item.item_id)
        allowlist.write_values(item.collection, item.item_id, values)
        assert allowlist.read_values(item.collection, item.item_id) == values, item


def test_every_local_source_maps_to_exactly_one_item():
    seen = {}
    for item in allowlist.ITEMS:
        for source in item.sources:
            assert source not in seen, (source, seen.get(source))
            seen[source] = item
    for key in allowlist.RENDERER_KEYS:
        assert allowlist.item_for("local", key) is not None


@pytest.mark.parametrize(
    "source,key",
    [
        ("prefs", "compute_device"),
        ("prefs", "installation_id"),
        ("prefs", "dictation.model_id"),
        ("prefs", "hf_endpoint_mode"),
        ("settings", "secret.llm_key.openai"),
        ("settings", "hf_token"),
        ("local", "voicestudio.workspace-layout"),
        ("local", "voicestudio.take-settings.v1"),
        ("local", "voicestudio.settings.last-route"),
    ],
)
def test_device_local_and_secret_keys_never_sync(source, key):
    assert allowlist.item_for(source, key) is None


def test_generation_defaults_never_carry_the_draft_and_keep_it_on_write(local):
    local.set_local(
        "voicestudio.clone.settings.v1",
        {"steps": 16, "speed": 1.0, "text": "draft", "refText": "r", "duration": 3, "selectedProfileId": "ab12cd34"},
        origin="renderer",
    )
    values = allowlist.read_values("settings", "generation-defaults")
    assert values == {"steps": 16, "speed": 1.0}
    allowlist.write_values("settings", "generation-defaults", {"steps": 24, "speed": 1.0})
    assert local.get_local("voicestudio.clone.settings.v1") == {
        "steps": 24, "speed": 1.0, "text": "draft", "refText": "r", "duration": 3, "selectedProfileId": "ab12cd34",
    }


def test_llm_settings_read_no_secret(local):
    settings_store.set_text("llm.active_provider", "openai")
    settings_store.set_text("llm.base_url.openai", "https://api.example")
    settings_store.set_secret("llm_key.openai", "sk-secret")
    values = allowlist.read_values("settings", "llm")
    assert "sk-secret" not in repr(values)
    assert values["provider"] == "openai" and values["baseUrl"] == "https://api.example"


def test_favorites_become_a_keyed_map_with_first_seen_times(local):
    local.set_local("voicestudio.gallery.favorites.v1", ["arch-a", "arch-b"], origin="renderer")
    values = allowlist.read_values("favorites", "gallery")
    assert set(values) == {"arch-a", "arch-b"}
    assert all(v["added_at"].endswith("Z") for v in values.values())
    again = allowlist.read_values("favorites", "gallery")
    assert again == values  # times are stable
    allowlist.write_values("favorites", "gallery", {"arch-b": values["arch-b"], "arch-c": {"added_at": "2026-10-16T09:00:00Z"}})
    assert sorted(local.get_local("voicestudio.gallery.favorites.v1")) == ["arch-b", "arch-c"]


def test_a_missing_value_removes_the_local_source(local):
    prefs.set_("generation_history_cap", 40)
    allowlist.write_values("settings", "history", {})
    assert prefs.get("generation_history_cap") is None
