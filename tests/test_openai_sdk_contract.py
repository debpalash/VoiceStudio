"""The official ``openai`` SDK — and the OpenAI Agents SDK voice pipeline built
on it — must work against VoiceStudio's /v1 routes unmodified.

Drives the routes through the real SDK: its HTTP client uses a transport
that hands each request to Starlette's TestClient, so what is tested is the
exact wire format the SDK produces (multipart ``timestamp_granularities[]``,
``extra_body`` fields, streaming reads, error-body parsing).

Fail-before coverage (each was broken before this file existed):
- ``gpt-4o-mini-tts`` (the Agents SDK default) returned 400 "Unknown model";
- OpenAI ``instructions`` was silently dropped;
- ``pcm`` was the engine's native rate instead of OpenAI's 24 kHz;
- ``aac`` returned a WAV body; ``opus`` returned Vorbis; a missing encoder
  returned WAV labelled as the requested format;
- GET /v1/models, /v1/audio/translations did not exist;
- transcription ``language``/``prompt``/``temperature`` never reached the
  engine, ``verbose_json`` had no ``words``, and language defaulted to "en";
- errors were FastAPI ``{"detail"}`` bodies (SDK showed a raw dict) and
  validation failures were 422 instead of OpenAI's 400.
"""
from __future__ import annotations

import importlib
import io
import struct
import wave

import openai
from openai._base_client import httpx as sdk_httpx
import pytest
import torch
from fastapi.testclient import TestClient

ENGINE_RATE = 48000  # deliberately not 24 kHz: pcm must be resampled


def _tts_mod():
    return importlib.import_module("services.tts_backend")


