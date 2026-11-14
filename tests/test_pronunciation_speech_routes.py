"""Saved pronunciation rows reach actual speech dispatch, once per input.

These fixtures come from maintained route suites; only inference/ASR are fake.
The dictionary is persisted in SQLite and its precedence runs unmodified.
"""
import asyncio
import importlib
import sqlite3
import threading

import httpx
import pytest
import torch

from test_dub_crash_resume import dub  # noqa: F401
from test_text_normalization_routes import (  # noqa: F401
    client, fake_engine, saved_pronunciations, batch_env, _run_ws_request,
)


@pytest.fixture
def dictionary_read_threads(saved_pronunciations, monkeypatch):
    pronunciation = importlib.import_module("services.pronunciation")
    load = pronunciation.load_entries_from_db
    reads_on_event_loop = []

    def load_rows():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            reads_on_event_loop.append(False)
        else:
            reads_on_event_loop.append(True)
        return load()

    monkeypatch.setattr(pronunciation, "load_entries_from_db", load_rows)
    return reads_on_event_loop


@pytest.fixture
def spoken_dub(dub, fake_engine, monkeypatch):
    backend = fake_engine()

    def generate(**kwargs):
        fake_engine.calls.append((kwargs["text"], kwargs))
        return torch.ones(1, 24000) * 0.1

    monkeypatch.setattr(backend, "generate", generate)

    async def resolve(**_kwargs):
        return backend

    async def resolve_dub():
        # Exercise the verified-local-model journal as well as speech dispatch.
        return "omnivoice", backend

    monkeypatch.setattr(dub.dg, "_resolve_dub_execution", resolve_dub)
    monkeypatch.setattr(dub.dg, "resolve_generation_backend", resolve)
    monkeypatch.setattr(dub.dg, "native_batch_width", lambda _backend: 2)
    return dub, backend


@pytest.mark.parametrize("native", [False, True])
def test_full_dub_uses_one_saved_dictionary_snapshot_for_keys_and_inference(
    spoken_dub, fake_engine, saved_pronunciations, monkeypatch, native,
):
    dub, backend = spoken_dub
    pronunciation = importlib.import_module("services.pronunciation")
    db = importlib.import_module("core.db")
    loaded_on = []
    load = pronunciation.load_entries_from_db

    def load_rows():
        loaded_on.append(threading.get_ident())
        return load()

    monkeypatch.setattr(pronunciation, "load_entries_from_db", load_rows)
    keyed_texts = []
    build_key = dub.dg.dub_resume.build_key

    def key(parts):
        keyed_texts.append(parts["text"])
        return build_key(parts)

    monkeypatch.setattr(dub.dg.dub_resume, "build_key", key)

    def change_saved_rows():
        with db.db_conn() as conn:
            conn.execute("UPDATE pronunciation_entries SET replacement='puppies' "
                         "WHERE term='cats' AND profile_id='pron-voice'")

    batches = []
    if native:
        def generate_batch(_self, texts, **_kwargs):
            batches.append(list(texts))
            change_saved_rows()
            return [torch.ones(1, 24000) * 0.1 for _ in texts]

        monkeypatch.setattr(fake_engine, "generate_batch", generate_batch)
    else:
        generate = backend.generate

        def generate_one(**kwargs):
            change_saved_rows()
            return generate(**kwargs)

        monkeypatch.setattr(backend, "generate", generate_one)

    segments = [dict(start=i * 2, end=(i + 1) * 2,
                     text="cats [[cats|cats]]", profile_id="pron-voice") for i in range(2)]
    events = dub.run(segments=segments, segment_ids=["a", "b"], language="English")
    assert any(event["type"] == "done" for event in events), events
    actual = batches[0] if native else [text for text, _ in fake_engine.calls]
    assert actual == ["felines cats"] * 2
    assert keyed_texts and set(keyed_texts) == {"felines cats"}
    assert len(loaded_on) == 1 and loaded_on[0] != threading.get_ident()
    if native:
        assert not fake_engine.calls  # no single-row fallback hidden by silence

    fake_engine.calls.clear()
    batches.clear()
    keyed_texts.clear()
    events = dub.run(segments=segments, segment_ids=["a", "b"], language="English")
    assert any(event["type"] == "done" for event in events), events
    assert len(loaded_on) == 2  # the next render sees the edited saved rows
    actual = batches[0] if native else [text for text, _ in fake_engine.calls]
    assert actual == ["puppies cats"] * 2
    assert keyed_texts and set(keyed_texts) == {"puppies cats"}


