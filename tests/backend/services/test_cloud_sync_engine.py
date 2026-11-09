"""The sync loop (desktop-app-guide §6-7, tests §11 items 5-7) against a fake server."""
import asyncio
import sqlite3

import pytest

from core import db as core_db
from core import prefs
from services.cloud_sync.client import CloudOff, SyncApiError
from services.cloud_sync.engine import SyncEngine
from services.cloud_sync.store import SyncStore

from .cloud_sync_fake import FakeSyncServer

ORG = "Or5tG7hJ9kL1zX3c"
CHOICES = {"settings": True, "shortcuts": True, "favorites": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    store = SyncStore()
    store.save_state(ORG, device_id="DvLocal000000001", enabled=True, choices=CHOICES,
                     last_full_sync_at="2026-10-16T00:00:00Z")
    server = FakeSyncServer()

    async def no_sleep(_seconds):
        return None

    engine = SyncEngine(store, server, ORG, sleep=no_sleep)
    unhook = engine.attach_hooks()
    yield store, server, engine
    unhook()


def run(coro):
    return asyncio.run(coro)


def theme() -> object:
    return SyncStore().get_local("voicestudio.theme.v2")


def test_pull_applies_remote_values_saves_the_base_and_does_not_echo(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "light", "locale": "de"})
    run(engine.run_once())
    assert theme() == "light"
    assert store.get_local("voicestudio.locale") == "de"
    assert store.item(ORG, "settings", "appearance").revision == 1
    assert store.state(ORG).cursor == server.head
    assert store.outbox(ORG) == []
    assert not [c for c in server.calls if c[0] == "put"]


def test_a_local_change_is_pushed_with_if_match_and_then_the_new_revision(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    run(engine.run_once())
    assert server.values("settings", "history") == {"generationHistoryCap": 40}
    first = server.items[("settings", "history")]["revision"]
    prefs.set_("generation_history_cap", 50)
    run(engine.run_once())
    puts = [c for c in server.calls if c[0] == "put"]
    assert [c[3] for c in puts] == [0, first]
    assert store.outbox(ORG) == []


def test_own_changes_coming_back_are_skipped(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    run(engine.run_once())
    run(engine.run_once())
    assert prefs.get("generation_history_cap") == 40
    assert store.outbox(ORG) == []


def test_conflict_on_push_merges_both_sides_and_retries_with_a_new_key(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "dark", "locale": "en"})
    run(engine.run_once())
    server.put_remote("settings", "appearance", {"theme": "dark", "locale": "de"})  # another device
    store.set_local("voicestudio.theme.v2", "light", origin="renderer")
    engine.local_changed("settings", "appearance")
    original_key = store.outbox(ORG)[0].idempotency_key
    # Push before pulling: our If-Match is stale.
    run(engine.push())
    assert server.values("settings", "appearance") == {"theme": "light", "locale": "de"}
    assert store.get_local("voicestudio.locale") == "de"
    assert server.keys_seen[-1] != original_key
    assert engine.status.conflicts == []


def test_both_sides_changing_one_key_keeps_local_and_notes_a_conflict(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "dark"})
    run(engine.run_once())
    server.put_remote("settings", "appearance", {"theme": "system"})
    store.set_local("voicestudio.theme.v2", "light", origin="renderer")
    engine.local_changed("settings", "appearance")
    run(engine.run_once())
    assert theme() == "light"
    assert server.values("settings", "appearance") == {"theme": "light"}
    assert engine.status.conflicts[-1]["keys"] == ["theme"]


def test_pull_pages_until_has_more_is_false(env):
    store, server, engine = env
    engine.page_size = 2
    for n in range(5):
        server.put_remote("favorites", "gallery", {f"arch-{n}": {"added_at": "2026-10-16T09:00:00Z"}})
        server.put_remote("settings", f"x-unknown-{n}", {"a": n})
    run(engine.pull())
    assert len([c for c in server.calls if c[0] == "changes"]) >= 3
    assert store.state(ORG).cursor == server.head


