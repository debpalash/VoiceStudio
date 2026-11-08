"""Integration test for POST /dub/transcribe/{job_id}.

Covers the full `_transcribe` closure inside `dub_core.py` with a recorded
Whisper output. No GPU, no model, no pyannote — just the real transcription
post-processing + segmentation pipeline exercised through the API.
"""

from __future__ import annotations

import io
import json
import os
import struct
import uuid
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# These tests exercise the transcribe-stream mechanics and assume ASR weights
# are installed — neutralize the no-ASR preflight (its own suite:
# tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(path: Path, seconds: float = 1.0, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """TestClient w/ isolated data dir; seeded fake model + no diarization."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)

    # Force module reloads so core.config rebinds DATA_DIR to the tmp dir.
    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    from api.routers import dub_core as _dc
    importlib.reload(_dc)
    import main as _main
    importlib.reload(_main)

    from fastapi.testclient import TestClient

    fake_model = MagicMock()
    fake_model.sampling_rate = 24000
    fake_model._asr_pipe = MagicMock()  # truthy — not-None passes preflight

    async def _get_model_stub():
        return fake_model

    monkeypatch.setattr(_main, "idle_worker", lambda: _noop_forever())
    monkeypatch.setattr(_dc, "get_model", _get_model_stub)
    monkeypatch.setattr(_dc, "get_diarization_pipeline", lambda: None)

    with TestClient(_main.app) as client:
        yield client, _dc, tmp_path


async def _noop_forever():
    import asyncio
    while True:
        await asyncio.sleep(3600)


def _seed_job(dc_module, tmp_path: Path, duration: float, scene_cuts=None) -> str:
    job_id = f"test_{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / "dub_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    audio_path = job_dir / "audio.wav"
    vocals_path = job_dir / "vocals.wav"
    _make_wav(audio_path, seconds=max(0.5, duration / 8))  # small stub
    _make_wav(vocals_path, seconds=max(0.5, duration / 8))

    dc_module._dub_jobs[job_id] = {
        "video_path": str(job_dir / "original.mp4"),
        "audio_path": str(audio_path),
        "vocals_path": str(vocals_path),
        "no_vocals_path": None,
        "duration": duration,
        "filename": "fixture.mp4",
        "segments": None,
        "dubbed_tracks": {},
        "scene_cuts": scene_cuts or [],
    }
    return job_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_completed_transcription_replays_without_running_asr(tmp_path, monkeypatch):
    """A lost final SSE event must reconnect to the persisted result."""
    import asyncio
    from api.routers import dub_core as dc

    job_id = "t_completed_replay"
    cached = [{
        "id": 0,
        "start": 0.0,
        "end": 1.0,
        "text": "Already transcribed",
        "text_original": "Already transcribed",
        "speaker_id": "Speaker 1",
    }]
    dc._dub_jobs[job_id] = {
        "transcription_complete": True,
        "segments": cached,
        "source_lang": "en",
        "full_transcript": "Already transcribed",
        "cast_sources": {"Speaker 1": {"duration": 1.0}},
    }

    def _unexpected_asr(*_args, **_kwargs):
        raise AssertionError("completed transcription re-entered ASR")

    monkeypatch.setattr(
        "services.asr_backend.load_active_asr_backend",
        _unexpected_asr,
    )

    async def _collect():
        response = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in response.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    assert "event: final" in body, body
    assert "Already transcribed" in body, body
    assert "event: done" in body, body


