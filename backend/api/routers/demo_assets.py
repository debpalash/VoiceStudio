"""Dubbing demo videos: user-requested download and its status.

``POST /demo/dubbing/download`` is the only thing that ever starts an outbound
call (``services/demo_assets.py``); ``GET /demo/dubbing/status`` reads local
state only and works offline.
"""
from fastapi import APIRouter, Depends

from api.dependencies import require_admin, require_consumer
from services import demo_assets

router = APIRouter()


@router.get("/demo/dubbing/status", dependencies=[Depends(require_consumer)])
def demo_dubbing_status():
    return demo_assets.status()


@router.post("/demo/dubbing/download", dependencies=[Depends(require_admin)])
async def demo_dubbing_download():
    return await demo_assets.start_download()
