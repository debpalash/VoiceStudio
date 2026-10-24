"""A design profile's identity sample renders with the seed it was saved with (#3254).

Saving used to render the sample at the fixed archetype seed (42), so the saved
voice did not sound like the take the user heard. Both the save-time render and
the deferred render behind ``GET /profiles/{id}/audio`` now use the stored seed.
"""
from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import archetypes as arch_router


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x01" * 64)
    return buf.getvalue()


@pytest.fixture
def design_client(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    seeds: list[int | None] = []

    async def render(a, out_path, **_kwargs):
        seeds.append(a.get("seed"))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(_wav_bytes())

    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)
    app = FastAPI()
    app.include_router(profiles.router)
    with TestClient(app) as client:
        yield client, seeds


def _engine_loaded(monkeypatch, loaded: bool) -> None:
    from services import model_manager

    monkeypatch.setattr(model_manager, "get_model_status", lambda: {"loaded": loaded})


def _save_design(client, **extra) -> str:
    data = {"name": "Low voice", "kind": "design", "vd_states": json.dumps({}), **extra}
    response = client.post("/profiles", data=data)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_save_renders_the_sample_with_the_submitted_seed(design_client, monkeypatch):
    client, seeds = design_client
    _engine_loaded(monkeypatch, True)
    _save_design(client, seed="76785")
    assert seeds == [76785]


def test_deferred_sample_render_uses_the_stored_seed(design_client, monkeypatch):
    client, seeds = design_client
    _engine_loaded(monkeypatch, False)
    profile_id = _save_design(client, seed="76785")
    assert seeds == []
    assert client.get(f"/profiles/{profile_id}/audio").status_code == 200
    assert seeds == [76785]


def test_save_without_a_seed_keeps_the_default(design_client, monkeypatch):
    client, seeds = design_client
    _engine_loaded(monkeypatch, True)
    _save_design(client)
    assert seeds == [42]