def test_transcribe_stream_surfaces_model_load_failure(tmp_path, monkeypatch):
    """Regression #255: when the model fails to load, the SSE transcribe stream
    must emit a structured `error` event carrying the real cause — not silently
    drop the connection (the UI renders a dropped stream as a misleading generic
    "Transcribe stream dropped … Likely ASR backend failed to load").

    Scoped to OMNIVOICE_PRELOAD_TTS_ASR, because that is now the only case in
    which transcribe loads the TTS core at all: the preflight used to load it
    unconditionally just to read an `_asr_pipe` that is None unless preloaded, and
    then free it again (see tests/test_dub_no_tts_load_for_asr.py). With preload
    off there is no TTS load on this path, so there is no TTS load failure to
    surface — the ASR load failure, which is the one that can still happen, has
    its own preflight guard and is covered separately.

    Drives the route's async generator directly (no TestClient/lifespan) — the
    preflight-error path yields a single event with no executor/Queue, so it
    stays isolated from the app event loop.
    """
    import asyncio
    from api.routers import dub_core as dc

    job_id = "t_modelfail"
    dc._dub_jobs[job_id] = {"audio_path": str(tmp_path / "a.wav"), "vocals_path": None}

    async def _boom():
        raise RuntimeError("CUDA driver init failed: simulated")

    monkeypatch.setattr(dc, "should_preload_tts_asr", lambda: True)
    monkeypatch.setattr(dc, "get_model", _boom)

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    assert "event: error" in body, body
    assert "CUDA driver init failed: simulated" in body, body


def test_transcribe_stream_never_closes_without_terminal_event(
    tmp_path, monkeypatch, caplog
):
    """Regression #516: an unanticipated exception INSIDE the stream body (one
    that escapes the per-chunk handler, e.g. segmentation blowing up) must still
    end the stream with a terminal `error` then `done` — never a silent
    disconnect (which the UI can only report as "stream dropped, likely ASR
    failed", hiding the real cause)."""
    import asyncio
    import numpy as np
    from api.routers import dub_core as dc

    job_id = "t_bodycrash"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    # Model + ASR backend load fine (preflight passes), so the failure happens
    # mid-body where the terminal-event guard is the only safety net.
    fake_model = MagicMock()
    fake_model._asr_pipe = MagicMock()

    async def _ok_model():
        return fake_model

    class _FakeASR:
        id = "fake"
        def ensure_loaded(self):  # preflight eager-load (no-op for the fake)
            pass
        def transcribe(self, path, *, word_timestamps=True):
            return {"chunks": [{"text": "hi", "timestamp": (0.0, 0.5)}],
                    "segments": [], "language": "en"}
        def unload(self):
            pass

    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend",
        lambda *a, **k: _FakeASR(),
    )
    # Make the post-chunk segmentation (outside the per-chunk try/except) blow
    # up — the exact class of "unanticipated escape" the guard must catch.
    def _boom_segment(*a, **k):
        raise RuntimeError("API_KEY=dub-secret /home/alice/private-video.mp4")
    monkeypatch.setattr(dc, "segment_transcript", _boom_segment)
    # Don't touch the GPU/TTS during the test.
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    # The stream must end with a terminal error followed by done.
    assert "event: error" in body, body
    assert "transcription_failed" in body, body
    assert "Transcription failed. Check the selected ASR engine and try again." in body, body
    assert "dub-secret" not in body, body
    assert "Traceback" not in body, body
    assert "dub-secret" not in caplog.text
    assert "/home/alice/private-video.mp4" not in caplog.text
    err_idx = body.rfind("event: error")
    done_idx = body.rfind("event: done")
    assert done_idx > err_idx >= 0, f"error must precede the terminal done: {body}"


def test_transcribe_chunk_failure_uses_stable_public_metadata(
    tmp_path, monkeypatch, caplog
):
    """Inner ASR failures must not serialize provider secrets or local paths."""
    import asyncio
    from api.routers import dub_core as dc

    job_id = "t_chunk_secret"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    fake_model = MagicMock()
    fake_model._asr_pipe = MagicMock()

    async def _ok_model():
        return fake_model

    class _FailingASR:
        id = "fake"

        def ensure_loaded(self):
            pass

        def transcribe(self, path, *, word_timestamps=True):
            raise RuntimeError("TOKEN=chunk-secret /home/alice/private-audio.wav")

        def unload(self):
            pass

    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(dc, "_CHUNK_TRANSCRIBE_ATTEMPTS", 1)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend",
        lambda *a, **k: _FailingASR(),
    )

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(
                chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            )
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    assert "transcription_failed" in body, body
    assert "Transcription failed. Check the selected ASR engine and try again." in body
    assert "chunk-secret" not in body
    assert "/home/alice/private-audio.wav" not in body
    assert "chunk-secret" not in caplog.text
    assert "/home/alice/private-audio.wav" not in caplog.text


