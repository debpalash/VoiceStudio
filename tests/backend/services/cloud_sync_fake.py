"""An in-memory VoiceStudio Cloud sync server with the SyncClient interface (tests only)."""
from __future__ import annotations

import copy
from typing import Optional

from services.cloud_sync.client import CloudOff, SyncApiError


class FakeSyncServer:
    def __init__(self) -> None:
        self.head = 0
        self.items: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple] = []
        self.fail_next: list[SyncApiError] = []
        self.keys_seen: list[str] = []
        self.off = False
        self.devices: dict[str, dict] = {}
        self.oldest_cursor = 0

    # helpers for tests
    def put_remote(self, collection: str, item_id: str, values: Optional[dict], *, schema_version: int = 1,
                   extra: Optional[dict] = None) -> dict:
        self.head += 1
        deleted = values is None
        item = {
            "collection": collection, "item_id": item_id, "revision": self.head, "deleted": deleted,
            "schema_version": schema_version,
            "content": None if deleted else {"values": values, **(extra or {})}, "blobs": [],
        }
        self.items[(collection, item_id)] = item
        return copy.deepcopy(item)

    def values(self, collection: str, item_id: str) -> Optional[dict]:
        item = self.items.get((collection, item_id))
        return None if not item or item["deleted"] else item["content"]["values"]

    def _guard(self, name: str, *args) -> None:
        if self.off:
            raise CloudOff()
        self.calls.append((name, *args))
        if self.fail_next:
            raise self.fail_next.pop(0)

    # SyncClient interface
    async def register_device(self, name, platform, app, app_version, key, os_version=""):
        self._guard("register_device", name)
        device = {"id": f"Dv{len(self.devices):014d}", "name": name, "platform": platform, "app": app}
        self.devices[device["id"]] = device
        return device

    async def list_devices(self):
        self._guard("list_devices")
        return list(self.devices.values())

    async def revoke_device(self, device_id, key):
        self._guard("revoke_device", device_id)
        return {**self.devices.pop(device_id), "revoked_at": "now"}

    async def collections(self):
        self._guard("collections")
        return [{"name": n, "entitled": True, "enabled": True} for n in ("settings", "shortcuts", "favorites", "device-preferences")]

    async def usage(self):
        self._guard("usage")
        return {"used_bytes": 0, "limit_bytes": 10, "collections": []}

    async def changes(self, *, since, collections, limit=500):
        self._guard("changes", since)
        if since and since < self.oldest_cursor:
            raise SyncApiError(410, "sync_cursor_expired", {"oldest_cursor": self.oldest_cursor})
        page = sorted(
            (i for i in self.items.values() if i["revision"] > since and (not collections or i["collection"] in collections)),
            key=lambda i: i["revision"],
        )
        chunk = page[:limit]
        has_more = len(page) > limit
        cursor = chunk[-1]["revision"] if has_more else self.head
        return {"changes": copy.deepcopy(chunk), "cursor": cursor, "has_more": has_more, "head": self.head}

    def _check(self, collection, item_id, if_match):
        current = self.items.get((collection, item_id))
        revision = current["revision"] if current else 0
        if int(if_match) != revision:
            raise SyncApiError(409, "revision_conflict", {"current": copy.deepcopy(current)})

    async def put_item(self, collection, item_id, if_match, schema_version, content, key, blobs=None):
        self._guard("put", collection, item_id, if_match)
        self.keys_seen.append(key)
        self._check(collection, item_id, if_match)
        self.head += 1
        item = {"collection": collection, "item_id": item_id, "revision": self.head, "deleted": False,
                "schema_version": schema_version, "content": copy.deepcopy(content), "blobs": []}
        self.items[(collection, item_id)] = item
        return copy.deepcopy(item)

    async def delete_item(self, collection, item_id, if_match, key):
        self._guard("delete", collection, item_id, if_match)
        self.keys_seen.append(key)
        self._check(collection, item_id, if_match)
        self.head += 1
        item = {"collection": collection, "item_id": item_id, "revision": self.head, "deleted": True,
                "schema_version": 1, "content": None, "blobs": []}
        self.items[(collection, item_id)] = item
        return copy.deepcopy(item)

    async def batch(self, operations, key):
        self._guard("batch", len(operations))
        for index, op in enumerate(operations):
            current = self.items.get((op["collection"], op["item_id"]))
            if int(op["if_match"]) != (current["revision"] if current else 0):
                raise SyncApiError(409, "batch_conflict", {"index": index, "current": copy.deepcopy(current)})
        results = []
        for op in operations:
            if op["op"] == "put":
                self.head += 1
                item = {"collection": op["collection"], "item_id": op["item_id"], "revision": self.head,
                        "deleted": False, "schema_version": op["schema_version"],
                        "content": copy.deepcopy(op["content"]), "blobs": []}
            else:
                self.head += 1
                item = {"collection": op["collection"], "item_id": op["item_id"], "revision": self.head,
                        "deleted": True, "schema_version": 1, "content": None, "blobs": []}
            self.items[(op["collection"], op["item_id"])] = item
            results.append(copy.deepcopy(item))
        return {"results": results, "head": self.head}
