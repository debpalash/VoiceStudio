"""Long recordings retain a finite budget through routes and child engines."""
import asyncio
import io
import wave
import base64
import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _default_budgets(monkeypatch):
    for name in ("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR",
                 "OMNIVOICE_ASR_RECV_TIMEOUT_S", "OMNIVOICE_MCP_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)


def _recording(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8)
        audio.writeframes(b"\0\0" * int(seconds * 8))
    return out.getvalue()


@pytest.mark.usefixtures("asr_model_installed")
@pytest.mark.parametrize("endpoint,field", [
    ("/transcribe", "audio"), ("/v1/audio/transcriptions", "file"),
])
def test_long_upload_reaches_the_guard_with_a_duration_budget(monkeypatch, endpoint, field):
    from fastapi.testclient import TestClient
    from services import asr_backend as asr
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500, raising=False)

    class Backend:
        id = "test-asr"

        def transcribe(self, path, **kwargs):
            return {"text": "meeting", "language": "en", "segments": []}

    monkeypatch.setattr(asr, "get_capture_asr_backend", lambda **kw: Backend())
    monkeypatch.setattr(asr, "load_active_asr_backend", lambda **kw: Backend())
    waits = []

    class ClockObserver:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def wait_for(self, future, timeout):
            waits.append(timeout)
            return await asyncio.wait_for(future, timeout)

    monkeypatch.setattr(asr, "asyncio", ClockObserver())
    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post(endpoint, files={field: ("meeting.wav", _recording(1500), "audio/wav")})
    assert response.status_code == 200, response.text
    assert waits[-1] == pytest.approx(3000, abs=1)


def test_isolated_engine_does_not_keep_its_old_ten_minute_ceiling(tmp_path, monkeypatch):
    from services.subprocess_asr import IsolatedFasterWhisperBackend
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500, raising=False)

    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    backend = IsolatedFasterWhisperBackend()
    waits = []
    monkeypatch.setattr("services.model_manager.running_on_gpu_pool", lambda: True)
    monkeypatch.setattr(backend, "_spawn", lambda: None)
    monkeypatch.setattr(backend, "_send", lambda msg: None)

    def receive(timeout):
        waits.append(timeout)
        return {"op": "segments", "result": {"text": "meeting", "segments": []}}

    monkeypatch.setattr(backend, "_recv_with_timeout", receive)
    backend.transcribe(str(path))
    assert waits == [pytest.approx(3000, abs=1)]