@pytest.fixture()
def engine(monkeypatch):
    class _Engine(_tts_mod().TTSBackend):
        id = "sdk-contract-engine"
        display_name = "SDK contract engine (test)"
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return ENGINE_RATE

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).calls.append((text, kw))
            # 0.1 s of a quiet tone at 48 kHz.
            return (torch.sin(torch.arange(ENGINE_RATE // 10) * 0.05) * 0.1).unsqueeze(0)

    _Engine.calls = []
    inst = _Engine()
    tts = _tts_mod()
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

    tc = TestClient(app, client=("127.0.0.1", 50000))

    def _forward(request: sdk_httpx.Request) -> sdk_httpx.Response:
        res = tc.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=request.read(),
        )
        return sdk_httpx.Response(res.status_code, headers=res.headers.multi_items(), content=res.content)

    return openai.OpenAI(
        base_url="http://testserver/v1",
        api_key="not-needed",
        max_retries=0,
        http_client=sdk_httpx.Client(transport=sdk_httpx.MockTransport(_forward)),
    )


# ── Speech ──────────────────────────────────────────────────────────────────


def _is_mp3(b: bytes) -> bool:
    return b[:3] == b"ID3" or (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0)


_FORMAT_CHECKS = {
    "mp3": ("audio/mpeg", _is_mp3),
    "opus": ("audio/ogg", lambda b: b[:4] == b"OggS" and b"OpusHead" in b[:64]),
    "aac": ("audio/aac", lambda b: b[0] == 0xFF and (b[1] & 0xF6) == 0xF0),
    "flac": ("audio/flac", lambda b: b[:4] == b"fLaC"),
    "wav": ("audio/wav", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WAVE"),
    # OpenAI pcm: 24 kHz int16 mono, so 0.1 s == 2400 samples == 4800 bytes.
    "pcm": ("audio/pcm", lambda b: len(b) == 4800),
}


@pytest.mark.parametrize("fmt", sorted(_FORMAT_CHECKS))
def test_speech_every_format_is_really_that_format(client, engine, fmt):
    mime, check = _FORMAT_CHECKS[fmt]
    raw = client.audio.speech.with_raw_response.create(
        model="tts-1", voice="alloy", input="Hello.", response_format=fmt,
    )
    body = raw.content
    assert raw.headers["content-type"] == mime
    assert check(body), (fmt, body[:16])


def test_wav_keeps_engine_rate(client, engine):
    body = client.audio.speech.create(
        model="tts-1", voice="alloy", input="Hello.", response_format="wav",
    ).content
    with wave.open(io.BytesIO(body)) as w:
        assert w.getframerate() == ENGINE_RATE


def test_pcm_is_24khz_int16_le(client, engine):
    body = client.audio.speech.create(
        model="tts-1", voice="alloy", input="Hello.", response_format="pcm",
    ).content
    samples = struct.unpack(f"<{len(body) // 2}h", body)
    assert len(samples) == 2400 and max(abs(s) for s in samples) > 1000


def test_agents_sdk_voice_pipeline_request(client, engine):
    """The Agents SDK's OpenAITTSModel request: gpt-4o-mini-tts, voice 'ash',
    streamed pcm, `instructions` via extra_body."""
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts", voice="ash", input="Agent speaking.",
        response_format="pcm", extra_body={"instructions": "Calm and warm."},
    ) as res:
        body = b"".join(res.iter_bytes())
    assert len(body) == 4800
    text, kw = engine.calls[-1]
    assert text == "Agent speaking."
    assert kw["instruct"] == "Calm and warm."
    assert "voice" not in kw  # OpenAI voice names map to the engine default


@pytest.mark.parametrize("model", ["gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15", "tts-1-hd"])
def test_openai_tts_model_ids_alias_the_active_engine(client, engine, model):
    client.audio.speech.create(model=model, voice="coral", input="Hi.", response_format="wav")
    assert engine.calls


def test_real_engine_id_still_selects_that_engine(client, engine):
    client.audio.speech.create(model=engine.id, voice="default", input="Hi.", response_format="wav")
    assert engine.calls


def test_explicit_instruct_wins_over_instructions(client, engine):
    client.audio.speech.create(
        model="tts-1", voice="alloy", input="Hi.", response_format="wav",
        instructions="whisper", extra_body={"instruct": "shout"},
    )
    assert engine.calls[-1][1]["instruct"] == "shout"


@pytest.mark.parametrize(("instructions", "expected"), [
    # The Agents SDK's default instructions are prose; OmniVoice's instruct
    # validator rejects prose, which would fail every Agents SDK request.
    ("You will receive partial sentences. Do not complete the sentence.", None),
    ("female, whisper", "female, whisper"),
])
def test_omnivoice_family_gets_only_design_tags(client, engine, monkeypatch, instructions, expected):
    monkeypatch.setattr(engine, "instruct_vocabulary", "tags", raising=False)
    client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="ash", input="Hi.", response_format="wav",
        instructions=instructions,
    )
    assert engine.calls[-1][1].get("instruct") == expected


def test_voice_object_is_unwrapped(client, engine):
    client.audio.speech.create(
        model="tts-1", voice={"id": "preset-x"}, input="Hi.", response_format="wav",
    )
    assert engine.calls[-1][1]["voice"] == "preset-x"


def test_stream_format_audio_and_sse(client, engine):
    with client.audio.speech.with_streaming_response.create(
        model="tts-1", voice="alloy", input="Hi.", response_format="pcm", stream_format="audio",
    ) as res:
        assert len(b"".join(res.iter_bytes())) == 4800

    import base64
    import json

    with client.audio.speech.with_streaming_response.create(
        model="tts-1", voice="alloy", input="Hi.", response_format="pcm", stream_format="sse",
    ) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in res.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "speech.audio.done"
    audio = b"".join(base64.b64decode(e["audio"]) for e in events if e["type"] == "speech.audio.delta")
    assert len(audio) == 4800


def test_pcm_stream_yields_first_sentence_before_rendering_second(engine):
    import asyncio

    from api.routers.openai_compat import SpeechRequest, create_speech

    async def exercise():
        first = "This is the first sentence."
        second = "Here is the second sentence."
        response = await create_speech(SpeechRequest(
            model="tts-1", input=f"{first} {second}", response_format="pcm",
            stream_format="audio", num_step=8, language="en",
        ))
        assert [text for text, _ in engine.calls] == [first]
        stream = response.body_iterator
        assert len(await anext(stream)) == 4800
        assert [text for text, _ in engine.calls] == [first]
        assert len(await anext(stream)) == 4800
        assert engine.calls[-1] == (second, engine.calls[0][1])
        assert engine.calls[-1][1]["num_step"] == 8
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

        response = await create_speech(SpeechRequest(
            model="tts-1", input=f"{first} {second}", response_format="pcm", stream_format="sse",
        ))
        stream = response.body_iterator
        assert '"type": "speech.audio.delta"' in await anext(stream)
        await stream.aclose()
        assert [text for text, _ in engine.calls] == [first, second, first]

    asyncio.run(exercise())


def test_native_pcm_reports_input_failure_before_sending_headers(client, engine, monkeypatch):
    def chunks(self, text, **kwargs):
        if text:
            raise _tts_mod().TTSInputError("A reference is required.")
        yield torch.zeros(1, 4800)

    monkeypatch.setattr(engine, "can_stream", lambda self, **kw: True, raising=False)
    monkeypatch.setattr(engine, "stream_audio", chunks, raising=False)
    with pytest.raises(openai.BadRequestError, match="A reference is required"):
        client.audio.speech.create(
            model="tts-1", voice="alloy", input="Hello.", response_format="pcm",
            stream_format="audio",
        )


def test_native_pcm_reports_late_sse_error_and_closes_on_the_worker(engine, monkeypatch):
    import threading

    from api.routers import openai_compat

    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", threading.Lock())
    threads = []

    def chunks(self, text, **kwargs):
        threads.append(threading.get_ident())
        try:
            yield torch.zeros(1, 4800)
            threads.append(threading.get_ident())
            raise RuntimeError("late synthesis failure")
        finally:
            threads.append(threading.get_ident())

    monkeypatch.setattr(engine, "can_stream", lambda self, **kw: True, raising=False)
    monkeypatch.setattr(engine, "stream_audio", chunks, raising=False)
    from services import engine_routing

    async def profile(*args, **kwargs):
        return {"routing_status": "cpu_fallback", "routing_reason": "test routing notice"}

    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", profile)
    from main import app

    try:
        response = TestClient(app, client=("127.0.0.1", 50000)).post("/v1/audio/speech", json={
            "model": "tts-1", "voice": "alloy", "input": "Hello.",
            "response_format": "pcm", "stream_format": "sse",
        })
    finally:
        worker.shutdown()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-omnivoice-routing"] == "cpu_fallback"
    assert response.headers["x-omnivoice-routing-reason"] == "test routing notice"
    assert '"type": "speech.audio.delta"' in response.text
    assert '"type": "error"' in response.text
    assert '"type": "speech.audio.done"' not in response.text
    assert len(threads) == 3 and len(set(threads)) == 1
    assert not openai_compat._native_pcm_lock.locked()


def test_native_pcm_timeout_keeps_worker_reserved_until_cleanup(client, engine, monkeypatch):
    import threading

    from api.routers import openai_compat
    from services import model_manager

    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    slot = threading.Lock()
    unblock = threading.Event()
    closed = threading.Event()
    warmups = []
    monkeypatch.setattr(engine, "ensure_ready", lambda self: warmups.append(self))
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", slot)
    monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *a, **kw: 0.05)

    def chunks(self, text, **kwargs):
        try:
            assert unblock.wait(10)
            yield torch.zeros(1, 4800)
        finally:
            closed.set()

    monkeypatch.setattr(engine, "can_stream", lambda self, **kw: True, raising=False)
    monkeypatch.setattr(engine, "stream_audio", chunks, raising=False)
    request = dict(model="tts-1", voice="alloy", input="Hello.", response_format="pcm",
                   stream_format="audio")
    try:
        with pytest.raises(openai.InternalServerError) as timeout:
            client.audio.speech.create(**request)
        assert timeout.value.status_code == 503
        assert slot.locked() and not closed.is_set()
        with pytest.raises(openai.RateLimitError):
            client.audio.speech.create(**request)
        assert len(warmups) == 1
    finally:
        unblock.set()
        worker.shutdown()
    assert closed.is_set()
    assert not slot.locked()


