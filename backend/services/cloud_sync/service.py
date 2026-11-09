"""Runs the Cloud sync engine for the app: lifecycle, triggers and the UI's view.

Started at backend start-up and whenever main says sync was switched on;
stopped at once when main says it was switched off. Every Cloud call still
asks ``cloud_account`` for a token, so even a missed stop sends nothing.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import socket
from typing import Any, Callable, Optional

from core.version import APP_VERSION
from services import cloud_account
from services.cloud_sync import allowlist
from services.cloud_sync.client import CloudOff, SyncApiError, SyncClient
from services.cloud_sync.engine import DEVICE_PREFERENCES, SyncEngine, _safe_values as _values
from services.cloud_sync.events import POLL_SECONDS, EventStream
from services.cloud_sync.record_ids import ensure_sync_ids
from services.cloud_sync.store import SyncStore, new_idempotency_key

logger = logging.getLogger("omnivoice.cloud_sync")

DEBOUNCE_SECONDS = 1.5
CHOICE_KEYS = ("settings", "shortcuts", "favorites")


def _platform() -> str:
    system = platform.system().lower()
    return {"darwin": "macos", "windows": "windows"}.get(system, "linux")


def _os_version() -> str:
    """Short OS label shown in the Devices list: "macOS 15.1", "Windows 10.0.22631", "Linux 6.12"."""
    system = platform.system()
    if system == "Darwin":
        label = f"macOS {platform.mac_ver()[0]}".strip()
    elif system == "Windows":
        label = f"Windows {platform.version()}".strip()
    else:
        label = f"{system or 'Linux'} {platform.release().split('-')[0]}".strip()
    clean = "".join(ch for ch in label if ch.isprintable())
    return (clean or "unknown")[:64]


def _device_name() -> str:
    name = (socket.gethostname() or "VoiceStudio").split(".")[0].strip()
    return (name or "VoiceStudio")[:64]


def clean_choices(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw.get(key) is True for key in CHOICE_KEYS}


class CloudSyncService:
    def __init__(self, *, store: Optional[SyncStore] = None,
                 session: Callable[[], Optional[dict]] = cloud_account.session,
                 client_factory: Optional[Callable[[dict], Any]] = None,
                 stream: bool = True, app: str = "oss") -> None:
        self.store = store or SyncStore()
        self._session = session
        self._client_factory = client_factory or self._default_client
        self._stream_enabled = stream
        self.app = app
        self.engine: Optional[SyncEngine] = None
        self.client: Any = None
        self.organization_id: Optional[str] = None
        self._unhook: Optional[Callable[[], None]] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._debounce: Optional[asyncio.TimerHandle] = None
        self._stream_up = False
        self.collections: list[dict] = []
        self.usage: dict = {}
        self._view_at = 0.0
        #: Serializes start/stop/turn_on/turn_off/choices, so "off" can never be undone by a slower "on".
        self._lock = asyncio.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    @staticmethod
    def _default_client(info: dict) -> SyncClient:
        def token(fresh: bool = False) -> Optional[str]:
            return cloud_account.access_token(fresh=fresh)

        return SyncClient(info["sync_api"], info["organization_id"], token=token)

    # ── lifecycle ──────────────────────────────────────────────────────
    async def _info(self) -> Optional[dict]:
        return await asyncio.to_thread(self._session)

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> bool:
        """Start the loop if sync is on here and main hands out a token."""
        async with self._lock:
            return await self._start()

    async def nudge(self) -> None:
        """Window focus or "Sync now": start if sync could not start earlier, else run a round."""
        async with self._lock:
            if not self.running:
                await self._start()
        self.trigger()

    async def _start(self) -> bool:
        info = await self._info()
        if not info:
            return False
        if self.running:
            if info["organization_id"] == self.organization_id:
                return True
            await self._stop()  # signed in to another account: its own Organization and device
        state = self.store.state(info["organization_id"])
        if not state or not state.enabled or not state.device_id:
            self.organization_id = info["organization_id"]
            return False
        self._event_loop = asyncio.get_running_loop()
        self._open(info, state.device_id)
        self._loop_task = asyncio.create_task(self._loop(), name="cloud-sync-loop")
        if self._stream_enabled:
            self._stream_task = asyncio.create_task(self._stream(), name="cloud-sync-events")
        return True

    def _open(self, info: dict, device_id: Optional[str]) -> None:
        self.organization_id = info["organization_id"]
        if self.client is None:
            self.client = self._client_factory(info)
        self.client.device_id = device_id
        if self.engine is None:
            self.engine = SyncEngine(self.store, self.client, self.organization_id, on_local_change=self.schedule)
            self._unhook = self.engine.attach_hooks()

    async def stop(self) -> None:
        """Stop everything now. Local data and the device registration are kept."""
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        for task in (self._loop_task, self._stream_task):
            if task and not task.done():
                task.cancel()
        for task in (self._loop_task, self._stream_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._loop_task = self._stream_task = None
        if self._debounce:
            self._debounce.cancel()
            self._debounce = None
        if self._unhook:
            self._unhook()
            self._unhook = None
        self.engine = None
        if self.client is not None and hasattr(self.client, "aclose"):
            await self.client.aclose()
        self.client = None
        self._stream_up = False

    # ── triggers ───────────────────────────────────────────────────────
    def schedule(self) -> None:
        """A local change: run a round 1.5 s after the last one (callable from any thread)."""
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._debounce_now()
        else:
            loop.call_soon_threadsafe(self._debounce_now)  # a sync route in a worker thread

    def _debounce_now(self) -> None:
        if self._debounce:
            self._debounce.cancel()
        self._debounce = asyncio.get_running_loop().call_later(DEBOUNCE_SECONDS, self._wake.set)

    def trigger(self) -> None:
        loop = self._event_loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    async def _loop(self) -> None:
        failures = 0
        while True:
            if self.engine is None:
                return
            self._wake.clear()  # before the round: a wake-up during it is kept
            try:
                await self.engine.run_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad round must not end sync for good
                failures += 1
                logger.exception("cloud sync round failed")
                await asyncio.sleep(min(60.0, 2.0 ** failures))
                continue
            if self.engine is None or self.engine.status.stopped:
                return
            timeout = None if self._stream_up else POLL_SECONDS
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                continue  # the 60-second poll while the event stream is down

    async def _stream(self) -> None:
        async def on_head(collections: list[str]) -> None:
            self._stream_up = True
            if self.engine and set(collections) & self.engine.chosen():
                self.trigger()

        def on_revoked(reason: str) -> None:
            if self.engine:
                self.engine.status.stopped = reason
            self.trigger()

        async def poll() -> None:
            self._stream_up = False
            self.trigger()

        def open_lines(last_event_id: Optional[str]):
            self._stream_up = True
            return self.client.events(int(last_event_id) if last_event_id else None)

        await EventStream(open_lines, on_head=on_head, on_revoked=on_revoked, poll=poll).run()
        self._stream_up = False

    # ── turning sync on and off, choices ───────────────────────────────
    async def turn_on(self, choices: dict, new_device: bool = False) -> dict:
        """Turn sync on here; ``new_device`` after "This device was removed from sync"."""
        async with self._lock:
            return await self._turn_on(choices, new_device)

    async def _turn_on(self, choices: dict, new_device: bool) -> dict:
        info = await self._info()
        if not info:
            raise CloudOff()
        org = info["organization_id"]
        state = self.store.state(org)
        if new_device and state:
            await self._stop()
            state = self.store.save_state(org, device_id=None)
        self._open(info, state.device_id if state else None)
        device_id = state.device_id if state else None
        if not device_id:
            device = await self.client.register_device(_device_name(), _platform(), self.app, APP_VERSION,
                                                       new_idempotency_key(), os_version=_os_version())
            device_id = device["id"]
            self.client.device_id = device_id
        # Records get their sync_id the first time sync is turned on (S2 prep).
        await asyncio.to_thread(ensure_sync_ids)
        # Turning sync (back) on: full pull (cursor 0) and merge before any push (seeded first sync).
        self.store.save_state(org, device_id=device_id, enabled=True, choices=clean_choices(choices),
                              last_full_sync_at=None, cursor=0)
        await self.engine.publish_choices()
        await self._stop()
        await self._start()
        self.trigger()
        return self.view()

    async def turn_off(self) -> dict:
        async with self._lock:
            info = await self._info()
            org = (info or {}).get("organization_id") or self.organization_id
            await self._stop()
            if org and self.store.state(org):
                self.store.save_state(org, enabled=False)
            return self.view()

    async def set_choices(self, choices: dict, remove_from_cloud: list[str]) -> dict:
        async with self._lock:
            return await self._set_choices(choices, remove_from_cloud)

    async def _set_choices(self, choices: dict, remove_from_cloud: list[str]) -> dict:
        if not self.engine or not self.organization_id:
            raise CloudOff()
        org = self.organization_id
        previous = (self.store.state(org) or None)
        self.store.save_state(org, choices=clean_choices(choices))
        for name in remove_from_cloud:
            for collection in allowlist.CHOICE_COLLECTIONS.get(name, ()):
                for base in self.store.items(org, collection):
                    if not base.deleted:
                        self.store.queue(org, collection, base.item_id, "delete")
        newly_on = {k for k in CHOICE_KEYS if clean_choices(choices)[k] and not (previous and previous.choices.get(k))}
        if newly_on:
            self.store.save_state(org, last_full_sync_at=None)  # merge local items of the new toggles first
            self.engine.collections_chosen(
                {c for name in newly_on for c in allowlist.CHOICE_COLLECTIONS.get(name, ())})
        await self.engine.publish_choices()
        if remove_from_cloud:
            self.engine.confirm_deletes()  # the UI already asked, showing the count and size
            for name in remove_from_cloud:
                self.engine.draining.update(allowlist.CHOICE_COLLECTIONS.get(name, ()))
        self.trigger()
        return self.view()

    async def confirm_deletes(self) -> dict:
        if self.engine:
            self.engine.confirm_deletes()
            self.trigger()
        return self.view()

    async def use_choices_of(self, device_id: str) -> dict:
        if not self.organization_id:
            raise CloudOff()
        base = self.store.item(self.organization_id, DEVICE_PREFERENCES, device_id)
        values = _values(base) or {}
        choices = values.get("choices") if isinstance(values.get("choices"), dict) else {}
        return await self.set_choices(choices, [])

    # ── reads for the UI ───────────────────────────────────────────────
    async def refresh_server_view(self) -> None:
        if not self.client:
            return
        try:
            self.collections = await self.client.collections()
            self.usage = await self.client.usage()
        except (CloudOff, SyncApiError, Exception) as error:  # noqa: BLE001 — the view degrades, sync keeps going
            logger.info("cloud sync view unavailable: %s", type(error).__name__)

    async def devices(self) -> list[dict]:
        if not self.client:
            raise CloudOff()
        return await self.client.list_devices()

    async def revoke(self, device_id: str) -> dict:
        """Remove one of this account's devices, as the server lists them.

        The request uses the server's own copy of the ID, never the caller's
        text, so only a listed device can ever be addressed.
        """
        if not self.client:
            raise CloudOff()
        listed = next((d["id"] for d in await self.client.list_devices() if d.get("id") == device_id), None)
        if listed is None:
            raise LookupError("not one of this account's devices")
        return await self.client.revoke_device(listed, new_idempotency_key())

    def other_devices_choices(self) -> list[dict]:
        if not self.organization_id:
            return []
        state = self.store.state(self.organization_id)
        own = state.device_id if state else None
        return [
            {"device_id": base.item_id, "choices": (_values(base) or {}).get("choices") or {}}
            for base in self.store.items(self.organization_id, DEVICE_PREFERENCES)
            if base.item_id != own and not base.deleted
        ]

    def view(self) -> dict:
        state = self.store.state(self.organization_id) if self.organization_id else None
        status = self.engine.status if self.engine else None
        return {
            "enabled": bool(state and state.enabled),
            "running": self.running,
            "device_id": state.device_id if state else None,
            "choices": clean_choices(state.choices if state else {}),
            "collections": self.collections,
            "usage": self.usage,
            "other_devices": self.other_devices_choices(),
            "status": {
                "conflicts": status.conflicts if status else [],
                "needs_update": sorted(status.needs_update) if status else [],
                "paused": status.paused if status else {},
                "storage_full": bool(status and status.storage_full),
                "read_only": bool(status and status.read_only),
                "offline": bool(status and status.offline),
                "stopped": status.stopped if status else None,
                "pending_mass_delete": status.pending_mass_delete if status else None,
                "last_sync_at": status.last_sync_at if status else None,
            },
        }


_service: Optional[CloudSyncService] = None


def get_service() -> CloudSyncService:
    global _service
    if _service is None:
        _service = CloudSyncService()
    return _service