def test_transcribe_chunk_oom_has_one_actionable_public_message(
    tmp_path, monkeypatch
):
    """CUDA OOM must not collapse into a duplicated no-segments error."""
    import asyncio
    import torch
    from api.routers import dub_core as dc

    job_id = "t_chunk_oom"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    class _OOMASR:
        id = "pytorch-whisper"

        def ensure_loaded(self):
            pass

        def transcribe(self, path, *, word_timestamps=True):
            raise torch.OutOfMemoryError("secret diagnostic")

        def unload(self):
            pass

    monkeypatch.setattr(dc, "_CHUNK_TRANSCRIBE_ATTEMPTS", 1)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda: None)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda **_kw: _OOMASR()
    )

    async def _collect():
        response = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in response.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    assert body.count('"code": "transcription_memory"') == 1
    assert "Transcription ran out of GPU memory." in body
    assert "Transcription produced no segments" not in body
    assert "secret diagnostic" not in body


def test_transcribe_stream_surfaces_asr_load_failure_at_preflight(tmp_path, monkeypatch):
    """Regression #578: the reported failure mode is the *ASR model* failing to
    load (WhisperX: faster-whisper weights / CTranslate2-cuDNN mismatch / the
    torch-2.6 weights-only VAD regression), not the TTS model. Because WhisperX
    loads lazily inside ``transcribe()``, that failure used to be buried in N
    per-chunk errors (and the bare error event raced the browser's native
    connection-drop, so the UI showed the misleading generic "stream dropped …
    ASR backend failed to load").

    The preflight now eagerly calls ``backend.ensure_loaded()`` so the *real*
    cause surfaces once, as a structured ``error`` event, ALWAYS followed by a
    terminal ``done`` (so the stream closes via a named event, never a raw
    drop the client can only render generically).
    """
    import asyncio
    from api.routers import dub_core as dc

    job_id = "t_asrload"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    # TTS model loads fine; the *ASR backend* fails to load — the #578 case.
    fake_model = MagicMock()
    fake_model._asr_pipe = None

    async def _ok_model():
        return fake_model

    class _BoomASR:
        id = "whisperx"
        def ensure_loaded(self):
            raise RuntimeError(
                "Could not load library libcudnn_ops_infer.so.8: simulated"
            )
        def transcribe(self, path, *, word_timestamps=True):  # pragma: no cover
            raise AssertionError("transcribe must not run when load fails")
        def unload(self):
            pass

    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend",
        lambda *a, **k: _BoomASR(),
    )

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    # Real cause surfaced as a structured error, not the generic "stream dropped".
    assert "event: error" in body, body
    assert "ASR backend initialization failed" in body, body
    assert "libcudnn_ops_infer.so.8: simulated" in body, body
    assert "stream dropped" not in body, body
    # And it must be followed by a terminal `done` so the client closes via a
    # named event — never a bare error+connection-drop (which races the
    # browser's native EventSource error and loses the real cause).
    err_idx = body.find("event: error")
    done_idx = body.find("event: done")
    assert done_idx > err_idx >= 0, f"error must be followed by terminal done: {body}"


def test_transcribe_stream_preflight_crash_is_a_structured_error(monkeypatch):
    """Regression #1196: the whole preflight used to run in the endpoint body,
    BEFORE the StreamingResponse existed. An exception on any line without its
    own guard (the job-store lookup, the backend-id resolution, the
    `services.asr_backend` import, …) became an HTTP 500 — whose body
    EventSource cannot read — so the UI showed the generic "Transcribe stream
    dropped … likely ASR backend failed to load" guess while a perfectly alive
    backend knew the real cause. The preflight now runs INSIDE the stream, so
    any such crash lands in the terminal-event guard (#516) as a structured
    `error` + `done`.

    `_get_job` stands in for the class: any raise, anywhere in the preflight,
    must reach the client as a structured SSE error — never a non-2xx."""
    import asyncio
    from api.routers import dub_core as dc

    def _boom_get_job(job_id):
        raise RuntimeError("job store exploded: simulated")

    monkeypatch.setattr(dc, "_get_job", _boom_get_job)

    async def _collect():
        resp = await dc.dub_transcribe_stream("t_preflightcrash")
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    # Before the fix this raised straight out of the endpoint coroutine
    # (→ HTTP 500 through the app); it must instead stream a terminal error.
    body = asyncio.run(_collect())

    assert "event: error" in body, body
    assert "transcription_failed" in body, body
    assert "Transcription failed. Check the selected ASR engine and try again." in body, body
    assert "job store exploded: simulated" not in body, body
    assert "Traceback" not in body, body
    err_idx = body.rfind("event: error")
    done_idx = body.rfind("event: done")
    assert done_idx > err_idx >= 0, f"error must precede the terminal done: {body}"


