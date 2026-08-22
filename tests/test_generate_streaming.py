"""Streaming TTS preview (feat: streaming-tts-preview).

POST /generate with stream=true returns application/x-ndjson events —
"start" → N × "chunk" (base64 PCM16 preview per text chunk) → "done" — while
the classic stream=false path stays byte-identical. These tests prove:

  1. Chunks are yielded INCREMENTALLY: the first chunk's audio reaches the
     client while later chunks are still synthesizing (a mock engine with a
     per-chunk delay + wall-clock bookkeeping).
  2. The final saved WAV is byte-identical to the non-streaming output for
     the same request (same chunking, same per-chunk seeds, same concat /
     effect-chain / watermark / save pipeline).
  3. A mid-stream engine failure yields an "error" event (no "done"), writes
     no history row and saves no file — the client falls back to the classic
     whole-file flow.
  4. Short single-chunk text streams as one chunk through the unchanged
     single-shot pipeline, and the take lands in /history like any other.

Engine layer is stubbed per the test_generate_engine.py idiom.
"""
import base64
import importlib
import json
import os
import sqlite3
import time
import zlib

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
import torch


@pytest.fixture(autouse=True)
def _hermetic_store(tmp_path, monkeypatch):
    """Pin the outputs dir AND the history DB at throwaway paths for every test
    here, so a streamed take is saved and read back through the SAME location
    no matter what ran before.

    Why it's needed: the router SAVES the final WAV through
    ``api.routers.generation.OUTPUTS_DIR`` while these tests READ it back
    through ``core.config.OUTPUTS_DIR``. Those are two separate module
    bindings, and a full-suite run can split them apart — an earlier test that
    reloads ``core.config`` / ``main`` under a tmp data dir (e.g.
    ``test_dub_transcribe``'s ``app_client`` fixture) moves one binding and not
    the other. The save then lands in one dir while the read-back looks in
    another, so the take "vanishes" — the #1088 CI ``FileNotFoundError``.
    Pinning BOTH bindings — plus the DB, through the same
    ``ensure_schema.__globals__`` seam the takes suite uses against the
    #909/#932 module-purge leak — makes each test hermetic and order-independent.
    """
    import core.config as cfg
    import api.routers.generation as gen

    outdir = tmp_path / "outputs"
    outdir.mkdir()
    monkeypatch.setattr(cfg, "OUTPUTS_DIR", str(outdir))
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(outdir))

    dbf = tmp_path / "history.db"

    def _get_db():
        conn = sqlite3.connect(str(dbf))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setitem(gen.ensure_schema.__globals__, "get_db", _get_db)
    gen.ensure_schema()

LONG_TEXT = (
    "The first sentence sets the scene tonight. "
    "A second sentence carries the middle part. "
    "The third sentence wraps everything up now."
)


def _tts_mod():
    """Run-time resolve (see test_generate_engine.py for the rationale)."""
    return importlib.import_module("services.tts_backend")


def _make_deterministic_engine(engine_id="stream-fake", *, delay_s=0.0,
                               fail_on_call=None, fail_message=None):
    """TTSBackend stub whose output is a pure function of the input text, so
    the streamed per-chunk renders and the classic whole-request render can be
    compared bit-for-bit. Optional per-call delay (to observe incrementality)
    and fail-on-Nth-call (to drive the mid-stream error path). ``fail_message``
    overrides the default secret-bearing failure text, e.g. to raise an error
    that classifies to a known failure class (#1607)."""

    class _Engine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Streaming fake engine (test)"
        gpu_compat = ("cpu",)
        calls: list = []  # (text, monotonic_start_time)

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
            type(self).calls.append((text, time.monotonic()))
            if fail_on_call is not None and len(type(self).calls) == fail_on_call:
                raise RuntimeError(
                    fail_message
                    or "TOKEN=stream-secret /home/alice/private-reference.wav"
                )
            if delay_s:
                time.sleep(delay_s)
            # Deterministic, text-dependent waveform (crc-seeded sine-ish ramp).
            amp = 0.2 + (zlib.crc32(text.encode("utf-8")) % 1000) / 2000.0
            n = 4800  # 200 ms @ 24 kHz
            return (torch.linspace(-1.0, 1.0, n) * amp).unsqueeze(0)

    return _Engine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def no_omnivoice_model(monkeypatch):
    async def _boom():
        raise AssertionError("get_model() was called — engine selection ignored")

    import api.routers.generation as gen_mod
    monkeypatch.setattr(gen_mod, "get_model", _boom)


