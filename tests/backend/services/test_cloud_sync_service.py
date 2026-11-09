"""Sync service lifecycle: off means nothing runs, stop is immediate, turning on pulls first."""
import asyncio
import sqlite3

import pytest

from core import db as core_db
from core import prefs
from services.cloud_sync.service import CloudSyncService
from services.cloud_sync.store import SyncStore

from .cloud_sync_fake import FakeSyncServer

ORG = "Or5tG7hJ9kL1zX3c"
INFO = {"access_token": "acc", "organization_id": ORG, "sync_api": "https://appapi.voicestudio.sh/v1/sync"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
    server = FakeSyncServer()
    session = {"value": dict(INFO)}
    made = []

    def factory(info):
        made.append(info)
        return server

    service = CloudSyncService(session=lambda: session["value"], client_factory=factory, stream=False)
    return service, server, session, made


async def until(condition, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


def test_signed_out_or_switched_off_starts_nothing(setup):
    service, server, session, made = setup
    session["value"] = None

    async def body():
        assert await service.start() is False

    asyncio.run(body())
    assert made == [] and server.calls == []


def test_sync_not_turned_on_here_starts_nothing(setup):
    service, server, _, _ = setup

    async def body():
        assert await service.start() is False

    asyncio.run(body())
    assert server.calls == []


def test_turning_on_registers_the_device_and_syncs_with_a_pull_first(setup):
    service, server, _, _ = setup
    prefs.set_("generation_history_cap", 30)

    async def body():
        view = await service.turn_on({"settings": True, "shortcuts": False, "favorites": False})
        await until(lambda: server.values("settings", "history") is not None)
        await service.stop()
        return view

    view = asyncio.run(body())
    names = [c[0] for c in server.calls]
    assert names[0] == "register_device"
    assert names.index("changes") < names.index("put")
    assert view["enabled"] is True and view["choices"] == {"settings": True, "shortcuts": False, "favorites": False}
    device_id = SyncStore().state(ORG).device_id
    assert server.values("device-preferences", device_id)["choices"]["settings"] is True


def test_stop_is_immediate_and_later_changes_are_not_queued(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        await until(lambda: any(c[0] == "put" for c in server.calls))
        await service.stop()
        assert not service.running
        calls = len(server.calls)
        prefs.set_("generation_history_cap", 99)
        await asyncio.sleep(0.05)
        return calls

    calls = asyncio.run(body())
    assert len(server.calls) == calls
    assert SyncStore().outbox(ORG) == []


def test_turning_off_keeps_the_device_and_turning_back_on_reuses_it(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        first = SyncStore().state(ORG).device_id
        view = await service.turn_off()
        assert view["enabled"] is False and SyncStore().state(ORG).device_id == first
        await service.turn_on({"settings": True})
        await service.stop()
        return first

    first = asyncio.run(body())
    assert [c[0] for c in server.calls].count("register_device") == 1
    assert SyncStore().state(ORG).device_id == first


def test_also_remove_from_the_cloud_deletes_that_collection_after_switching_it_off(setup):
    service, server, _, _ = setup
    server.put_remote("favorites", "gallery", {"arch-a": {"added_at": "2026-10-16T09:00:00Z"}})

    async def body():
        await service.turn_on({"settings": True, "favorites": True})
        await until(lambda: SyncStore().item(ORG, "favorites", "gallery") is not None)
        await service.set_choices({"settings": True, "favorites": False}, ["favorites"])
        await until(lambda: server.items[("favorites", "gallery")]["deleted"])
        await service.stop()

    asyncio.run(body())
    assert server.items[("favorites", "gallery")]["deleted"] is True


def test_setting_up_again_after_removal_registers_a_new_device(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        first = SyncStore().state(ORG).device_id
        await service.turn_on({"settings": True}, new_device=True)
        await service.stop()
        return first

    first = asyncio.run(body())
    assert [c[0] for c in server.calls].count("register_device") == 2
    assert SyncStore().state(ORG).device_id != first


def test_turning_sync_on_gives_every_voice_a_sync_id(setup):
    service, _, _, _ = setup
    with sqlite3.connect(core_db.DB_PATH) as conn:
        conn.execute("INSERT INTO voice_profiles (id, name, created_at) VALUES ('ab12cd34', 'Mine', 1)")

    async def body():
        await service.turn_on({"settings": True})
        await service.stop()

    asyncio.run(body())
    with sqlite3.connect(core_db.DB_PATH) as conn:
        sync_id = conn.execute("SELECT sync_id FROM voice_profiles WHERE id='ab12cd34'").fetchone()[0]
    assert sync_id and len(sync_id) == 16


# ── review findings (final review, 2026-10-16) ─────────────────────────


def test_an_unexpected_error_in_a_round_does_not_end_sync(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        await until(lambda: service.engine is not None)
        original = service.engine.run_once
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            await original()

        service.engine.run_once = flaky
        service.trigger()
        await until(lambda: len(calls) >= 1)
        service.trigger()
        await until(lambda: len(calls) >= 2)
        assert service.running
        await service.stop()

    asyncio.run(body())


def test_a_change_during_a_round_still_gets_its_own_round(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        await until(lambda: service.engine is not None and service.engine.status.last_sync_at is not None)
        rounds = []
        original = service.engine.run_once

        async def slow():
            rounds.append(1)
            if len(rounds) == 1:
                service.trigger()  # arrives while this round runs
                await asyncio.sleep(0.02)
            await original()

        service.engine.run_once = slow
        service.trigger()
        await until(lambda: len(rounds) >= 2)
        await service.stop()

    asyncio.run(body())


def test_a_write_from_a_worker_thread_schedules_a_round(setup, monkeypatch):
    from services.cloud_sync import service as service_module

    monkeypatch.setattr(service_module, "DEBOUNCE_SECONDS", 0.01)
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        await until(lambda: server.values("device-preferences", SyncStore().state(ORG).device_id) is not None)
        await asyncio.to_thread(prefs.set_, "generation_history_cap", 77)  # e.g. a sync def route
        await until(lambda: server.values("settings", "history") == {"generationHistoryCap": 77})
        await service.stop()

    asyncio.run(body())


def test_turning_off_while_turning_on_wins(setup):
    service, server, _, _ = setup
    gate = asyncio.Event()
    original = server.register_device

    async def slow_register(*args, **kwargs):
        await gate.wait()
        return await original(*args, **kwargs)

    server.register_device = slow_register

    async def body():
        on = asyncio.create_task(service.turn_on({"settings": True}))
        await asyncio.sleep(0.01)
        off = asyncio.create_task(service.turn_off())
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.gather(on, off, return_exceptions=True)
        assert not service.running
        assert SyncStore().state(ORG).enabled is False

    asyncio.run(body())


def test_a_double_turn_on_registers_one_device(setup):
    service, server, _, _ = setup

    async def body():
        await asyncio.gather(service.turn_on({"settings": True}), service.turn_on({"settings": True}))
        await service.stop()

    asyncio.run(body())
    assert [c[0] for c in server.calls].count("register_device") == 1


def test_a_nudge_starts_sync_when_it_could_not_start_at_boot(setup):
    service, server, session, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        await service.stop()
        session["value"] = None  # offline at boot
        assert await service.start() is False
        session["value"] = dict(INFO)
        await service.nudge()
        assert service.running
        await service.stop()

    asyncio.run(body())


def test_signing_in_to_another_account_restarts_on_its_organization(setup):
    service, server, session, _ = setup
    other = {**INFO, "organization_id": "OrOther000000001"}

    async def body():
        await service.turn_on({"settings": True})
        assert service.organization_id == ORG
        SyncStore().save_state(other["organization_id"], device_id="DvOther000000001", enabled=True,
                               choices={"settings": True})
        session["value"] = other
        assert await service.start() is True
        assert service.organization_id == "OrOther000000001"
        await service.stop()

    asyncio.run(body())


def test_os_version_is_short_single_line_text():
    from services.cloud_sync.service import _os_version

    value = _os_version()
    assert 1 <= len(value) <= 64
    assert "\n" not in value and "\r" not in value and "\x00" not in value


def test_a_setting_changed_while_sync_was_stopped_is_pushed_when_it_starts_again(setup):
    service, server, _, _ = setup
    prefs.set_("generation_history_cap", 30)

    async def body():
        await service.turn_on({"settings": True})
        await until(lambda: server.values("settings", "history") == {"generationHistoryCap": 30})
        await service.stop()
        prefs.set_("generation_history_cap", 31)  # no hooks while stopped
        assert await service.start()
        await until(lambda: server.values("settings", "history") == {"generationHistoryCap": 31})
        await service.stop()

    asyncio.run(body())


def test_only_a_device_the_server_lists_can_be_removed(setup):
    service, server, _, _ = setup

    async def body():
        await service.turn_on({"settings": True})
        other = await server.register_device("MacBook", "macos", "oss", "0.6.0", "k" * 32)
        removed = await service.revoke(other["id"])
        assert removed["id"] == other["id"]
        calls = [c for c in server.calls if c[0] == "revoke_device"]
        assert calls == [("revoke_device", other["id"])]
        with pytest.raises(LookupError):
            await service.revoke("DvNotListed00001")
        assert [c for c in server.calls if c[0] == "revoke_device"] == calls  # nothing sent
        await service.stop()

    asyncio.run(body())