def test_dub_preview_uses_saved_voice_entries_and_inline_precedence(
    spoken_dub, client, fake_engine, saved_pronunciations, dictionary_read_threads,
):
    response = client.post("/dub/preview-segment/job", json={
        "text": "Dr. Smith has 2 cats. [[cats|cats]]", "language": "English",
        "profile_id": "pron-voice",
    })
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("audio/")
    assert fake_engine.calls[-1][0] == "Professor Smith has two felines. cats"
    assert dictionary_read_threads == [False]


def test_incremental_dub_detects_only_changed_effective_saved_pronunciations(
    spoken_dub, client, fake_engine, saved_pronunciations,
):
    dub, _backend = spoken_dub
    segments = [
        dict(start=i, end=i + 1, text=text, profile_id=voice)
        for i, (text, voice) in enumerate([
            ("cats", "pron-voice"), ("cats", ""),
            ("dogs", "pron-voice"), ("[[cats|cats]]", "pron-voice"),
        ])
    ]
    ids = ["voice", "global", "unrelated", "inline"]
    events = dub.run(segments=segments, segment_ids=ids, language="English")
    assert any(event["type"] == "done" for event in events), events
    stored = dict(dub.job["seg_hashes_by_lang"]["en"])

    def plan():
        response = client.post("/tools/incremental", json={
            "segments": [dict(segment, id=sid) for sid, segment in zip(ids, segments)],
            "stored_hashes": stored, "lang": "en",
        })
        assert response.status_code == 200, response.text
        return response.json()

    assert plan()["fresh"] == ids
    db = importlib.import_module("core.db")
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement='puppies' "
                     "WHERE term='cats' AND profile_id='pron-voice'")
    changed = plan()
    assert changed["stale"] == ["voice"]
    assert changed["fresh"] == ids[1:]
    unchanged_audio = {sid: (dub.path / f"seg_en_{sid}.wav").read_bytes() for sid in ids[1:]}
    fake_engine.calls.clear()
    events = dub.run(segments=segments, segment_ids=ids, language="English",
                     regen_only=changed["stale"])
    assert any(event["type"] == "done" for event in events), events
    assert [text for text, _kwargs in fake_engine.calls] == ["puppies"]
    assert all((dub.path / f"seg_en_{sid}.wav").read_bytes() == audio
               for sid, audio in unchanged_audio.items())

    # Fit-only assembly continues to reuse the now-current speech cache.
    fake_engine.calls.clear()
    events = dub.run(segments=segments, segment_ids=ids, language="English",
                     regen_only=[], timing_strategy="strict_slot")
    assert any(event["type"] == "done" for event in events), events
    assert not fake_engine.calls