def test_native_pcm_lease_failure_releases_the_lock(engine, monkeypatch):
    import asyncio
    import threading

    from api.routers import openai_compat

    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    slot = threading.Lock()
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", slot)

    class BrokenLease:
        def __enter__(self):
            raise RuntimeError("lease unavailable")

        def __exit__(self, *exc):
            pytest.fail("A lease that never entered must not exit")

    monkeypatch.setattr(_tts_mod(), "engine_in_use", lambda backend, **kw: BrokenLease())
    try:
        with pytest.raises(RuntimeError, match="lease unavailable"):
            asyncio.run(openai_compat._native_pcm_stream(engine(), "Hello.", {}, sse=False).__anext__())
        worker.submit(lambda: None).result(timeout=5)
        assert not slot.locked()
    finally:
        worker.shutdown()


def test_native_pcm_failure_after_first_chunk_closes_stream_and_next_request_works(engine, monkeypatch):
    import threading

    from api.routers import openai_compat
    from services import model_manager

    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    slot = threading.Lock()
    closed = threading.Event()
    timeouts = []
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", slot)

    def timeout(*a, **kw):
        # Called once per chunk wait, then again while the response is built.
        timeouts.append(1)
        if len(timeouts) == 2:
            raise RuntimeError("response could not be built")
        return 5

    def chunks(self, text, **kwargs):
        try:
            yield torch.zeros(1, 4800)
            yield torch.zeros(1, 4800)
        finally:
            closed.set()

    monkeypatch.setattr(model_manager, "generate_timeout_s", timeout)
    monkeypatch.setattr(engine, "can_stream", lambda self, **kw: True, raising=False)
    monkeypatch.setattr(engine, "stream_audio", chunks, raising=False)
    from main import app

    request = {"model": "tts-1", "voice": "alloy", "input": "Hello.",
               "response_format": "pcm", "stream_format": "audio"}
    http = TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)
    try:
        assert http.post("/v1/audio/speech", json=request).status_code == 500
        worker.submit(lambda: None).result(timeout=5)
        assert closed.is_set()
        assert not slot.locked()
        closed.clear()
        monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *a, **kw: 5)
        ok = http.post("/v1/audio/speech", json=request)
        assert ok.status_code == 200 and len(ok.content) == 9600
    finally:
        worker.shutdown()