def test_large_finite_request_budget_does_not_overflow_the_engine_lock(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from services.asr_backend import run_transcribe_guarded
    from services.subprocess_asr import IsolatedFasterWhisperBackend

    budget = 2 * threading.TIMEOUT_MAX
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", str(budget))
    monkeypatch.setattr("services.model_manager.running_on_gpu_pool", lambda: True)
    engine = IsolatedFasterWhisperBackend()
    monkeypatch.setattr(engine, "_spawn", lambda: None)
    monkeypatch.setattr(engine, "_send", lambda message: None)
    waits = []
    monkeypatch.setattr(engine, "_recv_with_timeout", lambda timeout: waits.append(timeout) or {"op": "segments"})
    with ThreadPoolExecutor(max_workers=1) as pool:
        asyncio.run(run_transcribe_guarded(pool, lambda: engine.transcribe("audio.wav")))
    assert waits == [pytest.approx(budget)]  # only the lock primitive is bounded


def test_cancelled_lock_wait_does_not_start_a_late_transcription(monkeypatch):
    import threading
    from time import monotonic
    from concurrent.futures import ThreadPoolExecutor
    from services.inference_cancellation import InferenceCancellation
    from services.asr_backend import ASRTimeoutError
    from services.subprocess_asr import IsolatedFasterWhisperBackend

    locked, attempted = threading.Lock(), threading.Event()
    locked.acquire()

    class Lock:
        def acquire(self, *, timeout):
            attempted.set()
            return locked.acquire(timeout=timeout)

        def release(self):
            locked.release()

    monkeypatch.setattr("services.model_manager.running_on_gpu_pool", lambda: True)
    engine = IsolatedFasterWhisperBackend()
    engine._lock = Lock()
    monkeypatch.setattr(engine, "_spawn", lambda: pytest.fail("cancelled work must not start a child"))
    cancellation = InferenceCancellation(deadline=monotonic() + 10)

    def transcribe():
        with cancellation.activate():
            return engine.transcribe("audio.wav")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(transcribe)
        try:
            assert attempted.wait(2)
            cancellation.cancel()
            with pytest.raises(ASRTimeoutError):
                future.result(timeout=2)
        finally:
            locked.release()


@pytest.mark.parametrize("queued", [False, True])
def test_cancelled_conversion_probe_releases_upload_after_its_reader_exits(tmp_path, monkeypatch, queued):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers import voice_convert
    from api.routers.generation import _TempReferenceLease
    from services import asr_backend

    path = tmp_path / "upload.wav"
    path.write_bytes(_recording(1500))
    lease = _TempReferenceLease(str(path))
    started, finish, cleaned = threading.Event(), threading.Event(), threading.Event()
    submitted, occupied = threading.Event(), threading.Event()
    acquire = lease.acquire

    def acquire_tracked():
        release = acquire()

        def done():
            release()
            cleaned.set()

        return done

    def probe(path):
        started.set()
        assert finish.wait(5)
        assert not cleaned.is_set()
        return 1500

    monkeypatch.setattr(lease, "acquire", acquire_tracked)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda **kwargs: None)
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", probe)

    class ProbePool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            submitted.set()
            return future

    async def scenario():
        work = asyncio.create_task(voice_convert._transcribe_source(str(path), source_lease=lease))
        assert await asyncio.to_thread((submitted if queued else started).wait, 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        lease.finish_request()
        if queued:
            assert cleaned.is_set()
            assert not path.exists()
            assert not started.is_set()
            return
        assert path.exists()
        assert not cleaned.is_set()
        finish.set()
        assert await asyncio.to_thread(cleaned.wait, 2)
        assert not path.exists()

    with ProbePool(max_workers=1) as pool:
        monkeypatch.setattr("services.model_manager._cpu_pool", pool)
        if queued:
            def occupy():
                occupied.set()
                finish.wait(5)

            pool.submit(occupy)
            assert occupied.wait(2)
            submitted.clear()
        try:
            asyncio.run(scenario())
        finally:
            finish.set()


def test_caller_owned_upload_drains_its_cancelled_probe_before_cleanup(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from services.asr_backend import run_transcribe_guarded

    path = tmp_path / "upload.wav"
    path.write_bytes(_recording(1500))
    started, finish = threading.Event(), threading.Event()

    def probe(path):
        started.set()
        assert finish.wait(5)
        assert tmp_path.joinpath("upload.wav").exists()
        return 1500

    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", probe)

    with ThreadPoolExecutor(max_workers=1) as pool:
        async def request():
            try:
                await run_transcribe_guarded(pool, lambda: pytest.fail("abandoned ASR must not start"), audio_path=str(path))
            finally:
                path.unlink()

        async def scenario():
            work = asyncio.create_task(request())
            assert await asyncio.to_thread(started.wait, 2)
            work.cancel()
            await asyncio.sleep(0)
            assert not work.done()
            assert path.exists()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert not path.exists()

        try:
            asyncio.run(scenario())
        finally:
            finish.set()


def test_probe_queue_cannot_wait_past_the_finite_unknown_duration_floor(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from services.asr_backend import ASRTimeoutError, run_transcribe_guarded

    occupied, finish, cleaned = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "0.05")
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: pytest.fail("expired probe must not start"))

    def occupy():
        occupied.set()
        finish.wait(5)

    with ThreadPoolExecutor(max_workers=1) as cpu, ThreadPoolExecutor(max_workers=1) as gpu:
        monkeypatch.setattr("services.model_manager._cpu_pool", cpu)
        cpu.submit(occupy)
        assert occupied.wait(2)

        async def scenario():
            with pytest.raises(ASRTimeoutError):
                await asyncio.wait_for(run_transcribe_guarded(
                    gpu, lambda: pytest.fail("expired ASR must not start"),
                    audio_path="upload.wav", on_abandon=cleaned.set,
                ), timeout=1)
            assert cleaned.is_set()

        try:
            asyncio.run(scenario())
        finally:
            finish.set()


@pytest.mark.usefixtures("asr_model_installed")
def test_stream_model_preflight_spends_the_file_deadline(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers import dub_core as dub
    from services import asr_backend as asr

    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    monkeypatch.setitem(dub._dub_jobs, "slow-preflight", {"audio_path": str(path), "segments": None})
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "0.1")
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", "0")
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500)
    monkeypatch.setattr(dub, "should_preload_tts_asr", lambda: False)
    entered, finish = threading.Event(), threading.Event()

    def preflight():
        entered.set()
        assert finish.wait(5)
        return None

    monkeypatch.setattr(asr, "asr_model_missing_error", preflight)
    monkeypatch.setattr(asr, "load_active_asr_backend", lambda **kwargs: pytest.fail("expired load must not start"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(dub, "_cpu_pool", pool)

        async def scenario():
            response = await dub.dub_transcribe_stream("slow-preflight", per_segment_refs=False)

            async def collect():
                parts = []
                async for part in response.body_iterator:
                    parts.append(part.decode() if isinstance(part, bytes) else part)
                return "".join(parts)

            body = await asyncio.wait_for(collect(), timeout=1)
            assert entered.is_set()
            assert "transcription_timeout" in body
            assert "event: done" in body

        try:
            asyncio.run(scenario())
        finally:
            finish.set()


@pytest.mark.usefixtures("asr_model_installed")
def test_stream_model_loading_spends_the_file_deadline_and_drains_before_restore(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers import dub_core as dub
    from services import asr_backend as asr

    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    monkeypatch.setitem(dub._dub_jobs, "slow-file-load", {"audio_path": str(path), "segments": None})
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "0.1")
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", "0")
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500)
    monkeypatch.setattr(dub, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(dub, "offload_tts_for_asr", lambda: None)
    monkeypatch.setattr(dub, "ASR_LOAD_KEEPALIVE_S", 0.01)
    entered, finish, unloaded, restored = (threading.Event() for _ in range(4))

    class Backend:
        id = "whole-file"
        requires_full_audio_for_speaker_consistency = True

        def unload(self):
            unloaded.set()

    def load(**kwargs):
        entered.set()
        assert finish.wait(5)
        return Backend()

    def restore():
        assert unloaded.is_set()
        restored.set()

    monkeypatch.setattr(asr, "load_active_asr_backend", load)
    monkeypatch.setattr(dub, "restore_tts_after_asr", restore)

    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(dub, "_gpu_pool", pool)

        async def collect():
            response = await dub.dub_transcribe_stream("slow-file-load", per_segment_refs=False)
            parts = []
            async for part in response.body_iterator:
                parts.append(part.decode() if isinstance(part, bytes) else part)
            return "".join(parts)

        async def scenario():
            body = await asyncio.wait_for(collect(), timeout=1)
            assert entered.is_set()
            assert "transcription_timeout" in body
            assert "event: done" in body
            assert not unloaded.is_set()
            assert not restored.is_set()
            finish.set()
            assert await asyncio.to_thread(unloaded.wait, 2)
            assert await asyncio.to_thread(restored.wait, 2)

        try:
            asyncio.run(scenario())
        finally:
            finish.set()


@pytest.mark.usefixtures("neutral_asr_host")
def test_cpp_engine_does_not_keep_its_old_five_minute_ceiling(tmp_path, monkeypatch):
    from engines.whisper_cpp import backend as cpp
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500, raising=False)

    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    monkeypatch.setattr(cpp, "locate_binary", lambda: tmp_path / "whisper-cli")
    monkeypatch.setattr(cpp, "resolve_model", lambda: tmp_path / "ggml-base.bin")
    monkeypatch.setattr(cpp, "_ensure_wav_16k", lambda path, scratch: path)
    waits = []

    def recognize(command, output, timeout):
        waits.append(timeout)
        return {"transcription": []}

    monkeypatch.setattr(cpp, "run_transcription", recognize)
    cpp.WhisperCppBackend().transcribe(str(path))
    assert waits == [pytest.approx(3000, abs=1)]


@pytest.mark.parametrize("duration,expected", [(None, 300), (1, 300), (1500, 3000),
                                              (-1, 300), (float("nan"), 300), (float("inf"), 300)])
def test_shared_budget_and_mcp_wait_agree(duration, expected):
    from core.asr_budget import transcribe_budget_s
    from mcp_server import _post_timeout_s
    assert transcribe_budget_s(duration) == expected
    assert _post_timeout_s("transcribe", duration_s=duration) == expected + 30


@pytest.mark.parametrize("raw", ["soon", "nan", "inf", "-inf", "-2", ""])
def test_invalid_settings_have_the_same_finite_fallback(monkeypatch, raw):
    from core.asr_budget import transcribe_budget_s
    from mcp_server import _post_timeout_s
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", raw)
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", raw)
    assert transcribe_budget_s(1500) == 3000
    assert _post_timeout_s("transcribe", duration_s=1500) == 3030


def test_explicit_floor_factor_and_disabled_scaling(monkeypatch):
    from core.asr_budget import transcribe_budget_s
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "900")
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", "3")
    assert transcribe_budget_s(1500) == 4500
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", "0")
    assert transcribe_budget_s(1500) == 900