def test_transcribe_stream_sends_bytes_while_asr_loads(tmp_path, monkeypatch):
    """Regression #1196 (silent-load drop class): the old endpoint-body
    preflight sent NOT ONE byte — not even response headers — until the ASR
    backend finished loading. A first-run load downloads multi-GB weights, so
    minutes of byte-silence tripped Chrome's ~5 min no-response timeout (and
    reverse-proxy timeouts in front of Docker installs), severing the stream
    with the generic "stream dropped" message even though the backend was
    healthy and still working.

    The stream must now (a) open with an immediate comment byte before the
    preflight runs, and (b) emit keepalive comments while the load is in
    flight — both invisible to EventSource handlers, so no client changes."""
    import asyncio
    import time
    from api.routers import dub_core as dc

    job_id = "t_slowload"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    fake_model = MagicMock()
    fake_model._asr_pipe = None

    async def _ok_model():
        return fake_model

    def _slow_boom(**_kw):
        time.sleep(0.15)  # long enough for several keepalive intervals below
        raise RuntimeError("weights download interrupted: simulated")

    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(
        "services.asr_backend.load_active_asr_backend", _slow_boom
    )
    monkeypatch.setattr(dc, "ASR_LOAD_KEEPALIVE_S", 0.02)

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return parts

    try:
        parts = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    body = "".join(parts)
    # (a) The very first bytes are the stream-open comment — before any model
    # work. This is what stops the browser/proxy no-response clocks.
    assert parts[0].startswith(": transcribe-stream open"), parts[0]
    # (b) Keepalives flowed during the slow load, before the terminal error.
    assert ": asr-load keepalive" in body, body
    assert body.index(": asr-load keepalive") < body.index("event: error"), body
    # And the slow load's real failure still surfaces as the structured
    # preflight error, followed by the terminal done.
    assert "ASR backend initialization failed" in body, body
    assert "weights download interrupted: simulated" in body, body
    err_idx = body.find("event: error")
    done_idx = body.find("event: done")
    assert done_idx > err_idx >= 0, f"error must be followed by terminal done: {body}"


def test_reset_pool_on_wedge_resets_resilient_pool():
    """#730: a chunk transcribe that times out wedges its GPU-pool worker. The
    chunked stream must abandon the pool so the next chunk / a concurrent TTS
    generate gets a fresh worker instead of starving behind it. dub_core now
    shares asr_backend.reset_pool_after_wedge with the whole-file guards — one
    mechanism, no drift."""
    from api.routers import dub_core as dc

    class _Pool:
        def __init__(self):
            self.resets = 0

        def reset(self):
            self.resets += 1

    pool = _Pool()
    assert dc.reset_pool_after_wedge(pool) is True
    assert pool.resets == 1


def test_reset_pool_on_wedge_is_a_noop_without_reset():
    """A plain executor has no reset(); the helper must no-op, never raise —
    it runs on the failure path it's recovering from."""
    from concurrent.futures import ThreadPoolExecutor

    from api.routers import dub_core as dc

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        assert dc.reset_pool_after_wedge(pool) is False  # must not raise
    finally:
        pool.shutdown(wait=False)


