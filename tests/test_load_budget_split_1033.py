"""Model LOAD time must not eat the GENERATE timeout budget (#1033/#1037 class).

Field evidence (#1014, measured on a Tesla T4): a fresh install's first
TTS request spent its entire OMNIVOICE_GENERATE_TIMEOUT_S window (300s)
downloading the multi-GB checkpoint — 0% GPU utilization throughout — and
died with the "too heavy for the available compute" 503. Two user reports
(#1033, #1037) match the signature. The generate guard was wrapping the
adapter's lazy `_ensure_loaded()` (weight download included) together with
the actual synthesis.

The fix gives loading its own, larger budget (OMNIVOICE_MODEL_LOAD_TIMEOUT,
default 1200s) via the new `TTSBackend.ensure_ready()` hook, dispatched
BEFORE the generate clock starts, in both /generate's adapter path and
/v1/audio/speech. These tests drive the class with a fake backend whose
"load" is slower than a tiny generate budget but inside the load budget —
fail-before (GpuJobTimeoutError from the generate guard), pass-after.

Engine-stub pattern from tests/test_agentic_provider_contract.py.
"""
import os
import time

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest
import torch


# generate budget << load time: the load alone would blow the generate clock.
_GENERATE_S = 0.75
_LOAD_S = 1.5


def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _make_slow_loading_engine(engine_id, load_seconds):
    class _SlowLoad(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Slow-Loading Engine (test)"
        load_calls: list = []

        def __init__(self):
            self._loaded = False

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def _ensure_loaded(self):
            if not self._loaded:
                type(self).load_calls.append(time.monotonic())
                time.sleep(load_seconds)  # stands in for the weight download
                self._loaded = True

        def generate(self, text, **kw) -> torch.Tensor:
            self._ensure_loaded()
            return torch.zeros(1, 2400)

    return _SlowLoad


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def test_base_ensure_ready_dispatches_to_lazy_loader():
    eng = _make_slow_loading_engine("slow-hookcheck", 0.01)()
    assert not eng._loaded
    eng.ensure_ready()
    assert eng._loaded


def test_speech_survives_a_load_slower_than_the_generate_budget(client, monkeypatch):
    """The #1033/#1037 class, end to end: load (1.5s) > generate budget
    (0.75s) but < load budget — must succeed. Before the fix the generate
    guard killed the request mid-'download' with the misleading 503.

    Watermarking is disabled for the duration. It runs INSIDE the generate
    budget, and its first call in a process loads the AudioSeal model — which
    on a cold run takes longer than the 0.2s budget this test deliberately
    sets, so the request 503'd on the watermark rather than on anything to do
    with the load/generate split. That made the file pass in a full session
    (AudioSeal already warm from an earlier test) and fail when run alone,
    which is the wrong way round for a regression test: the isolated run is
    the honest one. The budget asymmetry is what is under test; the watermark
    is an unrelated cold start riding in the same window.
    """
    import services.model_manager as mm
    import api.routers.openai_compat as oc
    import services.watermark as wm

    monkeypatch.setattr(wm, "is_enabled", lambda: False)

    fake_cls = _make_slow_loading_engine("slow-load-engine", _LOAD_S)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "slow-load-engine", fake_cls)
    # Pay the one-off cold costs of the generate path (lazy imports of
    # audio_dsp/torchaudio etc. inside _run_tts) BEFORE the tiny budget starts.
    # On a loaded CI runner they exceeded the old 0.2s budget and 503'd with
    # "more than 0s of actual compute time" depending only on which modules
    # shared the xdist worker/shard — a timing flake, not a load/generate bug.
    warm = _make_slow_loading_engine("slow-load-warmup", 0.0)()
    oc._run_tts(warm, "warm", {})
    # Small generate budget; load takes longer than it but fits the (large)
    # load budget — the exact asymmetry that used to be impossible because
    # both ran on one clock. Wide margins on both sides keep it deterministic.
    monkeypatch.setattr(mm, "GPU_JOB_TIMEOUT_S", _GENERATE_S)
    monkeypatch.setattr(oc, "run_on_gpu_pool_guarded", _patched_guard(mm, _GENERATE_S))

    res = client.post("/v1/audio/speech", json={
        "model": "slow-load-engine", "input": "Cold start.", "response_format": "wav",
    })
    assert res.status_code == 200, res.text
    assert res.content[:4] == b"RIFF"
    assert len(fake_cls.load_calls) == 1  # loaded exactly once, under the load budget


def _patched_guard(mm, generate_timeout):
    """run_on_gpu_pool_guarded with the module-default timeout shrunk, but
    explicit `timeout=` (the load-budget call) respected — mirrors how the
    real default flows from GPU_JOB_TIMEOUT_S at call time vs. definition
    time (the module default binds at def, so monkeypatching the constant
    alone doesn't reach it)."""
    real = mm.run_on_gpu_pool_guarded

    async def _guard(fn, *, what="GPU job", timeout=None, executor=None):
        return await real(
            fn, what=what,
            timeout=generate_timeout if timeout is None else timeout,
            executor=executor,
        )
    return _guard


def test_speech_load_exceeding_load_budget_gets_the_load_error(client, monkeypatch):
    """A genuinely stalled download still fails — but with the load-specific
    503 pointing at the engine's Weights list in Model Catalogue, not the 'too heavy for compute' text."""
    import services.model_manager as mm
    import api.routers.openai_compat as oc

    fake_cls = _make_slow_loading_engine("stalled-load-engine", 5.0)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stalled-load-engine", fake_cls)
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.2)

    res = client.post("/v1/audio/speech", json={
        "model": "stalled-load-engine", "input": "Stalled.", "response_format": "wav",
    })
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert "model-load budget" in detail
    assert "the engine's Weights list in Model Catalogue" in detail
    assert "too heavy" not in detail