def test_native_pcm_busy_answers_429_with_retry_after(client, engine, monkeypatch):
    import threading

    from api.routers import openai_compat

    slot = threading.Lock()
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", slot)
    monkeypatch.setattr(engine, "can_stream", lambda self, **kw: True, raising=False)
    assert slot.acquire(blocking=False)
    with pytest.raises(openai.RateLimitError) as busy:
        client.audio.speech.create(model="tts-1", voice="alloy", input="Hello.",
                                   response_format="pcm", stream_format="audio")
    assert busy.value.response.headers["retry-after"] == "1"


def test_buffered_request_waits_out_an_active_native_stream(client, engine, monkeypatch):
    ts = _tts_mod()
    stream_lease = ts.engine_in_use(engine(), native=True)
    stream_lease.__enter__()
    try:
        with pytest.raises(openai.RateLimitError) as busy:
            client.audio.speech.create(model="tts-1", voice="alloy", input="Hello.",
                                       response_format="wav")
        assert busy.value.response.headers["retry-after"] == "1"
        assert not engine.calls
    finally:
        stream_lease.__exit__(None, None, None)
    client.audio.speech.create(model="tts-1", voice="alloy", input="Hello.", response_format="wav")
    assert engine.calls


def test_native_stream_refuses_to_start_beside_running_generation(engine):
    ts = _tts_mod()
    backend = engine()
    with ts.engine_in_use(backend):
        with pytest.raises(ts.EngineBusyError):
            ts.engine_in_use(backend, native=True).__enter__()
    with ts.engine_in_use(backend, native=True):
        with pytest.raises(ts.EngineBusyError):
            ts.engine_in_use(backend, yield_to_native=True).__enter__()
    with ts.engine_in_use(backend, yield_to_native=True):
        pass


def test_primed_speech_closes_when_transport_stops_accepting_audio():
    import asyncio

    from starlette.requests import ClientDisconnect

    from api.routers.openai_compat import _PrimedSpeechResponse

    async def exercise():
        closed = False

        async def chunks():
            nonlocal closed
            try:
                yield b"pcm"
            finally:
                closed = True

        async def send(message):
            if message["type"] == "http.response.body":
                await asyncio.Future()

        async def receive():
            await asyncio.Future()

        stream = chunks()
        response = _PrimedSpeechResponse(await anext(stream), stream, send_timeout=0.05)
        with pytest.raises(ClientDisconnect) as error:
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert isinstance(error.value.__context__, TimeoutError)
        assert closed

    asyncio.run(exercise())


