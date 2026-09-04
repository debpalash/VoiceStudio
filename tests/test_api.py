"""
VoiceStudio API — Unit Test Suite
Tests all roadmap features: TaskManager, scene detection, lip-sync scoring,
export endpoints (VTT, SRT, MP3, segments ZIP, stems ZIP), streaming TTS.

Uses FastAPI's TestClient (synchronous httpx) to avoid needing a running server.
GPU/model inference is mocked so tests run on any machine in seconds.
"""

import io
import os
import json
import uuid
import wave
import struct
import time
import pytest
import asyncio

# Patch environment before importing api
os.environ.setdefault("OMNIVOICE_MODEL", "test")

from unittest.mock import patch, MagicMock, AsyncMock
import torch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_wav_bytes(duration_s=1.0, sample_rate=24000, channels=1) -> bytes:
    """Create a valid WAV file in memory for testing."""
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    buf.seek(0)
    return buf.read()


def make_audio_tensor(duration_s=1.0, sample_rate=24000) -> torch.Tensor:
    """Create a torch audio tensor of the given duration."""
    n_samples = int(duration_s * sample_rate)
    return torch.zeros(1, n_samples)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _mock_model():
    """Prevent real model loading across the entire test session."""
    mock = MagicMock()
    mock.sampling_rate = 24000
    mock.generate.return_value = [make_audio_tensor(1.0)]

    import main as api_mod
    api_mod.model = mock
    # `_init_db` was absorbed into the FastAPI lifespan in the refactor; the
    # TestClient below triggers that lifespan on first request, so we just
    # import init_db directly here for tests that need tables before any HTTP
    # call (legacy fixture behaviour).
    from core.db import init_db
    init_db()
    yield mock


@pytest.fixture()
def client():
    """Create a TestClient for the FastAPI app (no server needed).

    `client=("127.0.0.1", 50000)` makes `request.client.host` resolve to a
    loopback address — required because `backend/api/routers/system.py` is
    now gated by a router-level `require_admin` dependency. Tests that
    deliberately exercise the non-loopback rejection path build their own
    plain `TestClient(app)` (which defaults to host='testclient').
    """
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def seeded_job(client):
    """Create a fake dub job with segments, tracks, and WAV files on disk."""
    import main as api_mod

    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(__import__('core.config', fromlist=['DUB_DIR']).DUB_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # Write fake segment WAVs
    for i in range(3):
        seg_path = os.path.join(job_dir, f"seg_{i}.wav")
        with open(seg_path, "wb") as f:
            f.write(make_wav_bytes(0.5))

    # Write a fake dubbed track
    track_path = os.path.join(job_dir, "dubbed_en.wav")
    with open(track_path, "wb") as f:
        f.write(make_wav_bytes(2.0))

    # Write a fake background audio
    bg_path = os.path.join(job_dir, "no_vocals.wav")
    with open(bg_path, "wb") as f:
        f.write(make_wav_bytes(2.0))

    # Write a fake video
    video_path = os.path.join(job_dir, "original.mp4")
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 100)

    job = {
        "video_path": video_path,
        "audio_path": os.path.join(job_dir, "audio.wav"),
        "vocals_path": os.path.join(job_dir, "vocals.wav"),
        "no_vocals_path": bg_path,
        "duration": 3.0,
        "filename": "test_video.mp4",
        "segments": [
            {"id": "a1", "start": 0.0, "end": 1.0, "text": "Hello world", "speaker_id": "Speaker 1"},
            {"id": "a2", "start": 1.0, "end": 2.0, "text": "How are you", "speaker_id": "Speaker 1"},
            {"id": "a3", "start": 2.0, "end": 3.0, "text": "Goodbye", "speaker_id": "Speaker 2"},
        ],
        "dubbed_tracks": {
            "en": {"path": track_path, "language": "English", "language_code": "en"},
        },
        "scene_cuts": [1.5],
    }

    __import__('services.dub_pipeline', fromlist=['_dub_jobs'])._dub_jobs[job_id] = job
    yield job_id, job
    # Cleanup
    __import__('services.dub_pipeline', fromlist=['_dub_jobs'])._dub_jobs.pop(job_id, None)


