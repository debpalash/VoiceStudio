"""A busy engine never becomes a missing or silent piece of an assembled output.

A native PCM stream can hold the shared engine, so a buffered generation raises
``GpuPoolBusyError`` at once. Every caller that assembles an artifact (audiobook
chapter, dub segment, batch segment) waits through ``await_free_engine`` inside
its own timeout budget, off the GPU worker: it either succeeds once the engine
frees up or the job fails clearly. Interactive single requests keep the route's
503/429 + Retry-After convention. No models / GPU: every engine is a fake.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
import torch

from schemas.requests import DubRequest, DubSegment


def _busy():
    from services.model_manager import GpuPoolBusyError
    return GpuPoolBusyError("busy", retry_after=1)


@pytest.fixture
def fast_sleep(monkeypatch):
    real = asyncio.sleep
    slept = []

    async def sleep(delay, *a, **k):
        slept.append(delay)
        await real(0.005)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return slept


# ── shared helper ──────────────────────────────────────────────────────────


def test_helper_retries_until_free(fast_sleep):
    from services.model_manager import await_free_engine
    calls = []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise _busy()
        return "ok"

    assert asyncio.run(await_free_engine(call, budget_s=30)) == "ok"
    assert len(calls) == 3 and len(fast_sleep) == 2


def test_helper_raises_busy_past_budget(fast_sleep):
    from services.model_manager import GpuPoolBusyError, await_free_engine

    async def call():
        raise _busy()

    with pytest.raises(GpuPoolBusyError):
        asyncio.run(await_free_engine(call, budget_s=0))


def test_helper_stops_when_cancelled(fast_sleep):
    from services.model_manager import GpuPoolBusyError, await_free_engine
    state = {"cancelled": False, "calls": 0}

    async def call():
        state["calls"] += 1
        state["cancelled"] = True
        raise _busy()

    with pytest.raises(GpuPoolBusyError):
        asyncio.run(await_free_engine(call, budget_s=30,
                                      is_cancelled=lambda: state["cancelled"]))
    assert state["calls"] == 1


def test_helper_does_not_swallow_other_errors(fast_sleep):
    from services.model_manager import await_free_engine

    async def call():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(await_free_engine(call, budget_s=30))


# ── audiobook ──────────────────────────────────────────────────────────────


def _resolve(_voice_id):
    return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}


def _plan(*titles):
    from services.audiobook import AudiobookPlan, Chapter, Span
    return AudiobookPlan(chapters=[
        Chapter(title=t, spans=[Span(voice_id=None, text=f"text {t}")]) for t in titles
    ])


def _stub_book(monkeypatch, tmp_path, synth):
    from api.routers import audiobook

    def factory(default_voice=None, language=None, opts=None, voice_map=None, lease=None):
        return {"mode": "generic", "resolve": _resolve, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}

    monkeypatch.setattr(audiobook, "_build_synth", factory)
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "/usr/bin/true")
    return audiobook


def _book_events(monkeypatch, tmp_path, plan, synth, *, budget=None):
    from services import model_manager

    audiobook = _stub_book(monkeypatch, tmp_path, synth)
    if budget is not None:
        monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *a, **k: budget)

    async def run():
        out = []
        gen = audiobook._render_longform_sse(plan, default_voice=None)
        try:
            async for frame in gen:
                ev = json.loads(frame[len("data:"):].strip())
                out.append(ev)
                if ev["type"] in ("assembling", "error"):
                    break
        finally:
            await gen.aclose()
        return out

    return asyncio.run(run())


def test_audiobook_busy_then_free_renders_every_chapter(tmp_path, monkeypatch, fast_sleep):
    calls = {"n": 0}

    def synth(text, voice_id, speed=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _busy()
        return torch.zeros(2400)

    events = _book_events(monkeypatch, tmp_path, _plan("One", "Two"), synth)
    types = [e["type"] for e in events]
    assert types.count("chapter") == 2
    assert "chapter_error" not in types and types[-1] == "assembling"
    assert fast_sleep, "the busy engine must be waited out, not failed"


def test_audiobook_busy_past_budget_fails_without_a_partial_book(tmp_path, monkeypatch, fast_sleep):
    def synth(text, voice_id, speed=None):
        raise _busy()

    events = _book_events(monkeypatch, tmp_path, _plan("One", "Two"), synth, budget=0.05)
    types = [e["type"] for e in events]
    assert types.count("chapter_error") == 1
    assert types[-1] == "error"
    assert "chapter" not in types and "assembling" not in types
    assert "busy" in events[-1]["reason"]


def test_audiobook_cancel_during_wait_is_cancelled(tmp_path, monkeypatch):
    state = {"calls": 0}

    def synth(text, voice_id, speed=None):
        state["calls"] += 1
        raise _busy()

    audiobook = _stub_book(monkeypatch, tmp_path, synth)
    real = asyncio.sleep

    async def run():
        waiting = asyncio.Event()

        async def sleep(delay, *a, **k):
            waiting.set()
            await real(3600)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        seen = []

        async def consume():
            async for frame in audiobook._render_longform_sse(_plan("One"), default_voice=None):
                seen.append(json.loads(frame[len("data:"):].strip())["type"])

        task = asyncio.ensure_future(consume())
        await asyncio.wait_for(waiting.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return seen

    seen = asyncio.run(run())
    assert state["calls"] == 1
    assert "done" not in seen and "assembling" not in seen


# ── dub ────────────────────────────────────────────────────────────────────


@pytest.fixture
def dub_env(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg

    tb = importlib.import_module("services.tts_backend")
    tb.reset_active_backend()
    job = {"duration": 2.0, "dubbed_tracks": {}, "speaker_clones": {}}
    job_dir = tmp_path / "jobX"
    job_dir.mkdir()
    monkeypatch.setattr(dg, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dg, "_save_job", lambda job_id, j: None)
    monkeypatch.setattr(dg, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dg, "dub_seg_path",
                        lambda job_id, seg_id: str(job_dir / f"seg_{seg_id}.wav"))
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(dg, "mark_synthetic", lambda wav, sr, **kw: wav)
    monkeypatch.setattr(dg, "apply_mastering", lambda a, sample_rate=None: a)
    monkeypatch.setattr(dg, "get_effect_chain", lambda preset: None)
    monkeypatch.setattr(dg, "apply_effects_chain", lambda a, **k: a)
    monkeypatch.setattr(dg, "normalize_audio", lambda a, target_dBFS=None: a)

    class _Engine(tb.TTSBackend):
        id = "fake-busy"
        display_name = "Fake busy"
        supports_cloning = True
        gpu_compat = ("cpu",)
        calls = 0
        busy_times = 0

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).calls += 1
            if type(self).calls <= type(self).busy_times:
                raise _busy()
            return torch.full((1, 24000), 0.25)

    tb._REGISTRY["fake-busy"] = _Engine
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-busy")
    yield dg, job, _Engine
    tb.reset_active_backend()
    tb._REGISTRY.pop("fake-busy", None)


def _dub_events(dg, monkeypatch, *, cancelled=lambda: False):
    events = []

    class _TM:
        def is_cancelled(self, _id):
            return cancelled()

        async def add_task(self, _id, _type, func, *args, **_kw):
            async for e in func(*args):
                events.append(e)

    monkeypatch.setattr(dg, "task_manager", _TM())
    req = DubRequest(segments=[DubSegment(start=0.0, end=1.0, text="hola")],
                     segment_ids=["0"], language="Auto", language_code="es", num_step=4)
    asyncio.run(dg.dub_generate("jobX", req))
    return "".join(events)


def test_dub_segment_waits_out_a_busy_engine(dub_env, monkeypatch, fast_sleep):
    dg, job, engine = dub_env
    engine.busy_times = 2
    body = _dub_events(dg, monkeypatch)
    assert '"type": "error"' not in body
    assert "es" in job["dubbed_tracks"]
    assert engine.calls == 3 and len(fast_sleep) == 2


def test_dub_busy_past_budget_fails_the_job(dub_env, monkeypatch, fast_sleep):
    from services import model_manager
    dg, job, engine = dub_env
    engine.busy_times = 10**6
    monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *a, **k: 0.05)
    body = _dub_events(dg, monkeypatch)
    assert '"type": "error"' in body
    assert job["dubbed_tracks"] == {}


def test_dub_cancel_during_wait_is_cancelled(dub_env, monkeypatch, fast_sleep):
    dg, job, engine = dub_env
    engine.busy_times = 10**6
    body = _dub_events(dg, monkeypatch, cancelled=lambda: engine.calls >= 1)
    assert '"type": "cancelled"' in body and '"type": "error"' not in body
    assert engine.calls == 1
