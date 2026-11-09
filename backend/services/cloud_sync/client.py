"""HTTP client for the VoiceStudio Cloud sync API (vssaas sync-api.md).

Every request asks ``token()`` first; ``None`` raises ``CloudOff`` before any
byte is sent, which is how the S0 switches stop sync. The client never
follows redirects and never logs a token.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator, Callable, Optional

import httpx

TokenProvider = Callable[..., Optional[str]]  # token(fresh=False)


class CloudOff(Exception):
    """Sync or account features are switched off (or signed out): make no call."""


class SyncApiError(Exception):
    def __init__(self, status: int, code: str, details: Optional[dict] = None,
                 retry_after: Optional[float] = None, request_id: Optional[str] = None) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.details = details or {}
        self.retry_after = retry_after
        self.request_id = request_id


def _error(response: httpx.Response) -> SyncApiError:
    try:
        body = response.json().get("error") or {}
    except (ValueError, AttributeError):
        body = {}
    retry_after = response.headers.get("retry-after")
    try:
        seconds = float(retry_after) if retry_after is not None else None
    except ValueError:
        seconds = None
    return SyncApiError(
        response.status_code,
        str(body.get("code") or f"http_{response.status_code}"),
        body.get("details") if isinstance(body.get("details"), dict) else {},
        seconds,
        body.get("request_id") or response.headers.get("x-request-id"),
    )


_ORGANIZATION_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")


#: The sync API of each environment (vssaas desktop-app-guide §1). The client talks
#: to one of these and nothing else, whatever the bridge answers.
_KNOWN_SYNC_APIS = (
    "https://appapi.voicestudio.sh/v1/sync",
    "https://dev-appapi.voicestudio.sh/v1/sync",
    "http://127.0.0.1:8090/v1/sync",
)


def _checked_base(base_url: str) -> str:
    """One of the known sync endpoints; anything else is refused."""
    wanted = (base_url or "").rstrip("/")
    for known in _KNOWN_SYNC_APIS:
        if wanted == known:
            return known
    raise ValueError("refusing a sync endpoint outside the known VoiceStudio Cloud environments")


def _checked_organization(organization_id: str) -> str:
    if not _ORGANIZATION_ID.fullmatch(organization_id or ""):
        raise ValueError("refusing an Organization ID that is not Base62")
    return organization_id


class SyncClient:
    def __init__(self, base_url: str, organization_id: str, *, token: TokenProvider,
                 device_id: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None,
                 timeout: float = 30.0) -> None:
        self.base = f"{_checked_base(base_url)}/organizations/{_checked_organization(organization_id)}"
        self.device_id = device_id
        self._token = token
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self, *, device: bool = True, key: Optional[str] = None,
                       if_match: Optional[int] = None, fresh: bool = False) -> dict[str, str]:
        token = await asyncio.to_thread(self._token, True) if fresh else await asyncio.to_thread(self._token)
        if not token:
            raise CloudOff()
        headers = {"Authorization": f"Bearer {token}"}
        if device and self.device_id:
            headers["X-Sync-Device"] = self.device_id
        if key:
            headers["Idempotency-Key"] = key
        if if_match is not None:
            headers["If-Match"] = f'"{int(if_match)}"'
        return headers

    async def _call(self, method: str, path: str, *, json: Any = None, params: Optional[dict] = None,
                    device: bool = True, key: Optional[str] = None, if_match: Optional[int] = None) -> Any:
        headers = await self._headers(device=device, key=key, if_match=if_match)
        response = await self._http.request(method, self.base + path, json=json, params=params, headers=headers)
        if response.status_code == 401:
            # Refresh once (main replaces the token), then retry the same request.
            headers = await self._headers(device=device, key=key, if_match=if_match, fresh=True)
            response = await self._http.request(method, self.base + path, json=json, params=params, headers=headers)
        if response.status_code >= 400:
            raise _error(response)
        return response.json() if response.content else {}

    # ── devices ────────────────────────────────────────────────────────
    async def register_device(self, name: str, platform: str, app: str, app_version: str, key: str,
                              os_version: str = "") -> dict:
        body = {"name": name, "platform": platform, "app": app, "app_version": app_version}
        if os_version:
            body["os_version"] = os_version  # informational (devices-and-sessions)
        return (await self._call("POST", "/devices", json=body, device=False, key=key))["device"]

    async def list_devices(self) -> list[dict]:
        return (await self._call("GET", "/devices"))["devices"]

    async def revoke_device(self, device_id: str, key: str) -> dict:
        return (await self._call("POST", f"/devices/{device_id}/revoke", json={}, key=key))["device"]

    # ── collections and usage ──────────────────────────────────────────
    async def collections(self) -> list[dict]:
        return (await self._call("GET", "/collections"))["collections"]

    async def usage(self) -> dict:
        return await self._call("GET", "/usage")

    # ── items ──────────────────────────────────────────────────────────
    async def changes(self, *, since: int, collections: list[str], limit: int = 500) -> dict:
        params = {"since": str(since), "limit": str(limit)}
        if collections:
            params["collections"] = ",".join(collections)
        return await self._call("GET", "/changes", params=params)

    async def get_item(self, collection: str, item_id: str) -> dict:
        return await self._call("GET", f"/items/{collection}/{item_id}")

    async def put_item(self, collection: str, item_id: str, if_match: int, schema_version: int,
                       content: dict, key: str, blobs: Optional[list] = None) -> dict:
        body = {"schema_version": schema_version, "content": content, "blobs": blobs or []}
        return await self._call("PUT", f"/items/{collection}/{item_id}", json=body, key=key, if_match=if_match)

    async def delete_item(self, collection: str, item_id: str, if_match: int, key: str) -> dict:
        return await self._call("DELETE", f"/items/{collection}/{item_id}", key=key, if_match=if_match)

    async def batch(self, operations: list[dict], key: str) -> dict:
        return await self._call("POST", "/batch", json={"operations": operations}, key=key)

    # ── events ─────────────────────────────────────────────────────────
    async def events(self, last_event_id: Optional[int]) -> AsyncIterator[str]:
        """Yield raw SSE lines until the server closes the stream."""
        headers = await self._headers()
        headers["Accept"] = "text/event-stream"
        if last_event_id:
            headers["Last-Event-ID"] = str(last_event_id)
        timeout = httpx.Timeout(30.0, read=None)
        async with self._http.stream("GET", self.base + "/events", headers=headers, timeout=timeout) as response:
            if response.status_code >= 400:
                await response.aread()
                raise _error(response)
            async for line in response.aiter_lines():
                yield line