# ═══════════════════════════════════════════════════════════════════════
# TASK MANAGER TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestTaskManager:
    """Tests for the centralized async batch task queue."""

    def test_task_manager_init(self):
        from core.tasks import TaskManager
        tm = TaskManager()
        assert tm.active_tasks == {}
        assert tm.queue is None

    @pytest.mark.asyncio
    async def test_add_task_creates_entry(self):
        from core.tasks import TaskManager
        tm = TaskManager()
        tm._init_queue()

        async def dummy():
            pass

        await tm.add_task("t1", "test", dummy)
        assert "t1" in tm.active_tasks
        assert tm.active_tasks["t1"]["status"] == "pending"
        assert tm.active_tasks["t1"]["type"] == "test"

    @pytest.mark.asyncio
    async def test_worker_processes_task(self):
        from core.tasks import TaskManager
        tm = TaskManager()
        results = []

        async def work():
            results.append("done")

        await tm.add_task("t2", "test", work)

        # Run worker for a brief period
        worker = asyncio.create_task(tm.worker())
        await asyncio.sleep(0.2)
        worker.cancel()

        assert "done" in results
        assert tm.active_tasks["t2"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_worker_handles_failure(self):
        from core.tasks import TaskManager
        tm = TaskManager()

        async def fail():
            raise ValueError("boom")

        await tm.add_task("t3", "test", fail)

        worker = asyncio.create_task(tm.worker())
        await asyncio.sleep(0.2)
        worker.cancel()

        assert tm.active_tasks["t3"]["status"] == "failed"
        assert "boom" in tm.active_tasks["t3"]["error"]


# ═══════════════════════════════════════════════════════════════════════
# SRT EXPORT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSRTExport:
    def test_srt_export(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/srt/{job_id}")
        assert res.status_code == 200
        content = res.text
        assert "1\n" in content
        assert "Hello world" in content
        assert "-->" in content

    def test_srt_404_missing_job(self, client):
        res = client.get("/dub/srt/nonexistent")
        assert res.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# VTT EXPORT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestVTTExport:
    def test_vtt_export(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/vtt/{job_id}")
        assert res.status_code == 200
        content = res.text
        assert content.startswith("WEBVTT")
        assert "Hello world" in content
        assert "-->" in content
        # VTT uses periods not commas
        assert "." in content.split("-->")[0]

    def test_vtt_format_correct(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/vtt/{job_id}")
        lines = res.text.strip().split("\n")
        assert lines[0] == "WEBVTT"
        # Find a timestamp line
        ts_lines = [l for l in lines if "-->" in l]
        assert len(ts_lines) == 3
        # Verify format: HH:MM:SS.mmm
        for ts in ts_lines:
            start, end = ts.split("-->")
            assert "." in start.strip()
            assert "." in end.strip()

    def test_vtt_404_missing_job(self, client):
        res = client.get("/dub/vtt/nonexistent")
        assert res.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# PER-SEGMENT ZIP EXPORT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSegmentZipExport:
    def test_segments_zip_export(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/export-segments/{job_id}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"

        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        assert len(names) == 3
        # Verify naming convention: 001_0.00-1.00_Speaker1.wav
        assert names[0].startswith("001_")
        assert names[0].endswith(".wav")
        assert "Speaker" in names[0]

    def test_segments_zip_404(self, client):
        res = client.get("/dub/export-segments/nonexistent")
        assert res.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# STEM EXPORT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStemExport:
    def test_stems_zip_export(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/export-stems/{job_id}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"

        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        assert any("vocals" in n for n in names)
        assert any("background" in n for n in names)

    def test_stems_404_no_tracks(self, client):
        import main as api_mod
        job_id = "stems_test"
        __import__('services.dub_pipeline', fromlist=['_dub_jobs'])._dub_jobs[job_id] = {
            "segments": [], "dubbed_tracks": {}, "filename": "t.mp4",
            "video_path": "", "duration": 0,
        }
        res = client.get(f"/dub/export-stems/{job_id}")
        assert res.status_code == 400
        __import__('services.dub_pipeline', fromlist=['_dub_jobs'])._dub_jobs.pop(job_id, None)


# ═══════════════════════════════════════════════════════════════════════
# SCENE-AWARE DUBBING TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSceneAwareDubbing:
    def test_scene_cuts_stored(self, seeded_job):
        _, job = seeded_job
        assert "scene_cuts" in job
        assert isinstance(job["scene_cuts"], list)

    def test_scene_split_algorithm(self):
        """Test the segment splitting logic directly."""
        segments = [
            {"id": "s1", "start": 0.0, "end": 3.0, "text": "Hello world this is a test sentence", "speaker_id": "Speaker 1"},
        ]
        scene_cuts = [1.5]

        # Run the algorithm inline (mirrors api.py logic)
        sorted_cuts = sorted(scene_cuts)
        new_segments = []
        for s in segments:
            s_start = s["start"]
            s_end = s["end"]
            valid_cuts = [c for c in sorted_cuts if c > s_start + 0.2 and c < s_end - 0.2]

            if not valid_cuts:
                new_segments.append(s)
            else:
                curr_start = s_start
                curr_text = s["text"]
                total_dur = s_end - s_start

                for cut in valid_cuts:
                    ratio = (cut - curr_start) / max(total_dur, 0.01)
                    split_idx = int(len(curr_text) * ratio)
                    space_idx = curr_text.rfind(' ', 0, split_idx + 5)
                    if space_idx != -1 and space_idx > split_idx - 10:
                        split_idx = space_idx

                    part_text = curr_text[:split_idx].strip()
                    curr_text = curr_text[split_idx:].strip()

                    if part_text:
                        new_seg = dict(s)
                        new_seg["start"] = round(curr_start, 2)
                        new_seg["end"] = round(cut, 2)
                        new_seg["text"] = part_text
                        new_seg["id"] = "new1"
                        new_segments.append(new_seg)

                    curr_start = cut
                    total_dur = s_end - curr_start

                if curr_text:
                    new_seg = dict(s)
                    new_seg["start"] = round(curr_start, 2)
                    new_seg["end"] = round(s_end, 2)
                    new_seg["text"] = curr_text
                    new_seg["id"] = "new2"
                    new_segments.append(new_seg)

        assert len(new_segments) == 2
        assert new_segments[0]["end"] == 1.5
        assert new_segments[1]["start"] == 1.5
        # Text should be split
        combined = new_segments[0]["text"] + " " + new_segments[1]["text"]
        assert combined == "Hello world this is a test sentence"

    def test_no_split_when_cut_too_close_to_edge(self):
        """Cuts within 0.2s of segment edges should NOT split."""
        segments = [{"id": "s1", "start": 0.0, "end": 1.0, "text": "Short", "speaker_id": "Speaker 1"}]
        scene_cuts = [0.1, 0.9]  # Both within 0.2s padding

        sorted_cuts = sorted(scene_cuts)
        new_segments = []
        for s in segments:
            valid_cuts = [c for c in sorted_cuts if c > s["start"] + 0.2 and c < s["end"] - 0.2]
            if not valid_cuts:
                new_segments.append(s)

        assert len(new_segments) == 1  # No split occurred


# ═══════════════════════════════════════════════════════════════════════
# LIP-SYNC SCORING TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestLipSyncScoring:
    def test_sync_ratio_calculation(self):
        """Test the sync ratio math directly."""
        seg_duration = 2.0  # original segment is 2 seconds
        sample_rate = 24000

        # Generated audio is exactly 2 seconds → ratio = 1.0
        audio_tensor = make_audio_tensor(2.0, sample_rate)
        generated_dur = audio_tensor.shape[-1] / sample_rate
        sync_ratio = round(generated_dur / max(seg_duration, 0.01), 3)
        assert sync_ratio == 1.0

    def test_sync_ratio_fast(self):
        """Generated audio shorter than original → ratio < 1."""
        seg_duration = 2.0
        audio_tensor = make_audio_tensor(1.5, 24000)
        generated_dur = audio_tensor.shape[-1] / 24000
        sync_ratio = round(generated_dur / max(seg_duration, 0.01), 3)
        assert sync_ratio == 0.75

    def test_sync_ratio_slow(self):
        """Generated audio longer than original → ratio > 1."""
        seg_duration = 2.0
        audio_tensor = make_audio_tensor(3.0, 24000)
        generated_dur = audio_tensor.shape[-1] / 24000
        sync_ratio = round(generated_dur / max(seg_duration, 0.01), 3)
        assert sync_ratio == 1.5

    def test_sync_ratio_thresholds(self):
        """Verify color-coded classification logic."""
        def classify(ratio):
            if 0.95 <= ratio <= 1.05:
                return "green"
            elif ratio > 1.25:
                return "red"
            else:
                return "yellow"

        assert classify(1.0) == "green"
        assert classify(0.95) == "green"
        assert classify(1.05) == "green"
        assert classify(0.8) == "yellow"
        assert classify(1.2) == "yellow"
        assert classify(1.3) == "red"
        assert classify(1.5) == "red"


# ═══════════════════════════════════════════════════════════════════════
# SRT/VTT TIMESTAMP FORMATTING TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestTimestampFormatting:
    def test_srt_time_format(self):
        from api.routers.dub_export import _format_srt_time
        assert _format_srt_time(0.0) == "00:00:00,000"
        assert _format_srt_time(61.5) == "00:01:01,500"
        assert _format_srt_time(3661.123) == "01:01:01,123"

    def test_vtt_time_format(self):
        from api.routers.dub_export import _format_vtt_time
        assert _format_vtt_time(0.0) == "00:00:00.000"
        assert _format_vtt_time(61.5) == "00:01:01.500"
        # SRT uses comma, VTT uses period
        assert "." in _format_vtt_time(1.0)


# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINT VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestAPIEndpoints:
    def test_model_status(self, client):
        res = client.get("/model/status")
        assert res.status_code == 200
        data = res.json()
        assert "loaded" in data
        assert "status" in data

    def test_sysinfo(self, client):
        res = client.get("/sysinfo")
        assert res.status_code == 200
        data = res.json()
        assert "cpu" in data
        assert "ram" in data

    def test_dub_tracks(self, client, seeded_job):
        job_id, _ = seeded_job
        res = client.get(f"/dub/tracks/{job_id}")
        assert res.status_code == 200
        data = res.json()
        assert "tracks" in data
        assert "en" in data["tracks"]

    def test_tasks_stream_404(self, client):
        res = client.get("/tasks/stream/nonexistent")
        assert res.status_code == 404

    def test_dub_download_404(self, client):
        res = client.get("/dub/download/nonexistent")
        assert res.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# STREAMING TTS TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStreamingTTS:
    @pytest.mark.xfail(
        reason="TTS generation path routes through tts_backend engine registry "
               "now, not services.model_manager.get_model directly; patch target "
               "moved. Re-enable after updating to mock services.tts_backend.",
        strict=False,
    )
    def test_generate_returns_streaming_response(self, client):
        """POST /generate should return streamed WAV with metadata headers."""
        with patch("services.model_manager.get_model") as mock_get:
            mock_model = MagicMock()
            mock_model.sampling_rate = 24000
            mock_model.generate.return_value = [make_audio_tensor(1.0)]

            async def _get():
                return mock_model
            mock_get.return_value = _get()

            import main as api_mod
            api_mod.model = mock_model

            res = client.post("/generate", data={
                "text": "Hello world",
                "num_step": "4",
                "guidance_scale": "2.0",
                "speed": "1.0",
                "denoise": "true",
                "t_shift": "0.1",
                "position_temperature": "5.0",
                "class_temperature": "0.0",
                "layer_penalty_factor": "5.0",
                "postprocess_output": "true",
            })
            assert res.status_code == 200
            assert res.headers.get("content-type") == "audio/wav"
            assert res.headers.get("x-audio-id") is not None
            assert res.headers.get("x-gen-time") is not None
            assert res.headers.get("x-audio-duration") is not None
            # Verify it's valid WAV
            assert len(res.content) > 44  # WAV header is 44 bytes minimum


# ---------------------------------------------------------------------------
# /system/set-env loopback-origin guard (260518-ivy security fix)
# ---------------------------------------------------------------------------

def test_set_env_rejects_non_loopback():
    """A TestClient that does NOT override `client=` sets
    `request.client.host = 'testclient'` (non-loopback). The router-level
    `require_admin` dependency must return 403 and must NOT mutate
    os.environ. NOTE: the project-wide `client` fixture is now built with a
    loopback override so most tests see protected routes — this test
    instantiates its own plain client to exercise the rejection path."""
    from fastapi.testclient import TestClient
    from main import app

    sentinel = "__set_env_should_not_be_set__"
    # Ensure HF_TOKEN does not currently equal the sentinel
    original = os.environ.get("HF_TOKEN")
    os.environ.pop("HF_TOKEN", None)
    try:
        non_loopback_client = TestClient(app)  # default client.host = 'testclient'
        res = non_loopback_client.post("/system/set-env", json={"key": "HF_TOKEN", "value": sentinel})
        assert res.status_code == 403
        assert "loopback" in res.json().get("detail", "").lower()
        # Guard must short-circuit before the os.environ mutation
        assert os.environ.get("HF_TOKEN") != sentinel
    finally:
        if original is None:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = original


def test_set_env_allows_loopback():
    """A TestClient explicitly constructed with client=('127.0.0.1', ...) must pass
    the loopback gate, mutate os.environ, and return the documented payload."""
    from fastapi.testclient import TestClient
    from main import app

    loopback_client = TestClient(app, client=("127.0.0.1", 50000))
    original = os.environ.get("HF_TOKEN")
    os.environ.pop("HF_TOKEN", None)
    try:
        res = loopback_client.post(
            "/system/set-env",
            json={"key": "HF_TOKEN", "value": "hf_loopback_ok"},
        )
        assert res.status_code == 200
        # `shadowed` (#1787) is additive — HF_TOKEN is never persisted via
        # prefs.json (it goes through huggingface_hub.login() instead), so it
        # is never shadow-tracked and always reports False here.
        assert res.json() == {"key": "HF_TOKEN", "set": True, "shadowed": False}
        assert os.environ.get("HF_TOKEN") == "hf_loopback_ok"
    finally:
        if original is None:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = original


def test_server_mode_remote_without_api_key_cannot_set_executable_path(monkeypatch, tmp_path):
    """GHAS #506: bare Docker exposure must not become an executable setter."""
    from fastapi.testclient import TestClient
    from main import app

    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"not actually executable")
    original = os.environ.get("FFMPEG_PATH")
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    try:
        response = TestClient(app, client=("172.17.0.1", 50000)).post(
            "/system/set-env",
            json={"key": "FFMPEG_PATH", "value": str(executable)},
        )
        assert response.status_code == 403
        assert os.environ.get("FFMPEG_PATH") == original
    finally:
        if original is None:
            os.environ.pop("FFMPEG_PATH", None)
        else:
            os.environ["FFMPEG_PATH"] = original


def test_set_env_never_accepts_executable_path_even_with_admin_key(monkeypatch, tmp_path):
    """Executable selection exists only behind native IPC, never this API."""
    from fastapi.testclient import TestClient
    from main import app

    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"not actually executable")
    original = os.environ.get("FFMPEG_PATH")
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    try:
        response = TestClient(app, client=("172.17.0.1", 50000)).post(
            "/system/set-env",
            headers={"authorization": "Bearer s3cret"},
            json={"key": "FFMPEG_PATH", "value": str(executable)},
        )
        assert response.status_code == 400
        assert os.environ.get("FFMPEG_PATH") == original
    finally:
        if original is None:
            os.environ.pop("FFMPEG_PATH", None)
        else:
            os.environ["FFMPEG_PATH"] = original


def test_set_env_loopback_still_validates_allowlist():
    """Even on the loopback path, keys outside the allow-list must return 400 —
    the new guard must NOT bypass the existing allow-list enforcement."""
    from fastapi.testclient import TestClient
    from main import app

    loopback_client = TestClient(app, client=("127.0.0.1", 50000))
    res = loopback_client.post(
        "/system/set-env",
        json={"key": "DISALLOWED", "value": "x"},
    )
    assert res.status_code == 400
    assert "DISALLOWED" not in os.environ


# ---------------------------------------------------------------------------
# Router-wide loopback guard — covers the previously-unprotected siblings
# enumerated in the 260518-ivy loopback quick plan (removed with .planning/;
# see git history). Two representative routes are sampled here:
#   - /clean-audio  (POST, resource-exhaustion vector)
#   - /system/info  (GET,  info-disclosure vector)
# The router-level dependency means every other route on the system router
# is covered by the same gate without per-route tests.
# ---------------------------------------------------------------------------

def test_clean_audio_rejects_non_loopback():
    """`/clean-audio` (POST) was previously reachable from any LAN host —
    a resource-exhaustion vector (uploads + demucs CPU/GPU burn). Now gated
    at the router level."""
    from fastapi.testclient import TestClient
    from main import app

    non_loopback_client = TestClient(app)  # host = 'testclient'
    # Multipart payload doesn't matter — the dependency must short-circuit
    # before the body is parsed. Send empty bytes to keep the test fast.
    res = non_loopback_client.post(
        "/clean-audio",
        files={"audio": ("x.wav", b"", "audio/wav")},
    )
    assert res.status_code == 403
    assert "loopback" in res.json().get("detail", "").lower()


def test_system_info_rejects_non_loopback():
    """`/system/info` (GET) leaks data_dir, outputs_dir, crash_log_path,
    model checkpoints, and other host details to any reachable origin —
    info-disclosure vector. Now gated at the router level."""
    from fastapi.testclient import TestClient
    from main import app

    non_loopback_client = TestClient(app)  # host = 'testclient'
    res = non_loopback_client.get("/system/info")
    assert res.status_code == 403
    assert "loopback" in res.json().get("detail", "").lower()


def test_static_audio_served_with_canonical_mime():
    """Regression: `.wav` files served by `/audio` StaticFiles must come back
    with the IANA-canonical `audio/wav` Content-Type, NOT Python's default
    `audio/x-wav`.

    The `x-` prefix is vendor-experimental and never IANA-registered. macOS
    Chrome/Safari MIME-sniff leniently via CoreAudio so playback works there,
    but Linux Chrome/Firefox (FFmpeg) and Android Chrome (ExoPlayer) strictly
    honor the declared type and treat `audio/x-wav` as download-only — which
    silently broke the play button in the web app on those platforms.
    """
    from pathlib import Path
    from fastapi.testclient import TestClient
    from main import app
    from core.config import OUTPUTS_DIR

    # Drop a wav into the real OUTPUTS_DIR — the mount serves from there.
    tmp_wav = Path(OUTPUTS_DIR) / f"__mime_test_{uuid.uuid4().hex[:8]}.wav"
    tmp_wav.write_bytes(make_wav_bytes(0.1))
    try:
        client = TestClient(app)
        res = client.get(f"/audio/{tmp_wav.name}")
        assert res.status_code == 200, res.text
        ct = res.headers.get("content-type", "")
        assert ct == "audio/wav", (
            f"Expected audio/wav (IANA canonical), got {ct!r}. "
            f"Linux/Android browsers reject audio/x-wav as download-only."
        )
    finally:
        tmp_wav.unlink(missing_ok=True)