@pytest.mark.parametrize("limit,overall", [(1, 5), (5, 1)])
def test_explicit_stage_limit_and_overall_deadline_both_apply(limit, overall):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic
    from services.asr_backend import run_transcribe_guarded
    from services.asr_timing import remaining_s
    from services.inference_cancellation import current_cancellation

    def recognize():
        return remaining_s(current_cancellation().deadline)

    with ThreadPoolExecutor(max_workers=1) as pool:
        actual = asyncio.run(run_transcribe_guarded(
            pool, recognize, timeout=limit, deadline=monotonic() + overall,
        ))
    assert 0 < actual <= 1


def test_duration_probe_uses_real_media_metadata(tmp_path):
    from services.ffmpeg_utils import find_ffprobe, probe_audio_duration
    if not find_ffprobe():
        pytest.skip("ffprobe not installed")
    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    assert probe_audio_duration(str(path)) == pytest.approx(1500)


def test_duration_probe_is_local_bounded_and_best_effort(tmp_path, monkeypatch):
    import subprocess
    from services import ffmpeg_utils as ff
    path = tmp_path / "upload.mp3"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(ff, "find_ffprobe", lambda: "ffprobe")
    calls = []

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{"duration": "1500"}]}))

    monkeypatch.setattr(ff.subprocess, "run", probe)
    assert ff.probe_audio_duration(str(path)) == 1500
    command, kwargs = calls[0]
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert kwargs["timeout"] == 10
    assert ff.probe_audio_duration("https://example.invalid/private.mp3") is None
    assert len(calls) == 1

    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 10)

    monkeypatch.setattr(ff.subprocess, "run", expired)
    assert ff.probe_audio_duration(str(path)) is None


