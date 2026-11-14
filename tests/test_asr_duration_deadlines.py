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
    assert waits == [pytest.approx(3000, abs=1)]


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