def _stream_events(client, data):
    """POST /generate with stream=true; return [(event_dict, recv_time), ...]."""
    events = []
    with client.stream("POST", "/generate", data={**data, "stream": "true"}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("application/x-ndjson")
        for line in r.iter_lines():
            if line.strip():
                events.append((json.loads(line), time.monotonic()))
    return events


def _saved_wav_bytes(filename):
    from core.config import OUTPUTS_DIR
    with open(os.path.join(OUTPUTS_DIR, filename), "rb") as f:
        return f.read()


async def _asgi_stream_post(path, form_data):
    """POST form data straight at the ASGI app, timestamping every
    http.response.body message as it is SENT — the boundary uvicorn flushes
    per message. (TestClient/httpx's ASGITransport buffer the whole body, so
    they cannot observe incrementality.) Returns [(event_dict, sent_time)]."""
    from urllib.parse import urlencode

    from main import app

    import asyncio

    body = urlencode(form_data).encode()
    sent = []
    request_delivered = False

    async def receive():
        # Deliver the request body once; afterwards BLOCK (never resolve) —
        # StreamingResponse's listen_for_disconnect awaits receive() in a
        # loop, and an always-ready receive() busy-spins the event loop
        # without ever yielding to the response generator (deadlock).
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # cancelled by the app when it finishes

    async def send(msg):
        sent.append((msg, time.monotonic()))

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 3900),
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    await app(scope, receive, send)

    start_msg = sent[0][0]
    assert start_msg["type"] == "http.response.start", sent
    assert start_msg["status"] == 200, sent
    events, buf = [], b""
    for msg, ts in sent[1:]:
        if msg["type"] != "http.response.body":
            continue
        buf += msg.get("body", b"")
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                events.append((json.loads(line), ts))
    return events


def test_stream_yields_chunks_incrementally(monkeypatch, no_omnivoice_model):
    """First chunk audio must be SENT to the client BEFORE the last chunk has
    even started synthesizing — the whole point of the streaming preview."""
    import asyncio

    fake = _make_deterministic_engine(delay_s=0.25)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)

    events = asyncio.run(_asgi_stream_post("/generate", {
        "text": LONG_TEXT, "engine": "stream-fake",
        "seed": "7", "max_chunk_chars": "60", "stream": "true",
    }))
    types = [e["type"] for e, _ in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    n_chunks = types.count("chunk")
    assert n_chunks >= 3, f"expected >=3 text chunks, got {types}"
    assert len(fake.calls) == n_chunks

    # Incrementality: chunk 0 arrived before the LAST chunk's render started.
    first_chunk_recv = next(t for e, t in events if e["type"] == "chunk")
    last_render_start = fake.calls[-1][1]
    assert first_chunk_recv < last_render_start, (
        "first chunk was not delivered until after the last chunk began "
        "rendering — the stream is buffering, not streaming"
    )

    # Chunk payloads are non-empty PCM16 (whole samples).
    for e, _ in events:
        if e["type"] == "chunk":
            pcm = base64.b64decode(e["pcm"])
            assert len(pcm) > 0 and len(pcm) % 2 == 0

    start = events[0][0]
    assert start["sample_rate"] == 24000
    assert start["total_chunks"] == n_chunks
    assert start["seed"] == 7


def test_stream_final_file_identical_to_classic_output(client, monkeypatch,
                                                       no_omnivoice_model):
    """The saved take must be byte-identical whether or not the preview
    streamed — streaming is a delivery channel, not a different render."""
    fake = _make_deterministic_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)
    data = {
        "text": LONG_TEXT, "engine": "stream-fake",
        "seed": "42", "max_chunk_chars": "60",
    }

    classic = client.post("/generate", data=data)
    assert classic.status_code == 200, classic.text
    classic_bytes = _saved_wav_bytes(classic.headers["x-audio-path"])

    events = _stream_events(client, data)
    done = events[-1][0]
    assert done["type"] == "done"
    streamed_bytes = _saved_wav_bytes(done["audio_path"])

    assert streamed_bytes == classic_bytes
    assert done["seed"] == 42
    assert done["duration"] > 0


def test_stream_midstream_error_yields_error_event(
    client, monkeypatch, no_omnivoice_model, caplog
):
    """Chunk 2 blowing up must surface as an in-band error event AFTER the
    already-delivered chunk — no done, no history row, no saved file."""
    fake = _make_deterministic_engine(fail_on_call=2)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)

    before_ids = {h["id"] for h in client.get("/history").json()}
    events = _stream_events(client, {
        "text": LONG_TEXT, "engine": "stream-fake",
        "seed": "7", "max_chunk_chars": "60",
    })
    types = [e["type"] for e, _ in events]
    assert types[0] == "start"
    assert "chunk" in types            # chunk 0 was delivered before the crash
    assert types[-1] == "error"
    assert "done" not in types
    error = events[-1][0]
    assert error["code"] == "generation_failed"
    assert error["detail"] == "Generation failed. Check the selected engine and try again."
    assert "stream-secret" not in repr(error)
    assert "Traceback" not in repr(error)
    assert "stream-secret" not in caplog.text
    assert "/home/alice/private-reference.wav" not in caplog.text

    after_ids = {h["id"] for h in client.get("/history").json()}
    assert after_ids == before_ids     # nothing was recorded for the failure