@pytest.mark.parametrize("fixed_wait", [None, 45])
def test_actual_mcp_upload_passes_the_duration_based_wait(monkeypatch, fixed_wait):
    import httpx
    import mcp_server
    from fastapi import FastAPI

    backend = FastAPI()

    @backend.post("/transcribe")
    async def transcribe():
        return {"text": "meeting"}

    waits = []
    post = httpx.AsyncClient.post

    async def observe(self, url, **kwargs):
        waits.append(kwargs.get("timeout", self.timeout.read))
        return await post(self, url, **kwargs)

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "post", observe)
    if fixed_wait is not None:
        monkeypatch.setenv("OMNIVOICE_MCP_TIMEOUT_S", str(fixed_wait))
        monkeypatch.setattr(mcp_server, "_uploaded_audio_duration", lambda raw: pytest.fail("fixed wait needs no probe"))
    else:
        monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500, raising=False)
    server = mcp_server.create_mcp_server(app=backend)
    asyncio.run(server.call_tool("transcribe", {"audio_base64": base64.b64encode(_recording(1500)).decode()}))
    assert waits == [fixed_wait or 3030]


@pytest.mark.parametrize("failure", ["create", "write"])
def test_mcp_duration_estimation_survives_scratch_storage_failure(monkeypatch, failure):
    import tempfile
    from pathlib import Path
    from mcp_server import _uploaded_audio_duration

    def unavailable(*args, **kwargs):
        raise OSError("scratch storage unavailable")

    if failure == "create":
        monkeypatch.setattr(tempfile, "TemporaryDirectory", unavailable)
    else:
        monkeypatch.setattr(Path, "write_bytes", unavailable)
    assert _uploaded_audio_duration(_recording(1500)) is None


