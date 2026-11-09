"""Issue #312 class — dub generation and batch TTS must honor the active
engine selection (Model Catalogue) instead of silently falling back to
OmniVoice via services.model_manager.get_model(), and must refuse — with an
actionable error naming alternatives — instead of mis-cloning when the
active engine can't do reference-audio voice cloning.

Covers:
  - `cloning_capable_engine_ids()` excludes the fixed-preset-voice engines
    (kittentts, supertonic3, sherpa-onnx) and includes the cloning ones.
  - /dub/generate: a non-cloning active engine fails the whole job with one
    actionable message (never falls back to OmniVoice, never mis-clones
    per segment).
  - /dub/generate: a cloning-capable non-OmniVoice active engine actually
    runs the request (proves the engine selection is honored, not ignored).
  - batch: an unpinned voice_id runs fine on a non-cloning active engine;
    a pinned voice_id on the same engine fails fast, before any TTS runs.
  - `applies_own_mastering` still skips the shared mastering chain for both
    pipelines (mirrors test_generate_engine.py's coverage of the same knob
    for /generate).
"""
from __future__ import annotations

import asyncio
import importlib
import os

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
import torch
from fastapi import HTTPException

from schemas.requests import DubRequest, DubSegment


def _tts_mod():
    """Resolve services.tts_backend at RUN time — see test_generate_engine.py's
    docstring for why (sys.modules pre-pollution across the collected suite)."""
    return importlib.import_module("services.tts_backend")


def _make_fake_engine(engine_id, *, supports_cloning=True, available=True,
                       own_mastering=False, gpu_compat=("cpu",)):
    tb = _tts_mod()
    # Class-body assignment can't read the same name from the enclosing
    # function scope (class bodies don't close over locals) — alias first,
    # matching test_generate_engine.py's _make_fake_engine convention.
    _cloning, _mastering, _compat = supports_cloning, own_mastering, gpu_compat

    class _FakeEngine(tb.TTSBackend):
        id = engine_id
        display_name = f"Fake {engine_id} (test)"
        supports_cloning = _cloning
        applies_own_mastering = _mastering
        gpu_compat = _compat
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            if available:
                return True, "ready"
            return False, "fake engine deliberately unavailable (test)"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.full((1, 24000), 0.25)

    return _FakeEngine


@pytest.fixture
def fake_registry(monkeypatch):
    """Register a fake engine in the REAL registry so resolve_generation_backend
    exercises the actual lookup/is_available/routing/cloning chain, not a stub.
    Resets the MM2-01 active-backend cache before/after (see
    tests/test_mm2_lifecycle.py's convention) so one test's cached instance
    can't leak into the next."""
    tb = _tts_mod()
    tb.reset_active_backend()
    registered: list[str] = []

    def _register(engine_id, **kw):
        cls = _make_fake_engine(engine_id, **kw)
        tb._REGISTRY[engine_id] = cls
        registered.append(engine_id)
        return cls

    yield _register

    tb.reset_active_backend()
    for engine_id in registered:
        tb._REGISTRY.pop(engine_id, None)


@pytest.fixture
def no_omnivoice_model_manager(monkeypatch):
    """Fail loudly if resolution falls back to OmniVoice's get_model() path."""
    import services.model_manager as mm

    async def _boom():
        raise AssertionError(
            "services.model_manager.get_model() was called — engine "
            "selection was silently ignored (#312 class)"
        )

    monkeypatch.setattr(mm, "get_model", _boom)


# ── cloning_capable_engine_ids() ────────────────────────────────────────────


def test_cloning_capable_engine_ids_excludes_fixed_voice_engines():
    tb = _tts_mod()
    ids = set(tb.cloning_capable_engine_ids())
    assert ids.isdisjoint({"kittentts", "supertonic3", "sherpa-onnx"})
    assert {"omnivoice", "voxcpm2", "cosyvoice", "gpt-sovits"}.issubset(ids)


def test_cloning_capable_engine_ids_excludes_model_dependent_adapters():
    # MLXAudioBackend.supports_cloning is an instance @property (only some of
    # its curated models can clone) — a class-level getattr() returns the
    # property descriptor itself, which is truthy, so a naive check would
    # always recommend "mlx-audio" even when the configured model is Kokoro
    # (can't clone). Must be excluded from the suggestion list rather than
    # falsely recommended.
    tb = _tts_mod()
    assert isinstance(
        vars(tb.MLXAudioBackend).get("supports_cloning"), property
    ), "this test assumes MLXAudioBackend.supports_cloning is a property"
    assert "mlx-audio" not in set(tb.cloning_capable_engine_ids())