@pytest.mark.parametrize("startup_delay", [0, 0.3])
def test_wedged_chunk_does_not_overlap_a_native_retry(tmp_path, monkeypatch, startup_delay):
    """#1669: a timed-out native transcribe keeps executing in its thread.

    Resetting the pool and immediately retrying entered the same
    whisperx/CTranslate2 backend concurrently; the reporter's log shows that
    sequence immediately before Windows killed the process with 0xC0000005.
    Stop the transcript after one timeout and leave the worker accounted for.
    """
    import asyncio
    import threading
    from concurrent.futures import Executor, ThreadPoolExecutor

    from api.routers import dub_core as dc
    from services import asr_backend

    native_entered = threading.Event()

    class _RecordingPool(Executor):
        """Executor with a #851-style reset(): swap the inner pool, count calls."""

        def __init__(self):
            self.resets = 0
            self._inner = ThreadPoolExecutor(max_workers=1)
            self._pools = [self._inner]
            self.wait_for_native = False

        def submit(self, fn, /, *args, **kwargs):
            def delayed():
                import time
                if self.wait_for_native:
                    time.sleep(startup_delay)
                return fn(*args, **kwargs)
            future = self._inner.submit(delayed)
            if self.wait_for_native:
                # Start the guard's timeout only after the native call is
                # genuinely running. Scheduler delay is not the wedge under test.
                assert native_entered.wait(10), "mock ASR never started"
            return future

        def reset(self):
            self.resets += 1
            old, self._inner = self._inner, ThreadPoolExecutor(max_workers=1)
            self._pools.append(self._inner)
            old.shutdown(wait=False, cancel_futures=True)

        def shutdown(self, wait=True, *, cancel_futures=False):
            for inner in self._pools:
                inner.shutdown(wait=wait, cancel_futures=cancel_futures)

    release_wedge = threading.Event()

    class _WedgedASR:
        id = "whisperx"
        calls = 0

        def ensure_loaded(self):
            pass

        def transcribe(self, path, *, word_timestamps=True):
            type(self).calls += 1
            native_entered.set()
            release_wedge.wait(timeout=30)  # wedge far past the tiny chunk timeout
            return {"chunks": [], "segments": [], "language": "en"}

        def unload(self):
            pass

    job_id = "t_wedge"
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    fake_model = MagicMock()
    fake_model._asr_pipe = MagicMock()

    async def _ok_model():
        return fake_model

    pool = _RecordingPool()
    real_guard = dc.run_transcribe_guarded
    guard_calls = 0

    async def guard_running_call(executor, fn, **kwargs):
        nonlocal guard_calls
        guard_calls += 1
        pool.wait_for_native = True
        return await real_guard(executor, fn, **kwargs)

    monkeypatch.setattr(dc, "run_transcribe_guarded", guard_running_call)
    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(dc, "_gpu_pool", pool)
    monkeypatch.setattr(dc, "TRANSCRIBE_CHUNK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(dc, "_CHUNK_TRANSCRIBE_ATTEMPTS", 2)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend",
        lambda *a, **k: _WedgedASR(),
    )
    # Deterministic streak + recommendation: start at 0, active engine is not
    # already the isolated one (prefs on the dev box must not leak in).
    monkeypatch.setattr(asr_backend, "_timeout_streak", 0)
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "whisperx")

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        release_wedge.set()  # let the wedged worker threads exit
        pool.shutdown()
        dc._dub_jobs.pop(job_id, None)

    assert guard_calls == 1, "a timed-out native call must not be retried"
    assert pool.resets == 0, "an in-process native call cannot be killed by swapping pools"
    assert _WedgedASR.calls == 1, "the timed-out native call must not overlap a retry"
    # The user-facing chunk error is the guard's actionable message …
    assert "backend is running" in body, body
    assert "OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S" in body, body
    # … not the old parallel mechanism's dead-end advice.
    assert "Try restarting the server" not in body, body
    # Terminal error followed by done — stream still closes via named events.
    err_idx = body.rfind("event: error")
    done_idx = body.rfind("event: done")
    assert done_idx > err_idx >= 0, body


