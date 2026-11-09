"""/v1/audio/speech forwards the VoiceStudio extension controls to the engine."""
from __future__ import annotations

import importlib
import os
import uuid

import pytest
import torch
from fastapi.testclient import TestClient

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture()
def engine(monkeypatch):
    tts = importlib.import_module("services.tts_backend")

    class _Engine(tts.TTSBackend):
        id = "ext-controls-engine"
        display_name = "Extension controls engine (test)"
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).calls.append(kw)
            return torch.zeros(1, 2400)

    _Engine.calls = []
    inst = _Engine()
    monkeypatch.setitem(tts._REGISTRY, _Engine.id, _Engine)
    monkeypatch.setattr(tts, "get_active_tts_backend", lambda: inst)
    monkeypatch.setattr(tts, "get_engine_instance_for", lambda _id: inst)
    from services import engine_routing

    async def _profile(*_a, **_k):
        return {"routing_status": "native", "routing_reason": ""}

    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", _profile)
    return _Engine


@pytest.fixture()
def client():
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def profile_with_seed():
    from core.db import db_conn, init_db

    init_db()
    pid = str(uuid.uuid4())
    name = f"seeded-{pid[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, created_at, ref_audio_path, seed) "
            "VALUES (?, ?, 'clone', 0, ?, 4242)",
            (pid, name, f"{pid}.wav"),
        )
    yield name
    with db_conn() as conn:
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


def _speak(client, **extra):
    body = {"model": "tts-1", "voice": "alloy", "input": "Hi.", "response_format": "wav", **extra}
    return client.post("/v1/audio/speech", json=body)


def test_defaults_send_only_the_always_on_flags(client, engine):
    assert _speak(client).status_code == 200
    kw = engine.calls[-1]
    assert kw["denoise"] is True and kw["preprocess_prompt"] is True
    for key in ("duration", "seed", "chunk_duration", "chunk_threshold"):
        assert key not in kw


def test_extension_fields_are_forwarded_including_zero(client, engine):
    res = _speak(
        client, duration=2.5, seed=0, denoise=False, preprocess_prompt=False,
        chunk_duration=0, chunk_threshold=0,
    )
    assert res.status_code == 200, res.text
    kw = engine.calls[-1]
    assert kw["duration"] == 2.5
    assert kw["seed"] == 0
    assert kw["denoise"] is False and kw["preprocess_prompt"] is False
    assert kw["chunk_duration"] == 0 and kw["chunk_threshold"] == 0


@pytest.mark.parametrize("field,value", [("duration", 0), ("chunk_duration", -1), ("chunk_threshold", -0.5)])
def test_out_of_range_values_are_rejected(client, engine, field, value):
    assert _speak(client, **{field: value}).status_code in (400, 422)
    assert engine.calls == []


def test_profile_seed_is_default_and_request_seed_wins(client, engine, profile_with_seed):
    assert _speak(client, voice=profile_with_seed).status_code == 200
    assert engine.calls[-1]["seed"] == 4242
    assert _speak(client, voice=profile_with_seed, seed=7).status_code == 200
    assert engine.calls[-1]["seed"] == 7