def test_a_newer_schema_version_is_never_overwritten(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "light"}, schema_version=2)
    store.set_local("voicestudio.theme.v2", "dark", origin="renderer")
    run(engine.pull())
    assert theme() == "dark"
    assert "settings" in engine.status.needs_update


def test_unknown_content_fields_are_kept_when_pushing(env):
    store, server, engine = env
    server.put_remote("settings", "history", {"generationHistoryCap": 10}, extra={"x_future": {"a": 1}})
    run(engine.run_once())
    prefs.set_("generation_history_cap", 20)
    run(engine.run_once())
    content = server.items[("settings", "history")]["content"]
    assert content == {"values": {"generationHistoryCap": 20}, "x_future": {"a": 1}}


def test_first_sync_merges_with_an_empty_base_and_local_wins(env):
    store, server, engine = env
    store.save_state(ORG, last_full_sync_at=None, cursor=0)
    store.set_local("voicestudio.theme.v2", "dark", origin="renderer")
    server.put_remote("settings", "appearance", {"theme": "light", "locale": "en"})
    run(engine.run_once())
    assert theme() == "dark"
    assert store.get_local("voicestudio.locale") == "en"
    assert server.values("settings", "appearance") == {"theme": "dark", "locale": "en"}
    assert engine.status.conflicts[-1]["keys"] == ["theme"]
    assert store.state(ORG).last_full_sync_at


