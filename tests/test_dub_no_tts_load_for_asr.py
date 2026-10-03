"""A dub must not load the TTS model just to throw it away.

The transcribe preflight in ``dub_core`` called ``get_model()`` — pulling the full
~3 GB TTS core into memory — for exactly one reason: to read a preloaded
``_asr_pipe`` off it. But that attribute is only ever set by
``OmniVoice.from_pretrained`` under ``OMNIVOICE_PRELOAD_TTS_ASR``, which is off by
default ("intentionally false", model_manager.should_preload_tts_asr).

So in the default configuration every dub:
  1. loaded the TTS core,
  2. harvested ``None`` from it,
  3. had ``offload_tts_for_asr()`` free it again a few lines later — on unified
     memory (Apple Silicon) that offload is a full UNLOAD (#1119),
  4. and then cold-reloaded the very same model in dub_generate (~8 s).

Load → unload → reload, once per dub, for an attribute that was always None.
These tests pin the model load to the only case that can actually use it.
"""
from __future__ import annotations

import asyncio
import struct
import uuid
import wave
from pathlib import Path

import pytest


def _make_wav(path: Path, seconds: float = 0.5, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


class _FakeASR:
    id = "fake"

    def ensure_loaded(self):
        pass

    def transcribe(self, path, *, word_timestamps=True):
        return {"chunks": [{"text": "hi", "timestamp": (0.0, 0.5)}],
                "segments": [], "language": "en"}

    def unload(self):
        pass


@pytest.fixture()
def dub(tmp_path, monkeypatch):
    """dub_core rebound to an isolated data dir, with a job seeded and every
    heavy dependency stubbed. Yields (module, job_id, load_counter)."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))

    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    from api.routers import dub_core as dc
    importlib.reload(dc)

    calls = {"get_model": 0, "order": []}

    async def _counting_get_model():
        calls["get_model"] += 1
        raise AssertionError(
            "dub loaded the TTS core model during the ASR preflight — it only has "
            "an _asr_pipe to harvest when OMNIVOICE_PRELOAD_TTS_ASR is set"
        )

    monkeypatch.setattr(dc, "get_model", _counting_get_model)
    monkeypatch.setattr(dc, "get_diarization_pipeline", lambda *a, **k: None)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: calls["order"].append("offload"))
    monkeypatch.setattr(dc, "restore_tts_after_asr", lambda *a, **k: None)
    monkeypatch.setattr("services.asr_backend.asr_model_missing_error", lambda: None)
    monkeypatch.setattr(
        "services.asr_backend.load_active_asr_backend",
        lambda *a, **k: calls["order"].append("load-asr") or _FakeASR(),
    )

    job_id = f"test_{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / "dub_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    audio = job_dir / "audio.wav"
    vocals = job_dir / "vocals.wav"
    _make_wav(audio)
    _make_wav(vocals)
    dc._dub_jobs[job_id] = {
        "video_path": str(job_dir / "original.mp4"),
        "audio_path": str(audio), "vocals_path": str(vocals),
        "no_vocals_path": None, "duration": 1.0, "filename": "f.mp4",
        "segments": None, "dubbed_tracks": {}, "scene_cuts": [],
    }
    return dc, job_id, calls


def _drain(dc, job_id) -> str:
    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for c in resp.body_iterator:
            parts.append(c.decode() if isinstance(c, bytes) else c)
        return "".join(parts)
    return asyncio.run(_collect())


def test_transcribe_does_not_load_the_tts_model(dub):
    """The regression: default config must never touch the TTS core to transcribe."""
    dc, job_id, calls = dub
    body = _drain(dc, job_id)
    assert calls["get_model"] == 0, "dub loaded the TTS core it was about to free"
    assert calls["order"].index("offload") < calls["order"].index("load-asr")
    # And the stream still worked — we didn't just break the preflight.
    assert "error" not in body or "segment" in body or "done" in body


def test_transcribe_still_loads_the_model_when_preload_is_on(dub, monkeypatch):
    """The one case the load is for: an _asr_pipe actually exists to harvest."""
    dc, job_id, calls = dub
    monkeypatch.setattr(dc, "should_preload_tts_asr", lambda: True)

    class _Model:
        _asr_pipe = object()

    async def _get_model():
        calls["get_model"] += 1
        return _Model()

    monkeypatch.setattr(dc, "get_model", _get_model)
    _drain(dc, job_id)
    assert calls["get_model"] == 1


def test_preflight_error_does_not_leave_asr_on_vocals_unbound(dub, monkeypatch):
    """`asr_on_vocals` was assigned only inside the model-loaded branch but read
    from _gen_body — an early preflight bail raised NameError over the real error."""
    dc, job_id, calls = dub
    dc._dub_jobs[job_id]["audio_path"] = "/nonexistent/audio.wav"
    dc._dub_jobs[job_id]["vocals_path"] = "/nonexistent/vocals.wav"
    body = _drain(dc, job_id)
    assert "NameError" not in body
    assert "No audio available" in body


@pytest.mark.parametrize('outcome', ['complete', 'deleted', 'replaced', 'failed', 'cancelled', 'edited'])
def test_transcription_publishes_private_source_without_replacing_tracks(dub, monkeypatch, outcome):
    from fastapi import HTTPException

    dc, job_id, _ = dub
    job = dc._dub_jobs[job_id]
    original_segments = [{'id': 0, 'start': 0, 'end': .5, 'text': 'previous source'}]
    job.update(segments=original_segments, source_lang='old', full_transcript='previous source')
    monkeypatch.setattr(dc, 'should_preload_tts_asr', lambda: False)
    monkeypatch.setattr(dc, '_save_job', lambda *_: None)
    async def guarded(_pool, transcribe, **_kwargs):
        result = transcribe()  # actual ASR closure with the fixture's fake engine
        assert job['segments'] == original_segments
        assert job['source_lang'] == 'old', 'ASR exposed source metadata before commit'
        assert job['full_transcript'] == 'previous source'
        # A render can complete during ASR; keep its newly committed track.
        job['dubbed_tracks']['en'] = {'path': 'concurrently-published.wav'}
        if outcome == 'deleted':
            dc._dub_jobs.pop(job_id)
            monkeypatch.setattr(dc, '_get_job', lambda _: None)
        elif outcome == 'replaced':
            dc._dub_jobs[job_id] = {'segments': [], 'replacement': True}
        elif outcome == 'edited':
            job['segments'] = [dict(original_segments[0], text='imported correction')]
        elif outcome == 'failed':
            raise RuntimeError('injected ASR completion failure')
        elif outcome == 'cancelled':
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(dc, 'run_transcribe_guarded', guarded)
    if outcome == 'complete':
        result = asyncio.run(dc.dub_transcribe(job_id))
        assert result['source_lang'] == 'en'
        assert result['full_transcript'] == 'hi'
        assert job['segments'] != original_segments
        assert job['dubbed_tracks']['en']['path'] == 'concurrently-published.wav'
    else:
        error_type = asyncio.CancelledError if outcome == 'cancelled' else HTTPException
        with pytest.raises(error_type) as error:
            asyncio.run(dc.dub_transcribe(job_id))
        if outcome in ('deleted', 'replaced'):
            assert error.value.status_code == 404
        if outcome == 'edited':
            assert error.value.status_code == 409
            assert job['segments'][0]['text'] == 'imported correction'
        else:
            assert job['segments'] == original_segments
        assert job['source_lang'] == 'old'
        if outcome == 'replaced':
            assert dc._dub_jobs[job_id] == {'segments': [], 'replacement': True}


def test_cancelled_transcription_waiting_for_publication_keeps_source_private(dub, monkeypatch):
    import threading

    dc, job_id, _ = dub
    job = dc._dub_jobs[job_id]
    job['segments'] = [{'id': 0, 'start': 0, 'end': .5, 'text': 'original'}]
    original = dc._transcription_source(job)
    release = threading.Event()
    async def exercise():
        loop = asyncio.get_running_loop()
        held, contended, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_lock = dc.dub_pipeline._dub_jobs_lock
        class Lock:
            def __enter__(self):
                if not original_lock.acquire(blocking=False):
                    loop.call_soon_threadsafe(contended.set)
                    original_lock.acquire()
                return self
            def __exit__(self, *_):
                original_lock.release()
        monkeypatch.setattr(dc.dub_pipeline, '_dub_jobs_lock', Lock())
        publish = dc._publish_transcription
        def observed_publish(*args):
            try:
                return publish(*args)
            finally:
                loop.call_soon_threadsafe(finished.set)
        monkeypatch.setattr(dc, '_publish_transcription', observed_publish)
        def holder():
            with original_lock:
                loop.call_soon_threadsafe(held.set)
                assert release.wait(2)
        holding = loop.run_in_executor(None, holder)
        await held.wait()
        saving = asyncio.create_task(dc._save_transcription(
            job_id, job, original, {'segments': [{'text': 'cancelled ASR'}]},
        ))
        try:
            await asyncio.wait_for(contended.wait(), 10)
            saving.cancel()
            rendezvous = asyncio.Event()
            loop.call_soon(rendezvous.set)
            await rendezvous.wait()
            assert not saving.done(), "cancelled ASR detached its queued source worker"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await saving
            await asyncio.wait_for(finished.wait(), 10)
            await holding
            assert dc._transcription_source(job) == original
        finally:
            release.set()
            await asyncio.gather(saving, holding, return_exceptions=True)
    asyncio.run(exercise())


def test_cancellation_waits_for_admitted_transcription_commit(dub, monkeypatch):
    import threading

    dc, job_id, _ = dub
    job = dc._dub_jobs[job_id]
    original = dc._transcription_source(job)
    release = threading.Event()
    completed = []
    async def exercise():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        def save(*_):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            completed.append(True)
        monkeypatch.setattr(dc, '_save_job', save)
        saving = asyncio.create_task(dc._save_transcription(
            job_id, job, original, {'full_transcript': 'committed transcript'},
        ))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            saving.cancel()
            rendezvous = asyncio.Event()
            loop.call_soon(rendezvous.set)
            await rendezvous.wait()
            assert not saving.done(), 'ASR commit worker escaped cancellation'
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await saving
            assert completed == [True]
            assert job['full_transcript'] == 'committed transcript'
        finally:
            release.set()
            await asyncio.gather(saving, return_exceptions=True)
    asyncio.run(exercise())