def test_incremental_spoken_language_can_differ_from_track_code(
    spoken_dub, client, saved_pronunciations,
):
    dub, _backend = spoken_dub
    segments = [
        dict(start=0, end=1, text="cats", profile_id="pron-voice"),
        dict(start=1, end=2, text="cats", profile_id="pron-voice", target_lang="en"),
    ]
    events = dub.run(segments=segments, segment_ids=["auto", "english"], language="Auto")
    assert any(event["type"] == "done" for event in events), events
    body = {
        "segments": [dict(segment, id=sid)
                     for sid, segment in zip(["auto", "english"], segments)],
        "stored_hashes": dub.job["seg_hashes_by_lang"]["en"],
        "lang": "en", "language": "Auto",
    }
    response = client.post("/tools/incremental", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["fresh"] == ["auto", "english"]
    db = importlib.import_module("core.db")
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement='puppies' "
                     "WHERE term='cats' AND profile_id='pron-voice'")
    response = client.post("/tools/incremental", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["stale"] == ["english"]


@pytest.mark.parametrize("route", ["openai", "websocket"])
def test_native_streams_prepare_the_whole_saved_voice_text_once(
    client, fake_engine, saved_pronunciations, monkeypatch, dictionary_read_threads, route,
):
    streamed = []

    def stream_audio(_self, text, **kwargs):
        streamed.append((text, kwargs))
        yield torch.ones(1, 240) * 0.1
        yield torch.ones(1, 240) * 0.2

    monkeypatch.setattr(fake_engine, "can_stream", lambda _self, **_kw: True, raising=False)
    monkeypatch.setattr(fake_engine, "stream_audio", stream_audio, raising=False)
    payload = dict(text="Dr. Smith has 2 cats. [[cats|cats]]", language="en",
                   voice="pron-voice", engine="fake-norm-route")
    if route == "websocket":
        frames = _run_ws_request(client, payload)
        assert frames[-1]["type"] == "done", frames
    else:
        response = client.post("/v1/audio/speech", json={
            "model": payload["engine"], "input": payload["text"],
            "voice": "Pronunciation Voice", "language": "en", "response_format": "pcm",
            "stream_format": "audio",
        })
        assert response.status_code == 200 and response.content, response.text
    assert len(streamed) == 1
    assert streamed[0][0] == "Professor Smith has two felines. cats"
    assert not fake_engine.calls
    assert dictionary_read_threads == [False]


def test_voice_conversion_uses_saved_voice_entries_after_asr(
    client, fake_engine, saved_pronunciations, monkeypatch, tmp_path, dictionary_read_threads,
):
    from test_voice_convert import _wire_stubs, _FakeASR, _post_convert

    db = importlib.import_module("core.db")
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET ref_audio_path='convert-ref.wav', "
                     "ref_text='reference words', language='en' WHERE id='pron-voice'")
    generated = tmp_path / "converted"
    generated.mkdir()
    monkeypatch.setattr("api.routers.generation.OUTPUTS_DIR", str(generated))
    _wire_stubs(monkeypatch, engine_cls=fake_engine, asr=_FakeASR({
        "text": "Dr. Smith has 2 cats. [[cats|cats]]", "segments": [],
    }))
    response = _post_convert(client, "pron-voice", match_duration="0")
    assert response.status_code == 200, response.text
    assert fake_engine.calls[-1][0] == "Professor Smith has two felines. cats."
    assert dictionary_read_threads == [False]


@pytest.mark.parametrize("route", ["openai", "websocket"])
@pytest.mark.parametrize("control", ["disabled", "unreadable"])
def test_unavailable_saved_dictionary_still_normalizes_and_honors_inline(
    client, fake_engine, saved_pronunciations, monkeypatch, route, control,
):
    pronunciation = importlib.import_module("services.pronunciation")
    loads = []

    def unavailable():
        loads.append(True)
        raise sqlite3.OperationalError("controlled dictionary read failure")

    monkeypatch.setattr(pronunciation, "load_entries_from_db", unavailable)
    if control == "disabled":
        monkeypatch.setenv("OMNIVOICE_PRONUNCIATION", "0")
    payload = dict(text="Dr. Smith has 2 cats. [[cats|felines]]", language="en",
                   voice="pron-voice", engine="fake-norm-route")
    if route == "websocket":
        frames = _run_ws_request(client, payload)
        assert frames[-1]["type"] == "done", frames
    else:
        response = client.post("/v1/audio/speech", json={
            "model": payload["engine"], "input": payload["text"],
            "voice": payload["voice"], "language": "en", "response_format": "wav",
        })
        assert response.status_code == 200, response.text
    assert " ".join(text.strip() for text, _ in fake_engine.calls) == "Doctor Smith has two cats. felines"
    assert bool(loads) is (control == "unreadable")


@pytest.mark.parametrize("pronounce,spoken", [("0", "cats"), ("1", "felines")])
def test_generate_request_switch_keeps_inline_choices(
    client, fake_engine, saved_pronunciations, monkeypatch, tmp_path, dictionary_read_threads,
    pronounce, spoken,
):
    generated = tmp_path / "generated"
    generated.mkdir()
    monkeypatch.setattr("api.routers.generation.OUTPUTS_DIR", str(generated))
    response = client.post("/generate", data={
        "engine": "fake-norm-route", "profile_id": "pron-voice", "language": "English",
        "text": "Dr. Smith has 2 cats. [[cats|cats]]", "pronounce": pronounce, "seed": "42",
    })
    assert response.status_code == 200, response.text
    title = "Doctor" if pronounce == "0" else "Professor"
    assert fake_engine.calls[-1][0] == f"{title} Smith has two {spoken}. cats"
    assert dictionary_read_threads == ([False] if pronounce == "1" else [])