@pytest.mark.xfail(
    reason="dub_core._transcribe was refactored to route through "
           "services.asr_backend.get_active_asr_backend; the MagicMock fixture "
           "no longer satisfies the new bytes-path contract. Re-enable after "
           "updating mocks to the new backend interface.",
    strict=False,
)
class TestTranscribeRoute:
    def test_screenshot_regression_consolidates_fragments(self, app_client):
        """18 garbled Whisper chunks → clean segments, no mid-word stubs."""
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=18.0)

        with patch("mlx_whisper.transcribe", return_value=_load_fixture("whisper_screenshot.json")), \
             patch("torch.backends.mps.is_available", return_value=True):
            res = client.post(f"/dub/transcribe/{job_id}")

        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["job_id"] == job_id
        assert payload["source_lang"] == "en"

        segs = payload["segments"]
        assert 1 < len(segs) < 8, f"expected consolidation, got {len(segs)}"

        # No fragment survives past the floor (except possibly the trailing one).
        from services.segmentation import MIN_DUR, MIN_CHARS
        for s in segs[:-1]:
            assert (s["end"] - s["start"]) >= MIN_DUR
            assert len(s["text"]) >= MIN_CHARS

        # The original bug was that "stru", "c", "tured" were their OWN rows in
        # the segments table. Assert none of those appear as standalone segments.
        for frag in ("stru", "c", "tured", "ge", "The AI", "Then you"):
            assert frag not in [s["text"].strip() for s in segs], (
                f"{frag!r} leaked as a standalone segment"
            )

        # Every segment ends on a real word boundary.
        for s in segs:
            assert s["text"].strip(), "empty text"
            last = s["text"].rstrip()[-1]
            assert last.isalnum() or last in ".,!?;:'\")", f"trailing char {last!r}"

    def test_clean_input_preserves_sentence_structure(self, app_client):
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=14.0)

        with patch("mlx_whisper.transcribe", return_value=_load_fixture("whisper_clean.json")), \
             patch("torch.backends.mps.is_available", return_value=True):
            res = client.post(f"/dub/transcribe/{job_id}")

        assert res.status_code == 200, res.text
        segs = res.json()["segments"]
        # Every seg ends with sentence terminator (clean-input property).
        for s in segs:
            assert s["text"].rstrip().endswith((".", "!", "?"))

    def test_heuristic_speaker_assignment_without_diarization(self, app_client):
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=18.0)

        with patch("mlx_whisper.transcribe", return_value=_load_fixture("whisper_screenshot.json")), \
             patch("torch.backends.mps.is_available", return_value=True):
            res = client.post(f"/dub/transcribe/{job_id}")

        segs = res.json()["segments"]
        for s in segs:
            assert s["speaker_id"].startswith("Speaker ")

    def test_missing_job_returns_404(self, app_client):
        client, _, _ = app_client
        res = client.post("/dub/transcribe/does_not_exist")
        assert res.status_code == 404

    def test_source_lang_detected_and_persisted(self, app_client):
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=18.0)

        fixture = _load_fixture("whisper_screenshot.json")
        fixture["language"] = "es_ES"  # simulate Whisper dialect output

        with patch("mlx_whisper.transcribe", return_value=fixture), \
             patch("torch.backends.mps.is_available", return_value=True):
            res = client.post(f"/dub/transcribe/{job_id}")

        assert res.status_code == 200
        assert res.json()["source_lang"] == "es"
        # In-memory job was updated.
        assert dc._dub_jobs[job_id]["source_lang"] == "es"

    def test_selected_source_lang_overrides_detection(self, app_client):
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=18.0)
        dc._dub_jobs[job_id]["source_lang_override"] = "fr"
        fixture = _load_fixture("whisper_screenshot.json")
        fixture["language"] = "es_ES"

        with patch("mlx_whisper.transcribe", return_value=fixture), patch(
            "torch.backends.mps.is_available", return_value=True
        ):
            res = client.post(f"/dub/transcribe/{job_id}")

        assert res.status_code == 200
        assert res.json()["source_lang"] == "fr"
        assert dc._dub_jobs[job_id]["source_lang"] == "fr"

    def test_scene_cuts_applied_when_viable(self, app_client):
        client, dc, tmp = app_client
        job_id = _seed_job(dc, tmp, duration=14.0, scene_cuts=[5.5])

        with patch("mlx_whisper.transcribe", return_value=_load_fixture("whisper_clean.json")), \
             patch("torch.backends.mps.is_available", return_value=True):
            res = client.post(f"/dub/transcribe/{job_id}")

        segs = res.json()["segments"]
        # At least one segment boundary should land at/near the scene cut.
        near_cut = [s for s in segs if abs(s["end"] - 5.5) < 0.2 or abs(s["start"] - 5.5) < 0.2]
        assert near_cut, f"no segment boundary near scene cut 5.5; got {[(s['start'], s['end']) for s in segs]}"


