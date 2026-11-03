import uuid
import time
import json
from fastapi import APIRouter, Depends, HTTPException
from api.dependencies import require_admin
from pydantic import BaseModel

from core.db import db_conn
from core import event_bus
from schemas.requests import ProjectSaveRequest

router = APIRouter(dependencies=[Depends(require_admin)])


class ProjectRenameRequest(BaseModel):
    name: str

@router.get("/projects")
async def list_projects():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, video_path, duration, created_at, updated_at FROM studio_projects ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM studio_projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    result = dict(row)
    if result.get("state_json"):
        try:
            result["state"] = json.loads(result["state_json"])
        except Exception:
            result["state"] = {}
    else:
        result["state"] = {}
    return result

@router.post("/projects")
async def create_project(req: ProjectSaveRequest):
    project_id = str(uuid.uuid4())[:8]
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO studio_projects (id, name, video_path, audio_path, duration, state_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (project_id, req.name, req.video_path, req.audio_path, req.duration, json.dumps(req.state), now, now),
        )
    event_bus.emit("projects", {"action": "created", "id": project_id})
    return {"id": project_id, "name": req.name, "created_at": now}

@router.put("/projects/{project_id}")
async def update_project(project_id: str, req: ProjectSaveRequest):
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM studio_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")
        now = time.time()
        conn.execute(
            "UPDATE studio_projects SET name=?, video_path=?, audio_path=?, duration=?, state_json=?, updated_at=? WHERE id=?",
            (req.name, req.video_path, req.audio_path, req.duration, json.dumps(req.state), now, project_id),
        )
    event_bus.emit("projects", {"action": "updated", "id": project_id})
    return {"id": project_id, "name": req.name, "updated_at": now}

@router.patch("/projects/{project_id}")
async def rename_project(project_id: str, req: ProjectRenameRequest):
    """Lightweight rename — updates only the project name (and updated_at),
    without re-serialising the whole state blob like PUT does."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")
    now = time.time()
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM studio_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")
        conn.execute(
            "UPDATE studio_projects SET name=?, updated_at=? WHERE id=?",
            (name, now, project_id),
        )
    event_bus.emit("projects", {"action": "renamed", "id": project_id})
    return {"id": project_id, "name": name, "updated_at": now}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    with db_conn() as conn:
        conn.execute("DELETE FROM studio_projects WHERE id=?", (project_id,))
    event_bus.emit("projects", {"action": "deleted", "id": project_id})
    return {"deleted": project_id}


# Project handoff shares the existing authenticated projects router.
from api.routers.project_bundles import router as project_bundles_router
router.include_router(project_bundles_router)
