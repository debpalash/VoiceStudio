"""VoiceStudio Cloud settings sync routes (desktop only).

The renderer drives "Choose what to sync", devices and the localStorage
mirror here; Electron main calls ``/start`` and ``/stop`` when the user flips
the S0 switches. A remote administrator never reaches these: the router
requires both the admin gate and a loopback (desktop) caller.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.dependencies import require_admin, require_desktop
from core import change_hooks
from services.cloud_sync import allowlist
from services.cloud_sync.client import CloudOff, SyncApiError
from services.cloud_sync.merge import canonical
from services.cloud_sync.service import CHOICE_KEYS, get_service
from services.cloud_sync.store import SyncStore

router = APIRouter(
    prefix="/api/cloud-sync",
    tags=["cloud-sync"],
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)

_DEVICE_ID = r"^[A-Za-z0-9]{16}$"


class ChoicesBody(BaseModel):
    choices: dict[str, bool] = Field(default_factory=dict)
    remove_from_cloud: list[str] = Field(default_factory=list)
    new_device: bool = False


class LocalValue(BaseModel):
    key: str
    value: Any = None
    #: The newest mirror ``seq`` the renderer has applied: a pulled value newer than it is never overwritten.
    seen_seq: Optional[int] = None


def _choices(body: ChoicesBody) -> dict[str, bool]:
    return {k: v for k, v in body.choices.items() if k in CHOICE_KEYS}


async def _guarded(call):
    try:
        return await call
    except CloudOff:
        raise HTTPException(status_code=409, detail="cloud_off")
    except SyncApiError as error:
        raise HTTPException(status_code=502, detail=error.code)


@router.get("/status")
async def status() -> dict:
    service = get_service()
    if service.running:
        await service.refresh_server_view()
    return service.view()


@router.post("/on")
async def turn_on(body: ChoicesBody) -> dict:
    return await _guarded(get_service().turn_on(_choices(body), new_device=body.new_device))


@router.post("/off")
async def turn_off() -> dict:
    return await _guarded(get_service().turn_off())


@router.put("/choices")
async def set_choices(body: ChoicesBody) -> dict:
    remove = [name for name in body.remove_from_cloud if name in CHOICE_KEYS]
    return await _guarded(get_service().set_choices(_choices(body), remove))


@router.post("/run")
async def run_now() -> dict:
    """Window focus or "Sync now": also starts sync if it could not start earlier."""
    await get_service().nudge()
    return {"ok": True}


@router.post("/start")
async def start() -> dict:
    """Main: sync was switched on (or the app started signed in)."""
    return {"running": await get_service().start()}


@router.post("/stop")
async def stop() -> dict:
    """Main: sync or account features were switched off. Stops everything now."""
    await get_service().stop()
    return {"running": False}


@router.post("/confirm-deletes")
async def confirm_deletes() -> dict:
    return await _guarded(get_service().confirm_deletes())


@router.get("/devices")
async def devices() -> dict:
    return {"devices": await _guarded(get_service().devices())}


@router.post("/devices/{device_id}/revoke")
async def revoke(device_id: str = Path(pattern=_DEVICE_ID)) -> dict:
    try:
        return {"device": await _guarded(get_service().revoke(device_id))}
    except LookupError:
        raise HTTPException(status_code=404, detail="device_not_found")


@router.post("/use-choices-of/{device_id}")
async def use_choices_of(device_id: str = Path(pattern=_DEVICE_ID)) -> dict:
    return await _guarded(get_service().use_choices_of(device_id))


# ── renderer localStorage mirror ───────────────────────────────────────
@router.get("/local/keys")
async def local_keys() -> dict:
    return {
        "keys": list(allowlist.RENDERER_KEYS),
        "raw": sorted(allowlist.RAW_KEYS & set(allowlist.RENDERER_KEYS)),
        "partial": sorted(allowlist.PARTIAL_KEYS),
    }


@router.put("/local")
async def put_local(body: LocalValue) -> dict:
    if body.key not in allowlist.RENDERER_KEYS:
        raise HTTPException(status_code=400, detail="not_synced")
    store = SyncStore()
    current = store.local_row(body.key)
    if current is not None:
        value, seq, origin = current
        if origin == "sync" and body.seen_seq is not None and seq > body.seen_seq:
            return {"seq": seq, "applied": False}  # the renderer applies the pulled value first
        if canonical(value) == canonical(body.value):
            return {"seq": seq, "applied": False}  # unchanged: nothing to sync
    seq = store.set_local(body.key, body.value, origin="renderer")
    change_hooks.notify("local", body.key)
    return {"seq": seq, "applied": True}


@router.get("/local")
async def pulled_local(since: int = Query(0, ge=0)) -> dict:
    store = SyncStore()
    values = [
        {"key": key, "value": value, "raw": key in allowlist.RAW_KEYS}
        for _seq, key, value in store.local_since(since)
    ]
    return {"seq": store.local_seq(), "values": values}
