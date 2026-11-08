"""
/v1/audio/speech vs engines without quality knobs (#3654).

Several engines' runtimes have no num_step/guidance_scale options, so an
explicit value used to 200 with silently default-quality audio (the #1014
class one layer down — the reporter benchmarked 16 vs 32 and measured
identical files). The endpoint rejects explicit values per knob with a 400
naming the parameter. The engine itself stays permissive: deeper layers
can't tell an explicit request from an internal default fill (dubbing
previews always pass num_step=8). Per-engine audit in the PR thread.
"""
from __future__ import annotations

import pytest


def _tts_mod():
    import importlib

    return importlib.import_module("services.tts_backend")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def _register(monkeypatch, engine_id, *, supports_num_step=True,
              supports_guidance_scale=True, num_step_range=None, seen):
    import torch

    tts = _tts_mod()

    class _FakeEngine(tts.TTSBackend):
        id = engine_id
        display_name = "Fake Engine (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            seen.update(kw)
            return torch.zeros(1, 24000)

    # Class bodies don't close over function locals — set the flags after.
    _FakeEngine.supports_num_step = supports_num_step
    _FakeEngine.supports_guidance_scale = supports_guidance_scale
    _FakeEngine.num_step_range = num_step_range
    monkeypatch.setitem(tts._REGISTRY, engine_id, _FakeEngine)


def test_speech_rejects_explicit_knobs_on_unsupported_engine(client, monkeypatch):
    """Explicit num_step/guidance_scale on an engine without those flags →
    400 naming the parameter (was 200 with silently default audio)."""
    seen: dict = {}
    _register(monkeypatch, "no-knobs-engine", supports_num_step=False,
              supports_guidance_scale=False, seen=seen)

    def _explode(*a, **kw):
        raise AssertionError("evict_other_tts_engines must not run for a doomed request")

    monkeypatch.setattr(
        "services.engine_memory.evict_other_tts_engines", _explode
    )

    res = client.post("/v1/audio/speech", json={
        "model": "no-knobs-engine", "input": "hello",
        "response_format": "wav", "num_step": 32,
    })
    assert res.status_code == 400, res.text
    assert "num_step" in res.json()["detail"]
    assert seen == {}, "rejected before any synthesis work"

    res = client.post("/v1/audio/speech", json={
        "model": "no-knobs-engine", "input": "hello",
        "response_format": "wav", "guidance_scale": 5.0,
    })
    assert res.status_code == 400, res.text
    assert "guidance_scale" in res.json()["detail"]


def test_speech_partial_support_rejects_only_the_unsupported_knob(client, monkeypatch):
    """supertonic3 honors num_step but not guidance_scale; audio.cpp honors
    guidance_scale but not num_step — each rejects only its own gap."""
    seen: dict = {}
    _register(monkeypatch, "partial-steps", supports_num_step=True,
              supports_guidance_scale=False, seen=seen)
    res = client.post("/v1/audio/speech", json={
        "model": "partial-steps", "input": "hello",
        "response_format": "wav", "num_step": 32,
    })
    assert res.status_code == 200, res.text
    assert seen.get("num_step") == 32
    res = client.post("/v1/audio/speech", json={
        "model": "partial-steps", "input": "hello",
        "response_format": "wav", "guidance_scale": 5.0,
    })
    assert res.status_code == 400, res.text
    assert "guidance_scale" in res.json()["detail"]

    seen2: dict = {}
    _register(monkeypatch, "partial-guidance", supports_num_step=False,
              supports_guidance_scale=True, seen=seen2)
    res = client.post("/v1/audio/speech", json={
        "model": "partial-guidance", "input": "hello",
        "response_format": "wav", "guidance_scale": 5.0,
    })
    assert res.status_code == 200, res.text
    res = client.post("/v1/audio/speech", json={
        "model": "partial-guidance", "input": "hello",
        "response_format": "wav", "num_step": 32,
    })
    assert res.status_code == 400, res.text


def test_speech_passes_knobs_to_supporting_engines(client, monkeypatch):
    """Default engines keep accepting (and forwarding) the knobs."""
    seen: dict = {}
    _register(monkeypatch, "knobs-engine", seen=seen)

    res = client.post("/v1/audio/speech", json={
        "model": "knobs-engine", "input": "hello",
        "response_format": "wav", "num_step": 32,
    })
    assert res.status_code == 200, res.text
    assert seen.get("num_step") == 32


def test_speech_omits_knobs_cleanly_on_unsupported_engine(client, monkeypatch):
    """No knobs passed → the request flows to synthesis untouched."""
    seen: dict = {}
    _register(monkeypatch, "no-knobs-quiet", supports_num_step=False,
              supports_guidance_scale=False, seen=seen)

    res = client.post("/v1/audio/speech", json={
        "model": "no-knobs-quiet", "input": "hello",
        "response_format": "wav",
    })
    assert res.status_code == 200, res.text


def test_real_gguf_backend_advertises_no_knob_support():
    """The actual reported engine, not just the fakes."""
    from engines.omnivoice_gguf.backend import _make_backend_class

    cls = _make_backend_class()
    assert cls.supports_num_step is False
    assert cls.supports_guidance_scale is False


def test_base_backend_defaults_keep_everything_else_working():
    """New flags default True — unverified engines behave exactly as before."""
    from services.tts_backend import TTSBackend

    assert TTSBackend.supports_num_step is True
    assert TTSBackend.supports_guidance_scale is True


def test_speech_rejects_out_of_range_steps(client, monkeypatch):
    """A supporting engine with a documented range still 400s an explicit
    value it would silently clamp (supertonic3: 32 would render as 12)."""
    seen: dict = {}
    _register(monkeypatch, "ranged-engine", num_step_range=(5, 12), seen=seen)

    res = client.post("/v1/audio/speech", json={
        "model": "ranged-engine", "input": "hello",
        "response_format": "wav", "num_step": 32,
    })
    assert res.status_code == 400, res.text
    assert "num_step" in res.json()["detail"]
    assert seen == {}, "rejected before any synthesis work"

    res = client.post("/v1/audio/speech", json={
        "model": "ranged-engine", "input": "hello",
        "response_format": "wav", "num_step": 8,
    })
    assert res.status_code == 200, res.text
    assert seen.get("num_step") == 8


def test_real_ranged_engines_advertise_their_bounds():
    from engines.supertonic3.backend import Supertonic3Backend
    from services.tts_backend import VoxCPM2Backend
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    assert Supertonic3Backend.supports_num_step is True
    assert Supertonic3Backend.supports_guidance_scale is False
    assert Supertonic3Backend.num_step_range == (5, 12)
    assert VoxCPM2Backend.num_step_range == (1, 64)
    assert VoxCPM2SubprocessBackend.num_step_range == (1, 64)


def test_real_sidecar_backends_advertise_no_knob_support():
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from engines.moss_tts_nano_subprocess import MossTTSNanoSubprocessBackend

    for cls in (CosyVoiceSubprocessBackend, MossTTSNanoSubprocessBackend):
        assert cls.supports_num_step is False
        assert cls.supports_guidance_scale is False