@pytest.mark.parametrize("route", ["openai", "websocket"])
def test_sentence_stream_dictionary_reads_run_off_the_event_loop(
    client, fake_engine, saved_pronunciations, dictionary_read_threads, route,
):
    payload = dict(text="Dr. Smith has 2 cats. [[cats|cats]]", language="en",
                   voice="pron-voice", engine="fake-norm-route")
    if route == "websocket":
        frames = _run_ws_request(client, payload)
        assert frames[-1]["type"] == "done", frames
    else:
        response = client.post("/v1/audio/speech", json={
            "model": payload["engine"], "input": payload["text"],
            "voice": payload["voice"], "language": "en", "response_format": "pcm",
            "stream_format": "audio",
        })
        assert response.status_code == 200 and response.content, response.text
    assert dictionary_read_threads == [False]
    assert " ".join(text.strip() for text, _kw in fake_engine.calls) == "Professor Smith has two felines. cats"


def test_public_sentence_stream_reads_one_dictionary_snapshot_off_loop(
    fake_engine, saved_pronunciations, dictionary_read_threads,
):
    from api.routers.tts_stream import synthesize_stream

    async def render():
        return [chunk async for chunk in synthesize_stream(
            "Dr. Smith has 2 cats. [[cats|cats]]", language="en",
            voice="pron-voice", engine="fake-norm-route",
        )]

    assert asyncio.run(render())
    assert dictionary_read_threads == [False]
    assert " ".join(text.strip() for text, _kw in fake_engine.calls) == "Professor Smith has two felines. cats"


def test_openai_dictionary_read_keeps_the_request_loop_responsive(
    fake_engine, saved_pronunciations, monkeypatch,
):
    from main import app
    pronunciation = importlib.import_module("services.pronunciation")
    load = pronunciation.load_entries_from_db
    started, release = threading.Event(), threading.Event()
    released_while_reading = []

    def load_rows():
        started.set()
        released_while_reading.append(release.wait(timeout=3))
        return load()

    monkeypatch.setattr(pronunciation, "load_entries_from_db", load_rows)

    async def render():
        async def observe():
            assert await asyncio.to_thread(started.wait, 3), "request never reached dictionary loading"
            release.set()  # unrelated event-loop work can run during the read

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost") as api:
            response, _ = await asyncio.gather(api.post("/v1/audio/speech", json={
                "model": "fake-norm-route", "input": "cats", "voice": "pron-voice",
                "language": "en", "response_format": "wav",
            }), observe())
            assert response.status_code == 200, response.text

    asyncio.run(render())
    assert released_while_reading == [True]


def test_queue_native_batch_uses_the_saved_voice_dictionary_without_fallback(
    batch_env, fake_engine, saved_pronunciations, monkeypatch,
):
    batch, make_job = batch_env
    db = importlib.import_module("core.db")
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET kind='design', instruct='neutral' WHERE id='pron-voice'")
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-norm-route")
    monkeypatch.setenv(batch.BATCH_WIDTH_ENV, "8")

    async def transcript(*_args, **_kwargs):
        return ([dict(id=str(i), start=float(i), end=float(i + 1),
                      text="Dr. Smith has 2 cats. [[cats|cats]]") for i in range(2)], "en")

    monkeypatch.setattr("services.asr_backend.run_transcribe_guarded", transcript)
    batches = []

    def generate_batch(_self, texts, **_kwargs):
        batches.append(list(texts))
        return [torch.ones(1, 24000) * 0.1 for _ in texts]

    monkeypatch.setattr(fake_engine, "generate_batch", generate_batch)
    job = make_job("saved-native-queue")
    job["voice_id"] = "pron-voice"
    asyncio.run(batch._run_batch_pipeline(job["id"], job))
    assert "en" in job.get("outputs", {}), job
    assert batches == [["Professor Smith has two felines. cats"] * 2]
    assert not fake_engine.calls
    assert not job.get("warnings")


def test_disabling_normalization_does_not_disable_saved_pronunciations(
    client, fake_engine, saved_pronunciations, monkeypatch,
):
    monkeypatch.setenv("OMNIVOICE_TEXT_NORMALIZATION", "0")
    response = client.post("/v1/audio/speech", json={
        "model": "fake-norm-route", "voice": "pron-voice", "language": "en",
        "input": "Dr. Smith has 2 cats. [[cats|cats]]", "response_format": "wav",
    })
    assert response.status_code == 200, response.text
    assert fake_engine.calls[-1][0] == "Dr. Smith has 2 felines. cats"
