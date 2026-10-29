"""Text-normalization coverage for the three remaining TTS entry points.

Sibling of tests/test_text_normalization.py (which pins the pass itself and
the /generate + audiobook integrations). These routes hand text to
`backend.generate` directly — none funnels through /generate or the dub /
audiobook call sites — so each needs its own wiring, pinned here with the
same applied-EXACTLY-once spy + toggle-off contract:

  - POST /v1/audio/speech (OpenAI-compatible API) — normalized once, with the
    request's `language`, before the generate dispatch.
  - WS /ws/tts (streaming TTS) — normalized once on the WHOLE request text,
    BEFORE the sentence chunker fans it out (multi-sentence requests must not
    re-normalize per sentence).
  - batch dub queue — normalized once per segment inside `_gen`, with the
    job's target language (same shape as dub_generate's `_gen`).

Fake-engine/client harness from tests/test_text_normalization.py; the batch
pipeline harness is the hermetic stub set from
tests/test_dub_batch_engine_selection.py.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import importlib
import json

import pytest
import torch

from services import text_normalization


def _tts_mod():
    """Resolve services.tts_backend at RUN time (same rationale as
    test_generate_engine.py — collection-time bindings can go stale)."""
    return importlib.import_module("services.tts_backend")


def _make_fake_engine():
    class _FakeEngine(_tts_mod().TTSBackend):
        id = "fake-norm-route"
        display_name = "Fake Norm Route Engine (test)"
        supports_cloning = True
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

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 24000)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def fake_engine(monkeypatch):
    """Register a fresh fake engine in the REAL registry; reset the MM2-01
    active-backend cache so batch's resolve_generation_backend re-resolves."""
    tb = _tts_mod()
    tb.reset_active_backend()
    fake = _make_fake_engine()
    monkeypatch.setitem(tb._REGISTRY, "fake-norm-route", fake)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    yield fake
    tb.reset_active_backend()


@pytest.fixture()
def norm_spy(monkeypatch):
    """Count normalize_for_tts calls (patched on the module object the routes
    import per-request) while keeping the real behavior."""
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    norm_mod = importlib.import_module("services.text_normalization")
    calls = []
    real = norm_mod.normalize_for_tts

    def spy(text, language=None, mode="auto"):
        calls.append((text, language))
        return real(text, language, mode=mode)

    monkeypatch.setattr(norm_mod, "normalize_for_tts", spy)
    return calls


# ── POST /v1/audio/speech (OpenAI-compatible API) ────────────────────────────


def test_openai_speech_applies_normalization_exactly_once(client, fake_engine, norm_spy):
    res = client.post("/v1/audio/speech", json={
        "model": "fake-norm-route", "input": "Dr. Smith has 2 cats",
        "language": "en", "response_format": "wav",
    })
    assert res.status_code == 200, res.text
    assert len(norm_spy) == 1  # exactly once, at the choke point
    assert norm_spy[0] == ("Dr. Smith has 2 cats", "en")
    assert len(fake_engine.calls) == 1
    assert fake_engine.calls[0][0] == "Doctor Smith has two cats"


def test_openai_speech_toggle_off_sends_raw_text(client, fake_engine, monkeypatch):
    monkeypatch.setenv(text_normalization.ENV_VAR, "0")
    res = client.post("/v1/audio/speech", json={
        "model": "fake-norm-route", "input": "Dr. Smith has 2 cats",
        "language": "en", "response_format": "wav",
    })
    assert res.status_code == 200, res.text
    assert fake_engine.calls[-1][0] == "Dr. Smith has 2 cats"


# ── WS /ws/tts (streaming TTS) ───────────────────────────────────────────────


def _run_ws_request(client, payload):
    """Send one /ws/tts request; drain frames until done/error. Returns the
    JSON frames (binary PCM chunks are skipped)."""
    frames = []
    with client.websocket_connect("/ws/tts") as ws:
        ws.send_json(payload)
        while True:
            msg = ws.receive()
            text = msg.get("text")
            if text is None:
                continue  # binary PCM chunk
            frame = json.loads(text)
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                return frames


def test_ws_tts_applies_normalization_exactly_once(client, fake_engine, norm_spy):
    # Two sentences: the chunker fans the request out into per-sentence
    # generates, but normalization must run ONCE, on the whole text, before
    # the split — never once per sentence.
    frames = _run_ws_request(client, {
        "text": "Dr. Smith has 2 cats. He is 40.",
        "language": "en", "engine": "fake-norm-route",
    })
    assert frames[-1]["type"] == "done", frames
    assert len(norm_spy) == 1
    assert norm_spy[0] == ("Dr. Smith has 2 cats. He is 40.", "en")
    assert fake_engine.calls, "engine never ran"
    joined = " ".join(t.strip() for t, _ in fake_engine.calls)
    assert joined == "Doctor Smith has two cats. He is forty."


def test_ws_tts_toggle_off_sends_raw_text(client, fake_engine, monkeypatch):
    monkeypatch.setenv(text_normalization.ENV_VAR, "0")
    frames = _run_ws_request(client, {
        "text": "Dr. Smith has 2 cats",
        "language": "en", "engine": "fake-norm-route",
    })
    assert frames[-1]["type"] == "done", frames
    assert fake_engine.calls[-1][0] == "Dr. Smith has 2 cats"


