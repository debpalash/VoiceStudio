"""Performance regression budgets — operation counts, not wall-clock.

Implements the ROADMAP quality-track item "Perf regression budget (≤5 %)".
CI hardware varies (GitHub runners, contributor laptops, CUDA vs MPS vs CPU),
so wall-clock budgets on real models would flake; the hardware-independent
equivalent is pinning HOW MANY expensive operations a hot path performs:

  - WS /ws/tts        — exactly one engine `generate` per sentence, one
                        normalization pass per request (never per sentence).
  - dub re-mix        — a fit-only re-mix (regen_only=[]) of cached segments
                        performs ZERO TTS calls; once the natural-rate fast
                        path lands (fix/perf-dub-cache-batching), also zero
                        `torchaudio.load` / `atomic_save_wav` in the re-mix
                        loop (one assembly decode per segment is the floor).
  - batch native TTS  — one native batch of width W renders ceil(N/W)
                        `generate_batch` calls and zero per-segment
                        `generate` calls (fix/perf-dub-cache-batching).

A counter budget fails on ANY regression (stricter than 5 %) and cancels out
host speed. The guards deliberately count expensive work rather than timing
filesystem and audio-assembly work, whose latency is runner-dependent.

Updating a budget is a DELIBERATE act: if a change legitimately adds an
operation to a guarded path (e.g. a new required decode), update the expected
count here in the same PR, with a comment justifying the new floor — never
loosen a budget to "make CI pass". See docs/performance.md §Performance
budgets.

Tests for seams that only exist on fix/perf-dub-cache-batching skip with a
clear reason until that branch merges; they were validated against it.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import importlib
import json

import pytest
import torch



SR = 24000


def _tts_mod():
    """Resolve services.tts_backend at RUN time (same rationale as
    test_generate_engine.py — collection-time bindings can go stale)."""
    return importlib.import_module("services.tts_backend")


# ── WS /ws/tts — one generate per sentence, one normalize per request ────────


def _make_fake_stream_engine():
    class _FakeEngine(_tts_mod().TTSBackend):
        id = "fake-perf-stream"
        display_name = "Fake Perf Stream Engine (test)"
        supports_cloning = True
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return SR

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append(text)
            return torch.zeros(1, 2400)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def fake_stream_engine(monkeypatch):
    tb = _tts_mod()
    tb.reset_active_backend()
    fake = _make_fake_stream_engine()
    fake.calls = []
    monkeypatch.setitem(tb._REGISTRY, "fake-perf-stream", fake)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    yield fake
    tb.reset_active_backend()


def test_ws_tts_budget_one_generate_per_sentence(client, fake_stream_engine, monkeypatch):
    """Budget: a K-sentence request = exactly K engine generates + exactly 1
    normalization pass. A regression that re-normalizes per sentence, or
    synthesizes a sentence twice (retry loop, duplicated dispatch), doubles
    real synthesis time on every streaming request — this fails on the first
    extra call, long before it would show up as a 5% wall-clock drift."""
    from services import text_normalization
    from services.sentence_chunker import SentenceChunker

    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    norm_mod = importlib.import_module("services.text_normalization")
    norm_calls = []
    real_norm = norm_mod.normalize_for_tts

    def norm_spy(text, language=None, mode="auto"):
        norm_calls.append((text, language))
        return real_norm(text, language, mode=mode)

    monkeypatch.setattr(norm_mod, "normalize_for_tts", norm_spy)

    text = "The cat sat on the mat. The dog ran away. The bird flew home."
    # The budget's expected value, derived from the same chunker the route
    # uses: one generate per sentence of the normalized text.
    chunker = SentenceChunker(language="en")
    expected = chunker.push(real_norm(text, "en"))
    expected.extend(chunker.flush())
    # The chunker may coalesce trailing sentences on flush; the budget is
    # "one generate per chunk it yields", so only require a multi-chunk
    # request (a single chunk couldn't distinguish per-chunk from per-request).
    assert len(expected) >= 2, f"test text must fan out to 2+ chunks: {expected}"

    with client.websocket_connect("/ws/tts") as ws:
        ws.send_json({"text": text, "language": "en", "engine": "fake-perf-stream"})
        frames = []
        while True:
            msg = ws.receive()
            payload = msg.get("text")
            if payload is None:
                continue  # binary PCM chunk
            frame = json.loads(payload)
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                break

    assert frames[-1]["type"] == "done", frames
    assert len(fake_stream_engine.calls) == len(expected), (
        f"budget: exactly one generate per sentence "
        f"({len(expected)} sentences, {len(fake_stream_engine.calls)} generates)"
    )
    assert len(norm_calls) == 1, (
        f"budget: normalization runs once per request, before the sentence "
        f"split — got {len(norm_calls)} calls"
    )


# ── Dub re-mix — cached segments must not re-synthesize / re-decode ──────────


class _FakeDubModel:
    """Deterministic 'TTS engine' (same protocol as test_smart_fit_generate):
    the text encodes its own natural duration as a `<seconds>:` prefix.
    ``delay_s`` simulates real synthesis cost for the ratio test."""

    sampling_rate = SR

    def __init__(self, delay_s: float = 0.0):
        self.calls: list[str] = []
        self.batch_calls: list[list[str]] = []
        self.use_native_batch = False
        self.delay_s = delay_s

    def generate(self, text=None, **kwargs):
        self.calls.append(text)
        if self.delay_s:
            time.sleep(self.delay_s)
        dur = float(text.split(":", 1)[0])
        return [torch.full((1, int(dur * SR)), 0.25)]


class _FakeDubBackend(_tts_mod().TTSBackend):
    id = "fake-perf-dub"
    display_name = "Fake Perf Dub Engine"
    supports_cloning = True
    gpu_compat = ("cpu",)
    applies_own_mastering = False

    def __init__(self, model):
        self._model = model

    @property
    def sample_rate(self):
        return self._model.sampling_rate

    @property
    def supported_languages(self):
        return ["multi"]

    @classmethod
    def is_available(cls):
        return True, "ready"

    def generate(self, *a, **kw):
        return self._model.generate(*a, **kw)[0]


class _FakeNativeDubBackend(_FakeDubBackend):
    def generate_batch(self, texts, **kwargs):
        self._model.batch_calls.append(list(texts))
        return [
            torch.full((1, int(float(text.split(":", 1)[0]) * SR)), 0.25)
            for text in texts
        ]


@pytest.fixture
def dub_harness(monkeypatch, tmp_path):
    """Hermetic dub_generate harness — the stub set from
    test_smart_fit_generate.py: fake backend, no DB, no ffmpeg, WAVs under
    tmp_path. Text normalization is toggled off so the fake model's
    `<seconds>:` text protocol survives untouched (normalization budgets are
    pinned by the WS test above and test_text_normalization_routes.py)."""
    import api.routers.dub_generate as dg
    from services import text_normalization

    monkeypatch.setenv(text_normalization.ENV_VAR, "0")

    model = _FakeDubModel()

    async def _fake_resolve_generation_backend(**kwargs):
        backend_type = _FakeNativeDubBackend if model.use_native_batch else _FakeDubBackend
        return backend_type(model)

    job = {"duration": 4.0, "dubbed_tracks": {}, "speaker_clones": {}}
    job_dir = tmp_path / "jobP"
    job_dir.mkdir()

    monkeypatch.setattr(dg, "resolve_generation_backend", _fake_resolve_generation_backend)
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

    events: list[str] = []

    class _StubTaskManager:
        def is_cancelled(self, task_id):
            return False

        async def add_task(self, task_id, task_type, func, *args, **kwargs):
            async for evt in func(*args):
                events.append(evt)

    monkeypatch.setattr(dg, "task_manager", _StubTaskManager())

    def run(body: dict) -> list[dict]:
        from schemas.requests import DubRequest

        events.clear()
        req = DubRequest(**body)
        asyncio.run(dg.dub_generate("jobP", req))
        parsed = []
        for e in events:
            line = e.strip()
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: "):]))
        return parsed

    return run, model, job, job_dir


def _dub_body(segments, **extra):
    return {
        "segments": segments,
        "segment_ids": [str(i) for i in range(len(segments))],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        "timing_strategy": "concise",
        **extra,
    }


def _assert_done(parsed):
    assert any(p.get("type") == "done" for p in parsed), f"no done event in {parsed}"


_SEGS_3 = [
    {"start": 0.0, "end": 1.0, "text": "0.5:hola"},
    {"start": 1.2, "end": 2.2, "text": "0.5:mundo"},
    {"start": 2.4, "end": 3.4, "text": "0.5:adios"},
]


@pytest.mark.usefixtures("torch_dtype_isolation")
def test_dub_remix_budget_zero_tts_calls(dub_harness):
    """Budget: a fit-only re-mix (regen_only=[]) of fully cached segments
    performs ZERO engine generate calls. This is the contract every
    incremental-dub feature rides on (Phase 4.1's 70x speedup): a regression
    that quietly re-synthesizes even one cached segment turns a seconds
    re-mix back into a minutes re-render."""
    run, model, job, job_dir = dub_harness

    _assert_done(run(_dub_body(_SEGS_3)))
    assert len(model.calls) == 3, "fresh run synthesizes each segment once"

    _assert_done(run(_dub_body(_SEGS_3, regen_only=[])))
    assert len(model.calls) == 3, (
        f"budget: re-mix of cached segments makes zero TTS calls — "
        f"{len(model.calls) - 3} extra generate(s) detected"
    )


@pytest.mark.usefixtures("torch_dtype_isolation")
def test_dub_remix_budget_zero_decode_zero_rewrite(dub_harness, monkeypatch):
    """Budget (natural-rate fast path): a re-mix of N cached same-rate
    segments performs ZERO `torchaudio.load` and ZERO `atomic_save_wav`
    calls in the re-mix loop — the cached WAV path goes straight into the
    mix manifest and is decoded exactly once, by the final assembly. The
    old path decoded each cache, wrote an identical mix_<id> scratch WAV,
    then decoded that copy again (3 decodes + 1 write per segment)."""
    import api.routers.dub_generate as dg
    import torchaudio

    if not hasattr(dg, "_cached_payload_intact"):
        pytest.skip(
            "natural-rate cached fast path (_cached_payload_intact) not "
            "merged yet — lands with fix/perf-dub-cache-batching"
        )

    run, model, job, job_dir = dub_harness
    _assert_done(run(_dub_body(_SEGS_3)))

    load_calls: list[str] = []
    real_load = torchaudio.load

    def counting_load(path, *a, **kw):
        load_calls.append(str(path))
        return real_load(path, *a, **kw)

    monkeypatch.setattr(torchaudio, "load", counting_load)

    save_calls: list[str] = []
    real_save = dg.atomic_save_wav

    def counting_save(path, *a, **kw):
        save_calls.append(str(path))
        return real_save(path, *a, **kw)

    monkeypatch.setattr(dg, "atomic_save_wav", counting_save)

    _assert_done(run(_dub_body(_SEGS_3, regen_only=[])))

    assert len(model.calls) == 3, "re-mix must not re-synthesize"
    # Floor: the final assembly decodes each cached segment exactly once.
    assert len(load_calls) == 3, (
        f"budget: re-mix of 3 cached same-rate segments = exactly 3 decodes "
        f"(assembly only) — got {len(load_calls)}: {load_calls}"
    )
    assert len(save_calls) == 0, (
        f"budget: re-mix writes zero per-segment WAVs (no mix_<id> scratch "
        f"copies) — got {len(save_calls)}: {save_calls}"
    )


@pytest.mark.usefixtures("torch_dtype_isolation")
def test_interactive_dub_uses_native_batches(dub_harness, monkeypatch):
    """Four fresh rows at width two cost two native forwards and no serial ones."""
    run, model, _job, _job_dir = dub_harness
    model.use_native_batch = True
    monkeypatch.setenv("OMNIVOICE_DUB_BATCH_WIDTH", "2")
    segments = [
        {"start": i * 1.0, "end": i * 1.0 + 0.8, "text": "0.5:line"}
        for i in range(4)
    ]

    _assert_done(run(_dub_body(segments)))

    assert len(model.batch_calls) == 2
    assert model.calls == []


# ── Batch dub — native batches amortize, never duplicate ─────────────────────


def _make_fake_batch_engine():
    tb = _tts_mod()

    class _FakeBatchEngine(tb.TTSBackend):
        id = "fake-perf-batch"
        display_name = "Fake Perf Batch Engine (test)"
        supports_cloning = True
        gpu_compat = ("cpu",)
        generate_calls: list = []
        batch_calls: list = []

        @property
        def sample_rate(self) -> int:
            return SR

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).generate_calls.append(text)
            return torch.zeros(1, SR)

        def generate_batch(self, texts, **kw):  # native batch seam
            type(self).batch_calls.append(list(texts))
            durations = kw.get("duration") or [1.0] * len(texts)
            return [
                torch.zeros(1, max(1, int(float(d) * SR))) for d in durations
            ]

    return _FakeBatchEngine


def test_batch_budget_exact_native_batch_calls(monkeypatch, tmp_path):
    """Budget: with native batching and width W, N renderable segments cost
    exactly ceil(N/W) `generate_batch` calls and ZERO per-segment `generate`
    calls. Fails if the loop ever renders a segment both ways (paying the
    forward pass twice), re-renders a batch, or silently falls back to the
    per-segment path (N engine dispatches instead of N/W)."""
    import api.routers.batch as b

    if not hasattr(b, "_native_batch_width"):
        pytest.skip(
            "lazy native batch rendering (_native_batch_width/_prefetch_batch) "
            "not merged yet — lands with fix/perf-dub-cache-batching"
        )

    tb = _tts_mod()
    tb.reset_active_backend()
    fake = _make_fake_batch_engine()
    fake.generate_calls = []
    fake.batch_calls = []
    monkeypatch.setitem(tb._REGISTRY, "fake-perf-batch", fake)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-perf-batch")
    # Pin the width: 4 segments / width 2 = exactly 2 native batches.
    monkeypatch.setenv(b.BATCH_WIDTH_ENV, "2")

    monkeypatch.setattr(b, "DATA_DIR", str(tmp_path))

    async def _fake_run_transcribe_guarded(pool, fn, what=None):
        return (
            [
                {"id": f"s{i}", "start": float(i), "end": float(i) + 0.9,
                 "text": f"segment {i}", "text_original": f"segment {i}"}
                for i in range(4)
            ],
            "en",
        )

    monkeypatch.setattr(
        "services.asr_backend.run_transcribe_guarded",
        _fake_run_transcribe_guarded,
    )

    def _fake_subprocess_run(cmd, *a, **kw):
        class _Result:
            stdout = b""
            stderr = b"Duration: 00:00:06.00, start: 0.000000, bitrate: 1000 kb/s\n"

        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_subprocess_run)
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")

    job = {
        "id": "jobPerfBatch",
        "status": "running",
        "filename": "in.mp4",
        "video_path": str(tmp_path / "in.mp4"),
        "langs": ["en"],  # == source_lang → translation stage is a no-op
        "voice_id": None,
        "preserve_bg": True,
        "created_at": 0.0,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
    }
    try:
        asyncio.run(b._run_batch_pipeline("jobPerfBatch", job))
    finally:
        tb.reset_active_backend()

    assert len(fake.batch_calls) == 2, (
        f"budget: 4 segments at width 2 = exactly 2 generate_batch calls — "
        f"got {len(fake.batch_calls)}: {[len(c) for c in fake.batch_calls]}"
    )
    assert all(len(c) == 2 for c in fake.batch_calls), (
        f"budget: every native batch carries exactly the width — "
        f"got widths {[len(c) for c in fake.batch_calls]}"
    )
    assert len(fake.generate_calls) == 0, (
        f"budget: zero per-segment generates when the native batch path "
        f"covers all segments — got {len(fake.generate_calls)}"
    )