def test_stream_pings_while_reference_text_refinement_runs(tmp_path, monkeypatch):
    """The quiet stretch after the segments event must not be byte-silent."""
    import asyncio
    import time
    from concurrent.futures import ThreadPoolExecutor
    from api.routers import dub_core as dc
    from services import asr_backend, speaker_clone

    class Backend:
        id = "fake"
        def ensure_loaded(self): pass
        def transcribe(self, *a, **kw):
            return {"chunks": [{"text": "Hello there.", "timestamp": (0.0, 0.8)}],
                    "segments": [], "language": "en"}
        def unload(self): pass

    async def model():
        result = MagicMock()
        result._asr_pipe = MagicMock()
        return result

    def fake_refs(vocals, segs, out_dir, seg_ids=None):
        return {str(i): {"ref_audio": "x.wav", "ref_text": "raw"} for i in (seg_ids or [])}

    def slow_refine(clones, backend):
        time.sleep(0.4)
        for entry in clones.values():
            entry["ref_text"] = "refined"
        return clones

    monkeypatch.setattr(dc, "get_model", model)
    monkeypatch.setattr(asr_backend, "get_active_asr_backend", lambda *a, **kw: Backend())
    monkeypatch.setattr(dc, "get_diarization_pipeline", lambda **kw: (None, None))
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda: None)
    monkeypatch.setattr(dc, "restore_tts_after_asr", lambda: None)
    monkeypatch.setattr(speaker_clone, "extract_segment_refs", fake_refs)
    monkeypatch.setattr(speaker_clone, "refine_ref_texts", slow_refine)
    monkeypatch.setattr(dc, "POST_ASR_PING_S", 0.02)

    audio = tmp_path / "ping.wav"
    _make_wav(audio, seconds=1.0)
    job_id = "ping_refine"
    dc._dub_jobs[job_id] = {"audio_path": str(audio), "vocals_path": None, "scene_cuts": []}

    async def collect():
        response = await dc.dub_transcribe_stream(job_id)
        out = []
        async for chunk in response.body_iterator:
            out.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(out)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            monkeypatch.setattr(dc, "_gpu_pool", pool)
            monkeypatch.setattr(dc, "_cpu_pool", pool)
            body = asyncio.run(asyncio.wait_for(collect(), timeout=20))
    finally:
        dc._dub_jobs.pop(job_id, None)

    last_segments = body.rindex("event: segments")
    final = body.index("event: final")
    assert "event: ping" in body[last_segments:final], body
    assert body.index("event: done") > final


@pytest.fixture
def fast_pings(monkeypatch):
    from api.routers import dub_core as dc

    monkeypatch.setattr(dc, "POST_ASR_PING_S", 0.01)
    return dc


def test_closing_ping_stream_early_propagates_cancel_to_the_work(fast_pings):
    dc = fast_pings
    record = []

    async def scenario():
        async def long_job():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                record.append("interrupted")
                raise

        job = asyncio.ensure_future(long_job())
        pings = dc._ping_while(job)
        assert bytes(await pings.__anext__()).startswith(b"event: ping")
        await pings.aclose()
        done, _ = await asyncio.wait({job}, timeout=2)
        return job in done and job.cancelled()

    import asyncio
    assert asyncio.run(scenario()) is True
    assert record == ["interrupted"]


def test_ping_stream_over_completed_future_is_empty_and_leaves_result(fast_pings):
    import asyncio

    async def scenario():
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"answer": 42})
        emitted = []
        async for item in fast_pings._ping_while(fut):
            emitted.append(item)
        return emitted, fut.result()

    assert asyncio.run(scenario()) == ([], {"answer": 42})


def test_error_surfacing_after_consumer_left_is_not_reported_as_unretrieved(fast_pings):
    import asyncio
    import gc

    complaints = []

    async def scenario():
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, ctx: complaints.append(str(ctx.get("message")))
        )

        async def stubborn():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise ValueError("raised while shutting down")

        job = asyncio.ensure_future(stubborn())
        stream = fast_pings._ping_while(job)
        await stream.__anext__()
        await stream.aclose()
        await asyncio.sleep(0.05)
        assert job.done()
        del job, stream
        gc.collect()
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert not any("never retrieved" in c for c in complaints), complaints