# ── /dub/generate/{job_id} ──────────────────────────────────────────────────


@pytest.fixture
def dub_job_env(monkeypatch, tmp_path):
    """Minimal hermetic environment for `dg.dub_generate()` — same stub set as
    test_smart_fit_generate.py's fixture, but WITHOUT patching
    resolve_generation_backend, so the real registry + capability gate run."""
    import api.routers.dub_generate as dg

    job = {"duration": 2.0, "dubbed_tracks": {}, "speaker_clones": {}}
    job_dir = tmp_path / "jobX"
    job_dir.mkdir()

    monkeypatch.setattr(dg, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dg, "_save_job", lambda job_id, j: None)
    monkeypatch.setattr(dg, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(
        dg, "dub_seg_path",
        lambda job_id, seg_id: str(job_dir / f"seg_{seg_id}.wav"),
    )
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(dg, "mark_synthetic", lambda wav, sr, **kw: wav)
    monkeypatch.setattr(dg, "apply_mastering", lambda a, sample_rate=None: a)
    monkeypatch.setattr(dg, "get_effect_chain", lambda preset: None)
    monkeypatch.setattr(dg, "apply_effects_chain", lambda a, **k: a)
    monkeypatch.setattr(dg, "normalize_audio", lambda a, target_dBFS=None: a)

    class _StubTaskManager:
        def is_cancelled(self, task_id):
            return False

        async def add_task(self, task_id, task_type, func, *args, **kwargs):
            async for _ in func(*args):
                pass

    monkeypatch.setattr(dg, "task_manager", _StubTaskManager())
    return dg, job


def _one_seg_request():
    return DubRequest(
        segments=[DubSegment(start=0.0, end=1.0, text="hola")],
        segment_ids=["0"], language="Auto", language_code="es", num_step=4,
    )


def test_dub_generate_fails_fast_for_non_cloning_engine(
    dub_job_env, fake_registry, no_omnivoice_model_manager, monkeypatch,
):
    """Active engine can't clone → the job fails once, up front, with an
    actionable message naming alternatives — never a silent OmniVoice run."""
    dg, job = dub_job_env
    fake_registry("fake-nonclone", supports_cloning=False)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-nonclone")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dg.dub_generate("jobX", _one_seg_request()))

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert "fake-nonclone" in detail
    assert "voice cloning" in detail
    assert "omnivoice" in detail  # names a real alternative


def test_dub_generate_model_load_oom_is_a_sanitized_resource_error(
    dub_job_env, monkeypatch,
):
    dg, _job = dub_job_env
    private = (
        "CUDA out of memory. Tried to allocate 1.14 GiB. "
        "Process 1031664 has 22.02 GiB memory in use. "
        "/home/alice/private/model.safetensors"
    )

    async def _oom(**_kwargs):
        raise RuntimeError(private)

    monkeypatch.setattr(dg, "resolve_generation_backend", _oom)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dg.dub_generate("jobX", _one_seg_request()))

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert "Close other GPU-heavy apps" in detail
    assert "1031664" not in detail
    assert "/home/alice" not in detail


def test_dub_generate_retry_oom_stream_is_sanitized(
    dub_job_env, fake_registry, monkeypatch,
):
    dg, _job = dub_job_env
    fake = fake_registry("fake-oom", supports_cloning=True)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-oom")
    private = (
        "CUDA out of memory. Process 1031664 has 22.02 GiB in use. "
        "/home/alice/private/model.safetensors"
    )
    calls = []

    def _oom(self, text, **kwargs):
        calls.append((text, kwargs))
        raise RuntimeError(private)

    monkeypatch.setattr(fake, "generate", _oom)
    events = []

    class _CaptureTaskManager:
        def is_cancelled(self, _task_id):
            return False

        async def add_task(self, _task_id, _task_type, func, *args, **_kwargs):
            async for event in func(*args):
                events.append(event)

    monkeypatch.setattr(dg, "task_manager", _CaptureTaskManager())
    asyncio.run(dg.dub_generate("jobX", _one_seg_request()))

    body = "".join(events)
    assert len(calls) == 2, "the initial generation and low-step retry must both run"
    assert "Close other GPU-heavy apps" in body
    assert "1031664" not in body
    assert "/home/alice" not in body