@pytest.mark.parametrize("fmt,duration", [("wav", None), ("pcm", 2)])
def test_speech_stream_keeps_container_and_duration_whole(client, engine, fmt, duration):
    text = "This is the first sentence. Here is the second sentence."
    client.audio.speech.create(
        model="tts-1", voice="alloy", input=text, response_format=fmt, stream_format="audio",
        extra_body={"duration": duration},
    )
    assert [value for value, _ in engine.calls] == [text]


def test_unknown_model_is_openai_error(client, engine):
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.speech.create(model="no-such-model", voice="alloy", input="Hi.")
    err = ei.value.body
    assert err["param"] == "model" and err["code"] == "model_not_found"
    assert err["type"] == "invalid_request_error"
    assert "Unknown model" in err["message"]
    assert not engine.calls


def test_validation_error_is_openai_400(client, engine):
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.speech.create(
            model="tts-1", voice="alloy", input="Hi.", response_format="ogg",  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 400
    assert ei.value.body["param"] == "response_format"


def test_missing_encoder_is_a_clear_400_not_a_wav(client, engine, monkeypatch):
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.speech.create(model="tts-1", voice="alloy", input="Hi.", response_format="mp3")
    assert ei.value.body["code"] == "unsupported_response_format"
    assert not engine.calls  # refused before any GPU work
    # Encoder-free formats still work without ffmpeg.
    client.audio.speech.create(model="tts-1", voice="alloy", input="Hi.", response_format="flac")


def test_http_errors_keep_detail_for_existing_clients(engine):
    from main import app

    res = TestClient(app, client=("127.0.0.1", 50000)).post(
        "/v1/audio/speech", json={"model": "no-such-model", "input": "Hi."},
    )
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["message"] == body["detail"]


# ── Models ──────────────────────────────────────────────────────────────────


def test_models_list_and_retrieve(client, engine):
    ids = {m.id for m in client.models.list()}
    assert {"tts-1", "tts-1-hd", "gpt-4o-mini-tts", "whisper-1", "gpt-4o-transcribe"} <= ids
    assert engine.id in ids
    assert client.models.retrieve("gpt-4o-mini-tts").owned_by == "voicestudio"
    with pytest.raises(openai.NotFoundError):
        client.models.retrieve("definitely-not-a-model")


# ── Transcriptions / translations ───────────────────────────────────────────


def _asr(monkeypatch, backend):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", lambda **_k: backend)


class _WhisperLike:
    id = "fake-whisper"

    def __init__(self, language="de", translates=True):
        self.calls = []
        self.language = language
        self.translates = translates

    def supports_translation(self):
        return self.translates

    def transcribe(self, audio_path, *, word_timestamps=True, language=None,
                   initial_prompt=None, temperature=None, task="transcribe"):
        self.calls.append(dict(word_timestamps=word_timestamps, language=language,
                               initial_prompt=initial_prompt, temperature=temperature, task=task))
        words = [{"word": " Hallo", "start": 0.0, "end": 0.4}, {"word": "5", "start": None, "end": None},
                 {"word": " Welt", "start": 0.5, "end": 0.9}] if word_timestamps else []
        return {"segments": [{"text": " Hallo Welt", "start": 0.0, "end": 1.0, "words": words}],
                "language": self.language, "duration": 1.0}


class _CtcLike:
    """An engine that takes none of the OpenAI decode options."""
    id = "fake-ctc"

    def transcribe(self, audio_path, *, word_timestamps=True):
        return {"segments": [{"text": "hi", "start": 0.0, "end": 0.5}], "language": None}


_AUDIO = ("clip.wav", b"RIFF0000WAVEfmt ", "audio/wav")


def test_transcription_passes_options_and_reports_language(client, monkeypatch):
    fake = _WhisperLike(language="de")
    _asr(monkeypatch, fake)
    res = client.audio.transcriptions.create(
        model="gpt-4o-transcribe", file=_AUDIO, language="de", prompt="Hallo",
        temperature=0.2, response_format="verbose_json",
        timestamp_granularities=["word", "segment"],
    )
    assert fake.calls[-1] == dict(word_timestamps=True, language="de", initial_prompt="Hallo",
                                  temperature=0.2, task="transcribe")
    assert res.language == "de" and res.text == "Hallo Welt"
    assert [w.word for w in res.words] == ["Hallo", "Welt"]
    seg = res.segments[0]
    assert (seg.id, seg.start, seg.end, seg.temperature) == (0, 0.0, 1.0, 0.2)
    assert seg.tokens == [] and seg.avg_logprob == 0.0


def test_verbose_json_without_word_granularity_skips_alignment(client, monkeypatch):
    fake = _WhisperLike()
    _asr(monkeypatch, fake)
    res = client.audio.transcriptions.create(model="whisper-1", file=_AUDIO, response_format="verbose_json")
    assert fake.calls[-1]["word_timestamps"] is False
    assert res.words is None and res.segments


def test_language_is_not_hardcoded_english(client, monkeypatch):
    _asr(monkeypatch, _CtcLike())
    res = client.audio.transcriptions.create(model="whisper-1", file=_AUDIO, response_format="verbose_json")
    assert res.language == "unknown"
    res = client.audio.transcriptions.create(
        model="whisper-1", file=_AUDIO, language="fr", response_format="verbose_json",
    )
    assert res.language == "fr"


def test_engine_without_options_is_not_handed_them(client, monkeypatch):
    _asr(monkeypatch, _CtcLike())
    res = client.audio.transcriptions.create(
        model="whisper-1", file=_AUDIO, language="en", prompt="p", temperature=0.5,
    )
    assert res.text == "hi"


def test_transcription_errors_are_openai_shaped(client, monkeypatch):
    _asr(monkeypatch, _WhisperLike())
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.transcriptions.create(model="whisper-1", file=_AUDIO, response_format="docx")  # type: ignore[arg-type]
    assert ei.value.body["param"] == "response_format"


def test_asr_engine_id_must_be_the_engine_that_serves(client, monkeypatch):
    """A concrete VoiceStudio ASR id is never silently served by another engine."""
    from services import asr_backend

    fake = _WhisperLike()
    _asr(monkeypatch, fake)
    monkeypatch.setitem(asr_backend._REGISTRY, "fake-whisper", _WhisperLike)
    monkeypatch.setitem(asr_backend._REGISTRY, "other-asr", _CtcLike)
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.transcriptions.create(model="other-asr", file=_AUDIO)
    assert ei.value.body["code"] == "model_not_active"
    assert "fake-whisper" in ei.value.body["message"]
    assert client.audio.transcriptions.create(model="fake-whisper", file=_AUDIO).text == "Hallo Welt"
    # Unregistered names (OpenAI ids, client defaults) still mean "the active engine".
    assert client.audio.transcriptions.create(model="whisper-large-v3", file=_AUDIO).text == "Hallo Welt"


def test_models_list_only_the_active_asr_engine(client, engine, monkeypatch):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "faster-whisper")
    stt = {m.id for m in client.models.list() if m.model_extra["voicestudio"]["kind"] == "stt"}
    assert stt == {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "faster-whisper"}