@pytest.mark.usefixtures("asr_model_installed")
def test_installed_capture_fallback_reports_exhausted_deadline(monkeypatch):
    from fastapi.testclient import TestClient
    from services import asr_backend as asr

    class SilentSherpa:
        id = "sherpa-onnx"
        spec = SimpleNamespace(id="silent-model")

        def transcribe(self, path, **kwargs):
            return {"text": "", "segments": [], "language": "en"}

    real = asr.run_transcribe_guarded

    async def guard(pool, fn, **kwargs):
        if kwargs.get("what") == "Dictation fallback":
            if kwargs.get("on_abandon"):
                kwargs["on_abandon"]()
            raise asr.ASRTimeoutError("installed fallback exceeded the deadline")
        return await real(pool, fn, **kwargs)

    monkeypatch.setattr(asr, "get_capture_asr_backend", lambda **kwargs: SilentSherpa())
    monkeypatch.setattr(asr, "run_transcribe_guarded", guard)
    from main import app
    response = TestClient(app, client=("127.0.0.1", 50000)).post(
        "/transcribe", files={"audio": ("voice.wav", _recording(2), "audio/wav")},
    )
    assert response.status_code == 504, response.text
    assert "fallback exceeded the deadline" in response.json()["detail"]


@pytest.mark.usefixtures("asr_model_installed")
def test_legacy_dub_uses_the_recording_budget(tmp_path, monkeypatch):
    from api.routers import dub_core as dub
    from services import asr_backend as asr
    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    job = {"audio_path": str(path), "duration": 1500, "segments": None}
    monkeypatch.setitem(dub._dub_jobs, "long-recording", job)
    monkeypatch.setattr(dub, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(dub, "_ensure_diarisation_accepted", lambda: None)
    monkeypatch.setattr(dub, "_save_job", lambda *args: None)
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: 1500, raising=False)
    budgets = []
    real = asr.run_transcribe_guarded

    async def guarded(pool, fn, **kwargs):
        # Keep the real request guard; recognition/postprocessing is unrelated.
        def recognize():
            from services.inference_cancellation import current_cancellation
            from services.asr_timing import remaining_s
            budgets.append(remaining_s(current_cancellation().deadline))
            return []
        return await real(pool, recognize, **kwargs)

    monkeypatch.setattr(dub, "run_transcribe_guarded", guarded)
    asyncio.run(dub.dub_transcribe("long-recording"))
    assert budgets == [pytest.approx(3000, abs=1)]