def test_ws_tts_reports_true_first_audio_latency(client, fake_engine, monkeypatch):
    """TTFA ends at the first binary chunk, not when the whole render ends."""
    import api.routers.tts_stream as stream
    from services import watermark

    # t0, synth start, synth end, first audio byte, finish.
    # Synthesis takes 0.20s; the remaining 0.30s of wall clock is delivery.
    ticks = iter((100.0, 100.0, 100.20, 100.125, 100.5))
    monkeypatch.setattr(stream, "_perf_counter", lambda: next(ticks), raising=False)
    monkeypatch.setattr(watermark, "mark_synthetic", lambda wav, _sr, **_kw: wav)

    frames = _run_ws_request(client, {
        "text": "One sentence.",
        "language": "en",
        "engine": "fake-norm-route",
    })
    done = frames[-1]

    assert done["type"] == "done", frames
    assert done["ttfa_ms"] == pytest.approx(125.0)
    assert done["gen_time_s"] == pytest.approx(0.5)   # end-to-end, incl. delivery
    assert done["duration_s"] == pytest.approx(1.0)
    # RTF is a RENDER metric: 0.20s of synthesis per 1.0s of audio. Deriving it
    # from the 0.5s wall clock would report 0.5 and blame the engine for a slow
    # consumer (#1620 review).
    assert done["rtf"] == pytest.approx(0.2)


# ── Batch dub queue ──────────────────────────────────────────────────────────


@pytest.fixture()
def batch_env(monkeypatch, tmp_path):
    """Hermetic _run_batch_pipeline harness — the stub set from
    tests/test_dub_batch_engine_selection.py, with a transcript segment whose
    text exercises the normalizer."""
    import api.routers.batch as b

    monkeypatch.setattr(b, "DATA_DIR", str(tmp_path))

    async def _fake_run_transcribe_guarded(pool, fn, what=None):
        return (
            [{"id": "s0", "start": 0.0, "end": 1.0,
              "text": "Dr. Smith has 2 cats",
              "text_original": "Dr. Smith has 2 cats"}],
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

    def _make_job(job_id):
        return {
            "id": job_id,
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

    return b, _make_job


def test_batch_applies_normalization_exactly_once(
    batch_env, fake_engine, norm_spy, monkeypatch,
):
    b, make_job = batch_env
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-norm-route")

    job = make_job("jobN1")
    asyncio.run(b._run_batch_pipeline("jobN1", job))

    assert "en" in job.get("outputs", {})
    assert len(norm_spy) == 1  # one segment → exactly one pass
    assert norm_spy[0] == ("Dr. Smith has 2 cats", "en")
    assert len(fake_engine.calls) == 1
    assert fake_engine.calls[0][0] == "Doctor Smith has two cats"


def test_batch_toggle_off_sends_raw_text(batch_env, fake_engine, monkeypatch):
    b, make_job = batch_env
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-norm-route")
    monkeypatch.setenv(text_normalization.ENV_VAR, "0")

    job = make_job("jobN2")
    asyncio.run(b._run_batch_pipeline("jobN2", job))

    assert "en" in job.get("outputs", {})
    assert fake_engine.calls[-1][0] == "Dr. Smith has 2 cats"


def test_batch_uses_native_tts_batches(batch_env, monkeypatch):
    """Batch dubbing sends eight-segment chunks to a native adapter once."""
    b, make_job = batch_env
    tb = _tts_mod()
    batch_calls = []

    class _NativeBatchEngine(tb.TTSBackend):
        id = "fake-native-batch"
        display_name = "Fake Native Batch Engine"
        supports_cloning = True
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):  # pragma: no cover - fallback proof
            # Raising alone is not proof: batch.py's _gen catches Exception
            # and substitutes silence + a job warning, so the test also
            # asserts no warnings below — the raise turning into a warning
            # is exactly the fallback evidence being checked for.
            raise AssertionError("native batch should not use single-item generate")

        def generate_batch(self, texts, **kw):
            batch_calls.append((list(texts), kw))
            return [torch.zeros(1, 24000) for _ in texts]

    monkeypatch.setitem(tb._REGISTRY, "fake-native-batch", _NativeBatchEngine)
    tb.reset_active_backend()
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-native-batch")
    # Pin the width: it is derived from the host's device headroom (#1620
    # review — an unconditional 8 would OOM 4-8 GB cards), so leaving it to
    # detection makes this assertion depend on the machine running the suite.
    # The derivation itself is covered by test_dub_batch_width_and_budget.py.
    monkeypatch.setenv(b.BATCH_WIDTH_ENV, "8")
    monkeypatch.setattr(
        "services.watermark.mark_synthetic",
        lambda wav, _sr, **_kw: wav,
    )

    async def _fake_transcribe_guarded(pool, fn, what=None):
        return (
            [
                {"id": f"s{i:05x}", "start": float(i), "end": float(i + 1),
                 "text": f"line {i}", "text_original": f"line {i}"}
                for i in range(10)
            ],
            "en",
        )

    monkeypatch.setattr(
        "services.asr_backend.run_transcribe_guarded",
        _fake_transcribe_guarded,
    )

    job = make_job("job-native-batch")
    asyncio.run(b._run_batch_pipeline("job-native-batch", job))

    assert [len(texts) for texts, _ in batch_calls] == [8, 2]  # 10 segments, width 8
    assert all(kw["duration"] == [1.0] * len(texts) for texts, kw in batch_calls)
    # The raise in generate() is swallowed by _gen's fallback handler into a
    # job warning + silence — so the absence of warnings is what actually
    # proves no segment fell back to single-item generation.
    assert not job.get("warnings"), job.get("warnings")