def test_translation_uses_whisper_translate_task(client, monkeypatch):
    fake = _WhisperLike(language="de")
    _asr(monkeypatch, fake)
    res = client.audio.translations.create(model="whisper-1", file=_AUDIO, response_format="verbose_json")
    assert fake.calls[-1]["task"] == "translate"
    assert res.language == "en"


def test_translation_refused_for_transcription_only_checkpoint(client, monkeypatch):
    """Whisper turbo ignores task=translate and returns the source language;
    that must be a 400, never source-language text labelled English."""
    fake = _WhisperLike(translates=False)
    _asr(monkeypatch, fake)
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.translations.create(model="whisper-1", file=_AUDIO)
    assert ei.value.body["code"] == "unsupported_task"
    assert "turbo" in ei.value.body["message"]
    assert not fake.calls


@pytest.mark.parametrize(("name", "ok"), [
    ("large-v3", True),
    ("Systran/faster-whisper-large-v3", True),
    ("openai/whisper-large-v3-turbo", False),
    ("deepdml/faster-whisper-large-v3-turbo-ct2", False),
    ("mlx-community/whisper-large-v3-turbo", False),
    ("distil-large-v3", False),
    ("small.en", False),
])
def test_whisper_checkpoint_translation_capability(name, ok):
    from services.asr_backend import FasterWhisperBackend, whisper_checkpoint_translates

    assert whisper_checkpoint_translates(name) is ok
    b = FasterWhisperBackend.__new__(FasterWhisperBackend)
    b._model_name = name
    assert b.supports_translation() is ok