def test_batch_uses_the_extracted_duration(tmp_path, monkeypatch):
    import subprocess
    from api.routers import batch
    from services import asr_backend as asr
    from services.inference_cancellation import current_cancellation
    from services.asr_timing import remaining_s
    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("services.ffmpeg_utils.validate_media_source", lambda path: None)
    monkeypatch.setattr("services.ffmpeg_utils.require_audio_stream", lambda path: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=b"", stderr=b"Duration: 00:25:00.00"))
    job = {"video_path": str(tmp_path / "input.mp4"), "langs": [], "voice_id": None, "status": "running"}
    budgets = []
    real = asr.run_transcribe_guarded

    async def guarded(pool, fn, **kwargs):
        def recognize():
            budgets.append(remaining_s(current_cancellation().deadline))
            return [], "en"
        return await real(pool, recognize, **kwargs)

    monkeypatch.setattr(asr, "run_transcribe_guarded", guarded)
    asyncio.run(batch._run_batch_pipeline("long-recording", job))
    assert budgets == [pytest.approx(3000, abs=1)]


def test_remote_asr_format_fallback_does_not_renew_the_deadline(tmp_path, monkeypatch):
    from services import asr_backend as asr, asr_timing
    from services.inference_cancellation import InferenceCancellation
    monkeypatch.setattr(asr, "resolve_openai_compat_asr_base_url", lambda: "http://127.0.0.1:8765/v1")
    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    clock = [100.0]
    monkeypatch.setattr(asr_timing, "monotonic", lambda: clock[0])
    waits = []

    def create(**kwargs):
        waits.append(kwargs.get("timeout"))
        if len(waits) == 1:
            clock[0] = 103.0
            raise ValueError("verbose JSON unavailable")
        return SimpleNamespace(text="meeting")

    engine = asr.OpenAICompatASRBackend()
    endpoint = SimpleNamespace(create=create)
    monkeypatch.setattr(engine, "_client", lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=endpoint)))
    with InferenceCancellation(deadline=105.0).activate():
        engine.transcribe(str(path))
    assert waits == [5.0, 2.0]


@pytest.mark.usefixtures("neutral_asr_host")
@pytest.mark.parametrize("kind", ["cpp", "isolated", "rocm-proxy"])
def test_child_engine_spends_the_remaining_guard_budget(tmp_path, monkeypatch, kind):
    from services.inference_cancellation import InferenceCancellation
    from services import asr_timing

    clock = [100.0]
    monkeypatch.setattr(asr_timing, "monotonic", lambda: clock[0])
    monkeypatch.setattr("services.ffmpeg_utils.probe_audio_duration", lambda path: pytest.fail("guard already owns the deadline"))
    path = tmp_path / "meeting.wav"
    path.write_bytes(_recording(1500))
    waits = []
    if kind == "cpp":
        from engines.whisper_cpp import backend as cpp
        monkeypatch.setattr(cpp, "locate_binary", lambda: tmp_path / "whisper-cli")
        monkeypatch.setattr(cpp, "resolve_model", lambda: tmp_path / "ggml-base.bin")

        def convert(path, scratch):
            clock[0] = 103.0
            return path

        monkeypatch.setattr(cpp, "_ensure_wav_16k", convert)
        monkeypatch.setattr(cpp, "run_transcription", lambda cmd, output, timeout: waits.append(timeout) or {})
        engine = cpp.WhisperCppBackend()
    else:
        from services.subprocess_asr import IsolatedFasterWhisperBackend
        if kind == "rocm-proxy":
            from core.windows_rocm.asr_proxy import RocmASRProxy
            engine = RocmASRProxy("local-model", "cpu")
        else:
            engine = IsolatedFasterWhisperBackend()
        monkeypatch.setattr("services.model_manager.running_on_gpu_pool", lambda: True)
        monkeypatch.setattr(engine, "_spawn", lambda: clock.__setitem__(0, 103.0))
        monkeypatch.setattr(engine, "_send", lambda message: None)
        monkeypatch.setattr(engine, "_recv_with_timeout", lambda timeout: waits.append(timeout) or {"op": "segments"})

    with InferenceCancellation(deadline=105.0).activate():
        engine.transcribe(str(path))
    assert waits == [2.0]