def test_expired_cursor_does_a_full_resync_keeping_local_edits(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    store.set_local("voicestudio.review-mode.v1", True, origin="renderer")
    engine.local_changed("settings", "review")  # what the renderer-mirror route announces
    run(engine.run_once())
    assert server.values("settings", "review") == {"reviewMode": True}
    run(engine.run_once())  # pull our own echoes so the cursor moves past them
    assert store.state(ORG).cursor == server.head
    # The server pruned history: both items vanish there.
    server.items.clear()
    server.oldest_cursor = server.head + 1
    server.head += 1
    prefs.set_("generation_history_cap", 41)  # changed locally after the last sync
    run(engine.run_once())
    assert server.values("settings", "history") == {"generationHistoryCap": 41}  # pushed as new
    # A settings group the server no longer has keeps its local values (never wiped).
    assert SyncStore().get_local("voicestudio.review-mode.v1") is True
    assert store.item(ORG, "settings", "review") is None  # only the merge base is forgotten


def test_mass_delete_waits_for_confirmation(env):
    store, server, engine = env
    for n in range(25):
        server.put_remote("settings", f"group-{n}", {"a": n})
        store.save_item(ORG, {**server.items[("settings", f"group-{n}")]})
        store.queue(ORG, "settings", f"group-{n}", "delete")
    run(engine.push())
    assert engine.status.pending_mass_delete == {"count": 25}
    assert not [c for c in server.calls if c[0] in ("delete", "batch")]
    engine.confirm_deletes()
    run(engine.push())
    assert engine.status.pending_mass_delete is None
    assert all(server.items[("settings", f"group-{n}")]["deleted"] for n in range(25))


def test_quota_keeps_the_row_and_reports_storage_full(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    server.fail_next = [SyncApiError(413, "storage_quota_exceeded", {"used_bytes": 10, "limit_bytes": 10})]
    run(engine.push())
    assert len(store.outbox(ORG)) == 1
    assert engine.status.storage_full is True


def test_not_entitled_pauses_the_collection(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    server.fail_next = [SyncApiError(403, "not_entitled", {"entitlement_key": "sync.settings"})]
    run(engine.push())
    assert engine.status.paused == {"settings": "not_entitled"}
    assert len(store.outbox(ORG)) == 1


def test_a_revoked_device_stops_sync(env):
    store, server, engine = env
    server.fail_next = [SyncApiError(403, "device_revoked")]
    run(engine.run_once())
    assert engine.status.stopped == "device_revoked"
    calls = len(server.calls)
    run(engine.run_once())
    assert len(server.calls) == calls


def test_a_server_error_keeps_the_idempotency_key_for_the_retry(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    key = store.outbox(ORG)[0].idempotency_key
    server.fail_next = [SyncApiError(503, "sync_unavailable")]
    run(engine.push())
    assert engine.status.offline is True
    run(engine.push())
    assert server.keys_seen == [key]
    assert engine.status.offline is False


def test_rate_limited_waits_and_retries_with_the_same_key(env):
    store, server, engine = env
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    engine.sleep = sleep
    prefs.set_("generation_history_cap", 40)
    server.fail_next = [SyncApiError(429, "rate_limited", retry_after=3)]
    run(engine.push())
    assert waits == [3]
    assert server.values("settings", "history") == {"generationHistoryCap": 40}


def test_switched_off_mid_round_changes_nothing(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    server.off = True
    run(engine.run_once())
    assert len(store.outbox(ORG)) == 1
    assert server.calls == []


def test_more_than_ten_waiting_rows_go_in_one_batch(env):
    store, server, engine = env
    for n in range(12):
        store.save_item(ORG, server.put_remote("settings", f"group-{n}", {"a": n}))
    for n in range(12):
        store.queue(ORG, "settings", f"group-{n}", "delete")
    engine.confirm_deletes()
    run(engine.push())
    assert [c for c in server.calls if c[0] == "batch"] == [("batch", 12)]
    assert store.outbox(ORG) == []


def test_never_two_rounds_at_once(env):
    store, server, engine = env
    active = []
    peak = []
    original = server.changes

    async def slow_changes(**kwargs):
        active.append(1)
        peak.append(len(active))
        await asyncio.sleep(0.01)
        try:
            return await original(**kwargs)
        finally:
            active.pop()

    server.changes = slow_changes

    async def both():
        await asyncio.gather(engine.run_once(), engine.run_once())

    run(both())
    assert max(peak) == 1


def test_unchosen_collections_are_neither_pulled_nor_pushed(env):
    store, server, engine = env
    store.save_state(ORG, choices={"settings": True, "shortcuts": False, "favorites": False})
    store.set_local("voicestudio.gallery.favorites.v1", ["arch-a"], origin="renderer")
    engine.local_changed("favorites", "gallery")
    server.put_remote("favorites", "gallery", {"arch-z": {"added_at": "2026-10-16T09:00:00Z"}})
    run(engine.run_once())
    assert server.values("favorites", "gallery") == {"arch-z": {"added_at": "2026-10-16T09:00:00Z"}}
    assert SyncStore().get_local("voicestudio.gallery.favorites.v1") == ["arch-a"]


def test_device_preferences_publishes_this_devices_choices(env):
    store, server, engine = env
    run(engine.publish_choices())
    run(engine.push())
    assert server.values("device-preferences", "DvLocal000000001")["choices"]["settings"] is True


# ── review findings (final review, 2026-10-16) ─────────────────────────


def test_a_malformed_remote_item_is_skipped_and_the_round_completes(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "light"})
    bad = server.put_remote("settings", "review", None)
    server.items[("settings", "review")] = {**bad, "deleted": False, "content": {"values": "x"}}
    server.put_remote("settings", "history", {"generationHistoryCap": 9})
    run(engine.run_once())
    assert theme() == "light"
    assert prefs.get("generation_history_cap") == 9
    assert store.state(ORG).cursor == server.head


def test_a_remote_tombstone_never_wipes_local_settings(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "dark"})
    run(engine.run_once())
    server.put_remote("settings", "appearance", None)  # another device removed it from the cloud
    run(engine.run_once())
    assert theme() == "dark"


def test_full_resync_keeps_local_settings_the_server_no_longer_has(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "dark"})
    run(engine.run_once())
    run(engine.run_once())
    server.items.clear()
    server.oldest_cursor = server.head + 1
    server.head += 1
    run(engine.run_once())
    assert theme() == "dark"


def test_value_keys_this_version_does_not_know_survive_a_push(env):
    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "dark", "x_new": {"a": 1}})
    run(engine.run_once())
    store.set_local("voicestudio.theme.v2", "light", origin="renderer")
    engine.local_changed("settings", "appearance")
    run(engine.run_once())
    assert server.values("settings", "appearance") == {"theme": "light", "x_new": {"a": 1}}