def test_translation_on_engine_that_cannot_translate_is_clear_400(client, monkeypatch):
    _asr(monkeypatch, _CtcLike())
    with pytest.raises(openai.BadRequestError) as ei:
        client.audio.translations.create(model="whisper-1", file=_AUDIO)
    assert ei.value.body["code"] == "unsupported_task"


# ── ASR backends accept the options they advertise ─────────────────────────


def test_faster_whisper_forwards_request_options():
    from types import SimpleNamespace

    from services.asr_backend import FasterWhisperBackend

    seen = {}

    class _Model:
        def transcribe(self, path, **kw):
            seen.update(kw)
            return iter([]), SimpleNamespace(language="fr", language_probability=1.0, duration=0.0)

    b = FasterWhisperBackend.__new__(FasterWhisperBackend)
    b._model = _Model()
    out = b.transcribe("x.wav", word_timestamps=False, language="fr", initial_prompt="p",
                       temperature=0.3, task="translate")
    assert (seen["language"], seen["initial_prompt"], seen["temperature"], seen["task"]) == ("fr", "p", 0.3, "translate")
    assert out["language"] == "fr"
    seen.clear()
    b.transcribe("x.wav", word_timestamps=False)  # unset options stay absent
    assert not {"language", "initial_prompt", "temperature", "task"} & set(seen)


def test_isolated_sidecar_validates_request_options(monkeypatch):
    from types import SimpleNamespace

    from engines._asr_sidecar import main as sidecar

    calls = []

    class _Model:
        def transcribe(self, path, **options):
            calls.append(options)
            return [], SimpleNamespace(language="de")

    monkeypatch.setattr(sidecar, "_get_model", lambda: _Model())
    sidecar._transcribe("/a.wav", False, {}, {"language": "de", "initial_prompt": "p",
                                             "temperature": 0.2, "task": "translate"})
    assert calls[-1] == {"word_timestamps": False, "condition_on_previous_text": False,
                         "language": "de", "initial_prompt": "p",
                         "temperature": 0.2, "task": "translate"}
    for bad in ({"task": "summarise"}, {"temperature": 3}, {"beam_size": 5}, {"language": ""}):
        with pytest.raises(ValueError):
            sidecar._transcribe("/a.wav", False, {}, bad)


def test_timed_out_mlx_step_releases_shared_gpu_worker_before_native_cleanup(monkeypatch):
    import asyncio
    import threading

    from api.routers import openai_compat
    from services import model_manager
    from services.tts_backend import MLXAudioBackend

    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    shared = openai_compat.ThreadPoolExecutor(max_workers=1)
    slot = threading.Lock()
    entered = threading.Event()
    unblock = threading.Event()
    closed = threading.Event()
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", slot)
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: shared)
    monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *a, **kw: 0.5)
    backend = MLXAudioBackend()

    def chunks(*args, **kwargs):
        try:
            entered.set()
            assert unblock.wait(10)
            yield torch.zeros(1, 4800)
        finally:
            closed.set()

    monkeypatch.setattr(backend, "stream_audio", chunks)

    async def exercise():
        stream = openai_compat._native_pcm_stream(backend, "Hello.", {}, sse=False)
        first = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(entered.wait, 2)
        peer = asyncio.wrap_future(shared.submit(lambda: "peer ran"))
        with pytest.raises(TimeoutError):
            await first
        assert await asyncio.wait_for(peer, 2) == "peer ran"
        assert slot.locked() and not closed.is_set()

    try:
        asyncio.run(exercise())
    finally:
        unblock.set()
        shared.shutdown()
        worker.shutdown()

    assert closed.is_set() and not slot.locked()