def test_stream_cleanup_waits_for_native_work_and_rejects_late_work():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers.dub_core import _ASRWorkLifetime
    lifetime = _ASRWorkLifetime()
    started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    cleanup_started = threading.Event()
    events = []
    def native():
        started.set()
        assert release.wait(5)
        events.append("native finished")
    def cleanup():
        events.append("unloaded")
        cleaned.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(lifetime.run, native)
        assert started.wait(5)
        lifetime.stop()
        def remove():
            cleanup_started.set()
            lifetime.cleanup(cleanup)
        removal = pool.submit(remove)
        try:
            assert cleanup_started.wait(5)
            assert not cleaned.is_set()
        finally:
            release.set()
        work.result(timeout=5)
        removal.result(timeout=5)
    assert events == ["native finished", "unloaded"]
    with pytest.raises(RuntimeError, match="stream has ended"):
        lifetime.run(lambda: pytest.fail("late work accessed unloaded model"))


def test_stream_unload_is_single_shot_during_disconnect():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers.dub_core import _ASRWorkLifetime
    lifetime = _ASRWorkLifetime()
    started, release = threading.Event(), threading.Event()
    calls = []
    def unload():
        calls.append("unload")
        assert len(calls) == 1
        started.set()
        assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        normal = pool.submit(lifetime.cleanup, unload)
        assert started.wait(5)
        lifetime.stop()
        disconnected = pool.submit(lifetime.cleanup, unload)
        release.set()
        normal.result(timeout=5)
        disconnected.result(timeout=5)
    assert calls == ["unload"]


def test_disconnect_during_diarization_waits_before_unload_and_restore(tmp_path, monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers import dub_core as dc
    from services import asr_backend

    started, release, cleanup_started, restored = [threading.Event() for _ in range(4)]
    events, guarded = [], []
    original_cleanup = dc._ASRWorkLifetime.cleanup
    def cleanup(lifetime, fn):
        guarded.append(lifetime._lock.locked())
        cleanup_started.set()
        return original_cleanup(lifetime, fn)
    monkeypatch.setattr(dc._ASRWorkLifetime, "cleanup", cleanup)
    def diarize(**kwargs):
        started.set()
        assert release.wait(5)
        events.append("diarization finished")
        return None, None
    class Backend:
        id = "fake"
        def ensure_loaded(self): pass
        def transcribe(self, *a, **kw):
            return {"chunks": [{"text": "hi", "timestamp": (0., .5)}], "segments": [], "language": "en"}
        def unload(self): events.append("unloaded")
    async def model():
        result = MagicMock()
        result._asr_pipe = MagicMock()
        return result
    def restore():
        events.append("restored")
        restored.set()
    monkeypatch.setattr(dc, "get_model", model)
    monkeypatch.setattr(asr_backend, "get_active_asr_backend", lambda *a, **kw: Backend())
    monkeypatch.setattr(dc, "get_diarization_pipeline", diarize)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda: None)
    monkeypatch.setattr(dc, "restore_tts_after_asr", restore)
    monkeypatch.setattr(dc, "POST_ASR_PING_S", .01)
    audio = tmp_path / "diar.wav"
    _make_wav(audio, seconds=1.)
    job_id = "diar_disconnect"
    dc._dub_jobs[job_id] = {"audio_path": str(audio), "vocals_path": None, "scene_cuts": []}
    async def scenario():
        response = await dc.dub_transcribe_stream(job_id)
        try:
            async for _ in response.body_iterator:
                if started.is_set():
                    break
            await response.body_iterator.aclose()
            assert await asyncio.to_thread(cleanup_started.wait, 5)
            assert guarded == [True]
            assert not restored.is_set()
        finally:
            release.set()
            await response.body_iterator.aclose()
            assert await asyncio.to_thread(restored.wait, 5)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            monkeypatch.setattr(dc, "_gpu_pool", pool)
            asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    finally:
        dc._dub_jobs.pop(job_id, None)
    assert events == ["diarization finished", "unloaded", "restored"]