def test_a_newer_schema_is_not_pushed_over_and_is_pulled_once_the_app_supports_it(env, monkeypatch):
    from services.cloud_sync import engine as engine_module

    store, server, engine = env
    server.put_remote("settings", "appearance", {"theme": "light"}, schema_version=2)
    run(engine.run_once())
    store.set_local("voicestudio.theme.v2", "dark", origin="renderer")
    engine.local_changed("settings", "appearance")
    run(engine.run_once())
    assert server.items[("settings", "appearance")]["schema_version"] == 2
    assert not [c for c in server.calls if c[0] == "put" and c[2] == "appearance"]
    monkeypatch.setitem(engine_module.SUPPORTED, "settings", 2)
    fresh = SyncEngine(store, server, ORG, sleep=engine.sleep)
    run(fresh.run_once())
    assert store.item(ORG, "settings", "appearance").schema_version == 2


def test_an_edit_made_while_its_push_is_in_flight_is_not_lost(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    original = server.put_item

    async def edit_during_put(*args, **kwargs):
        result = await original(*args, **kwargs)
        prefs.set_("generation_history_cap", 41)  # the user changes it again meanwhile
        return result

    server.put_item = edit_during_put
    run(engine.push())
    assert len(store.outbox(ORG)) == 1
    server.put_item = original
    run(engine.push())
    assert server.values("settings", "history") == {"generationHistoryCap": 41}


def test_a_reused_idempotency_key_is_renewed_and_the_write_retried(env):
    store, server, engine = env
    prefs.set_("generation_history_cap", 40)
    server.fail_next = [SyncApiError(409, "idempotency_key_reused")]
    run(engine.push())
    assert server.values("settings", "history") == {"generationHistoryCap": 40}
    assert len(set(server.keys_seen)) == 1


def test_a_batch_uses_its_own_key_not_a_rows_key(env):
    store, server, engine = env
    for n in range(12):
        store.save_item(ORG, server.put_remote("settings", f"group-{n}", {"a": n}))
        store.queue(ORG, "settings", f"group-{n}", "delete")
    row_keys = {r.idempotency_key for r in store.outbox(ORG)}
    keys = []
    original = server.batch

    async def record(operations, key):
        keys.append(key)
        return await original(operations, key)

    server.batch = record
    engine.confirm_deletes()
    run(engine.push())
    assert keys and keys[0] not in row_keys


def test_choosing_a_collection_later_pulls_what_the_cloud_already_has(env):
    store, server, engine = env
    store.save_state(ORG, choices={"settings": True, "favorites": False})
    server.put_remote("favorites", "gallery", {"arch-z": {"added_at": "2026-10-16T09:00:00Z"}})
    run(engine.run_once())
    assert SyncStore().get_local("voicestudio.gallery.favorites.v1") is None
    store.save_state(ORG, choices={"settings": True, "favorites": True})
    engine.collections_chosen({"favorites"})
    run(engine.run_once())
    assert SyncStore().get_local("voicestudio.gallery.favorites.v1") == ["arch-z"]


def test_choosing_a_collection_again_drops_its_pending_removals(env):
    store, server, engine = env
    store.save_item(ORG, server.put_remote("favorites", "gallery", {"arch-a": {"added_at": "2026-10-16T09:00:00Z"}}))
    store.queue(ORG, "favorites", "gallery", "delete")
    engine.collections_chosen({"favorites"})
    assert [r for r in store.outbox(ORG) if r.op == "delete"] == []