def test_dub_generate_uses_selected_cloning_engine_not_omnivoice(
    dub_job_env, fake_registry, no_omnivoice_model_manager, monkeypatch,
):
    """A cloning-capable non-OmniVoice engine actually runs the segment."""
    dg, job = dub_job_env
    fake = fake_registry("fake-clone", supports_cloning=True)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-clone")

    asyncio.run(dg.dub_generate("jobX", _one_seg_request()))

    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "hola"
    assert "es" in job["dubbed_tracks"]


def test_dub_generate_respects_applies_own_mastering(
    dub_job_env, fake_registry, no_omnivoice_model_manager, monkeypatch,
):
    dg, job = dub_job_env
    fake = fake_registry("fake-studio", supports_cloning=True, own_mastering=True)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-studio")

    mastering_calls = []
    monkeypatch.setattr(
        dg, "apply_mastering",
        lambda a, sample_rate=None: mastering_calls.append(1) or a,
    )

    asyncio.run(dg.dub_generate("jobX", _one_seg_request()))

    assert len(fake.calls) == 1
    assert mastering_calls == []  # studio engine's own mastering is not double-applied


# ── batch ────────────────────────────────────────────────────────────────


@pytest.fixture
def batch_job_env(monkeypatch, tmp_path):
    import api.routers.batch as b

    monkeypatch.setattr(b, "DATA_DIR", str(tmp_path))

    async def _fake_run_transcribe_guarded(pool, fn, what=None):
        # Bypass real ASR entirely — the engine-selection gate under test
        # runs right after transcription, before translate/generate.
        return (
            [{"id": "s0", "start": 0.0, "end": 1.0, "text": "hola",
              "text_original": "hola"}],
            "en",
        )

    monkeypatch.setattr(
        "services.asr_backend.run_transcribe_guarded",
        _fake_run_transcribe_guarded,
    )

    def _fake_subprocess_run(cmd, *a, **kw):
        class _Result:
            stdout = b""
            stderr = b"Duration: 00:00:02.00, start: 0.000000, bitrate: 1000 kb/s\n"

        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_subprocess_run)
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")

    def _make_job(job_id, *, voice_id=None):
        return {
            "id": job_id,
            "status": "running",
            "filename": "in.mp4",
            "video_path": str(tmp_path / "in.mp4"),
            "langs": ["en"],  # == source_lang → translation stage is a no-op
            "voice_id": voice_id,
            "preserve_bg": True,
            "created_at": 0.0,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "progress": None,
        }

    return b, _make_job


def test_batch_unpinned_voice_succeeds_on_noncloning_engine(
    batch_job_env, fake_registry, no_omnivoice_model_manager, monkeypatch,
):
    """No voice_id pinned → any active engine (cloning-capable or not) is fine."""
    b, make_job = batch_job_env
    fake = fake_registry("fake-batch-nonclone", supports_cloning=False)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-batch-nonclone")

    job = make_job("jobA", voice_id=None)
    asyncio.run(b._run_batch_pipeline("jobA", job))

    assert len(fake.calls) == 1
    assert "en" in job.get("outputs", {})


def test_batch_pinned_voice_fails_fast_on_noncloning_engine(
    batch_job_env, fake_registry, monkeypatch,
):
    """voice_id pinned + a non-cloning active engine → fail before any TTS
    runs, with the same actionable message shape as the dub gate."""
    b, make_job = batch_job_env
    fake = fake_registry("fake-batch-nonclone2", supports_cloning=False)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-batch-nonclone2")
    monkeypatch.setattr(
        b,
        "_batch_voice",
        lambda _voice_id: {
            "ref_audio": "reference.wav",
            "ref_text": "reference",
            "instruct": "",
            "seed": None,
            "requires_cloning": True,
        },
    )

    job = make_job("jobB", voice_id="some-voice-id")
    with pytest.raises(ValueError) as exc_info:
        asyncio.run(b._run_batch_pipeline("jobB", job))

    detail = str(exc_info.value)
    assert "fake-batch-nonclone2" in detail
    assert "voice cloning" in detail
    assert not fake.calls  # never reached generate


def test_batch_respects_applies_own_mastering(
    batch_job_env, fake_registry, no_omnivoice_model_manager, monkeypatch,
):
    b, make_job = batch_job_env
    fake = fake_registry("fake-batch-studio", supports_cloning=True, own_mastering=True)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-batch-studio")

    mastering_calls = []
    import services.audio_dsp as audio_dsp
    monkeypatch.setattr(
        audio_dsp, "apply_mastering",
        lambda a, sample_rate=None: mastering_calls.append(1) or a,
    )

    job = make_job("jobC", voice_id=None)
    asyncio.run(b._run_batch_pipeline("jobC", job))

    assert len(fake.calls) == 1
    assert mastering_calls == []