def test_stream_error_names_recognized_cause_and_journals(
    client, monkeypatch, no_omnivoice_model
):
    """#1607: a streaming request answers 200 and carries its failure as an
    in-band error frame, so it bypasses the global 500 handler. That used to
    mean two regressions vs the classic /generate path: the failure never
    reached the error journal (so it was absent from Diagnostics / the bug
    bundle the maintainer asks reporters for), and the frame carried only the
    opaque "Generation failed. Check the selected engine and try again." even
    when the cause was recognizable. Both are now reproduced in-band."""
    from core import error_journal

    error_journal.clear()

    # A corrupt-weights error classifies to MODEL_CACHE_CORRUPT — one of the
    # classes with a stable, actionable remediation.
    fake = _make_deterministic_engine(
        fail_on_call=1,
        fail_message="model.safetensors: Error while deserializing header: HeaderTooLarge",
    )
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)

    events = _stream_events(client, {"text": "Hello there.", "engine": "stream-fake"})
    error = events[-1][0]
    assert error["type"] == "error"
    assert error["code"] == "generation_failed"
    assert error["retryable"] is True

    # The cause is NAMED, not swallowed into the bare generic string.
    assert error["docs_topic"] == "MODEL_CACHE_CORRUPT"
    assert error["hint"]
    assert error["detail"] != (
        "Generation failed. Check the selected engine and try again."
    )
    assert error["detail"].startswith(
        "Generation failed. Check the selected engine and try again."
    )
    assert error["hint"] in error["detail"]
    # Still no raw exception text copied into the public frame.
    assert "HeaderTooLarge" not in repr(error)

    # The failure now lands in the journal that feeds Diagnostics / the bundle.
    recent = error_journal.recent()
    assert recent, "streaming generation failure must reach the error journal"
    assert any((e.get("route") or "").startswith("/generate") for e in recent)


def test_stream_short_text_single_chunk(client, monkeypatch, no_omnivoice_model):
    """Short text = one chunk through the unchanged single-shot pipeline; the
    take lands in /history exactly like a classic generate."""
    fake = _make_deterministic_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)

    events = _stream_events(client, {"text": "Hello there.", "engine": "stream-fake"})
    types = [e["type"] for e, _ in events]
    assert types == ["start", "chunk", "done"]
    assert events[0][0]["total_chunks"] == 1
    assert len(fake.calls) == 1

    done = events[-1][0]
    ids = {h["id"] for h in client.get("/history").json()}
    assert done["id"] in ids
    assert _saved_wav_bytes(done["audio_path"])  # file exists and is non-empty


def test_stream_execution_timeout_is_not_reported_as_capacity_busy(
    client, monkeypatch, no_omnivoice_model,
):
    """A render that started and expired is not a queue/admission failure."""
    fake = _make_deterministic_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)

    import api.routers.generation as gen_mod

    async def _expired(fn, *, what="GPU job", **kwargs):
        del fn, kwargs
        if what == "TTS generate":
            raise gen_mod.GpuJobTimeoutError("expired after 300 seconds")
        raise AssertionError(f"unexpected guarded job: {what}")

    monkeypatch.setattr(gen_mod, "run_on_gpu_pool_guarded", _expired)
    events = _stream_events(
        client, {"text": "Hello there.", "engine": "stream-fake"}
    )

    assert events[-1][0]["type"] == "error"
    assert events[-1][0]["code"] == "generation_timeout"
    assert events[-1][0]["code"] != "generation_busy"


def test_stream_native_model_path(client, monkeypatch, tmp_path):
    """The native OmniVoice model path streams too (per-chunk generate calls
    with duration=None), and its saved take matches the classic render."""
    from unittest.mock import MagicMock

    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)

    mock_model = MagicMock()
    mock_model.sampling_rate = 24000

    def _gen(**kw):
        amp = 0.2 + (zlib.crc32(kw["text"].encode("utf-8")) % 1000) / 2000.0
        return [(torch.linspace(-1.0, 1.0, 4800) * amp).unsqueeze(0)]

    mock_model.generate.side_effect = lambda **kw: _gen(**kw)

    async def _get():
        return mock_model

    import api.routers.generation as gen_mod
    monkeypatch.setattr(gen_mod, "get_model", _get)

    data = {"text": LONG_TEXT, "seed": "9", "max_chunk_chars": "60"}
    events = _stream_events(client, data)
    types = [e["type"] for e, _ in events]
    n_chunks = types.count("chunk")
    assert n_chunks >= 3 and types[-1] == "done"
    assert mock_model.generate.call_count == n_chunks
    for call in mock_model.generate.call_args_list:
        assert call.kwargs["duration"] is None  # chunk loop contract
    streamed_bytes = _saved_wav_bytes(events[-1][0]["audio_path"])

    mock_model.generate.reset_mock()
    classic = client.post("/generate", data=data)
    assert classic.status_code == 200, classic.text
    assert streamed_bytes == _saved_wav_bytes(classic.headers["x-audio-path"])


def test_classic_generate_unaffected_by_stream_default(client, monkeypatch,
                                                       no_omnivoice_model):
    """stream defaults to false → classic WAV response with the same headers."""
    fake = _make_deterministic_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "stream-fake", fake)
    res = client.post("/generate", data={"text": "Hello.", "engine": "stream-fake"})
    assert res.status_code == 200, res.text
    assert res.headers.get("content-type") == "audio/wav"
    assert res.headers.get("x-audio-id")
