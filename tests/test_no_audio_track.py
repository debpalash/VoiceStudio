"""A media file with no audio stream gets one actionable error everywhere.

Owner report (Linux, Electron): loading a video-only MP4 in Dub failed at
stage ``extract`` with ffmpeg's raw dump — "FFmpeg exited with code 234 …
Output file does not contain any stream … Error opening output files: Invalid
argument". Every audio-extract site (dub ingest, batch, ASR decode, transcribe
uploads, clone references, gallery imports) now raises ``NoAudioTrackError``
with a VoiceStudio sentence and the ``NO_AUDIO_TRACK`` failure class instead.
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.failure import (
    NO_AUDIO_TRACK_MESSAGE,
    NoAudioTrackError,
    build_failure,
    classify,
    is_no_audio_stream_stderr,
    is_terminal_failure_topic,
    no_audio_track_detail,
    public_hint_for_topic,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")

# The owner's diagnostic, trimmed to the lines ffmpeg prints for this case.
OWNER_STDERR = (
    "FFmpeg exited with code 234: Input #0, mov,mp4,m4a,3gp,3g2,mj2, from "
    "'[redacted path]':\n  Duration: 00:00:05.00, start: 0.000000, bitrate: 38 kb/s\n"
    "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, "
    "320x240, 25 fps\nOutput #0, wav, to '[redacted path]':\n"
    "[out#0/wav @ 0x5f] Output file does not contain any stream\n"
    "Error opening output file [redacted path].\n"
    "Error opening output files: Invalid argument"
)


def _make(path: Path, *, audio: bool) -> Path:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return path


@pytest.fixture
def video_only(tmp_path) -> Path:
    return _make(tmp_path / "silent.mp4", audio=False)


@pytest.fixture
def video_with_audio(tmp_path) -> Path:
    return _make(tmp_path / "speech.mp4", audio=True)


def _raises_no_audio():
    # Match on the stable base class and sentence, not the class object:
    # other suites reload core.failure, which would leave this module's
    # imported NoAudioTrackError a stale, non-matching class.
    return pytest.raises(ValueError, match="has no audio track")


def _events(raw: list[str]) -> list[dict]:
    out = []
    for chunk in raw:
        for line in chunk.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


# ── classification ─────────────────────────────────────────────────────────


def test_owner_diagnostic_is_classified_as_no_audio_track_not_invalid_argument():
    assert is_no_audio_stream_stderr(OWNER_STDERR)
    assert classify(OWNER_STDERR) == "NO_AUDIO_TRACK"
    assert classify(NO_AUDIO_TRACK_MESSAGE) == "NO_AUDIO_TRACK"
    assert classify("Stream map '0:a:0' matches no streams.") == "NO_AUDIO_TRACK"
    # Unrelated failures keep their own classes.
    assert classify("[Errno 22] Invalid argument") == "OS_INVALID_ARGUMENT"
    assert not is_no_audio_stream_stderr("Stream map '0:v:0' matches no streams.")


def test_failure_payload_is_actionable_and_terminal():
    fields = build_failure(NoAudioTrackError(), stage="extract")
    assert fields["reason"] == NO_AUDIO_TRACK_MESSAGE
    assert fields["docs_topic"] == "NO_AUDIO_TRACK"
    assert fields["hint"] == public_hint_for_topic("NO_AUDIO_TRACK")
    assert "234" not in fields["reason"]
    assert is_terminal_failure_topic("NO_AUDIO_TRACK")
    detail = no_audio_track_detail()
    assert detail["docs_topic"] == "NO_AUDIO_TRACK"
    assert detail["code"] == "no_audio_track"


def test_worker_protocol_treats_no_audio_as_terminal():
    from worker import errors

    assert errors.from_reason(NO_AUDIO_TRACK_MESSAGE).error_class.value == "terminal"


def test_http_surfaces_route_no_audio_track_to_a_structured_422():
    source = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text(encoding="utf-8")
    assert "@app.exception_handler(NoAudioTrackError)" in source
    assert "no_audio_track_detail()" in source


# ── probing ────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_probe_tells_audio_from_no_audio(video_only, video_with_audio, tmp_path, monkeypatch):
    from services import ffmpeg_utils

    assert ffmpeg_utils.has_audio_stream(str(video_only)) is False
    assert ffmpeg_utils.has_audio_stream(str(video_with_audio)) is True
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not media at all")
    assert ffmpeg_utils.has_audio_stream(str(junk)) is None
    assert ffmpeg_utils.has_audio_stream(str(tmp_path / "missing.mp4")) is None

    # Without ffprobe the ffmpeg stream listing gives the same answers.
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    assert ffmpeg_utils.has_audio_stream(str(video_only)) is False
    assert ffmpeg_utils.has_audio_stream(str(video_with_audio)) is True
    assert ffmpeg_utils.has_audio_stream(str(junk)) is None


# ── dub ingest (the reported path) ─────────────────────────────────────────


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize("probe", [True, False], ids=["probe", "stderr-fallback"])
async def test_dub_extract_of_video_without_audio_reports_no_audio_track(
    video_only, tmp_path, monkeypatch, probe,
):
    from services import dub_pipeline

    if not probe:
        # ffprobe unavailable: ffmpeg's own wording must still be recognized.
        monkeypatch.setattr(dub_pipeline, "require_audio_stream", lambda _path: None)
        monkeypatch.setattr("services.ffmpeg_utils.has_audio_stream", lambda _path: None)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    raw = [e async for e in dub_pipeline.ingest_pipeline(
        f"noaudio-{probe}", str(job_dir), {"kind": "upload", "path": str(video_only)},
    )]
    error = next(e for e in _events(raw) if e.get("type") == "error")
    assert error["stage"] == "extract"
    assert error["reason"] == NO_AUDIO_TRACK_MESSAGE
    assert error["docs_topic"] == "NO_AUDIO_TRACK"
    assert "code 234" not in error["reason"]
    assert "Invalid argument" not in error["reason"]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_dub_extract_of_video_with_audio_still_extracts(video_with_audio, tmp_path, monkeypatch):
    from services import dub_pipeline

    # Persistence is not under test; keep the job out of the history DB.
    monkeypatch.setattr(dub_pipeline, "find_cached_job", lambda *_a, **_k: None)
    monkeypatch.setattr(dub_pipeline, "put_and_save_job", lambda *_a, **_k: True)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    gen = dub_pipeline.ingest_pipeline(
        "withaudio", str(job_dir), {"kind": "upload", "path": str(video_with_audio)},
    )
    seen = []
    try:
        async for chunk in gen:
            seen.extend(_events([chunk]))
            if any(e.get("type") in ("extract_done", "error") for e in seen):
                break
    finally:
        await gen.aclose()
    assert not [e for e in seen if e.get("type") == "error"], seen
    assert any(e.get("type") == "extract_done" for e in seen)
    assert (job_dir / "audio.wav").stat().st_size > 1000


# ── the rest of the class ──────────────────────────────────────────────────


@needs_ffmpeg
def test_asr_decode_of_video_without_audio_raises_no_audio_track(video_only, video_with_audio):
    from services.asr_backend import _decode_audio_16k_mono

    with _raises_no_audio():
        _decode_audio_16k_mono(str(video_only))
    assert _decode_audio_16k_mono(str(video_with_audio)).size > 8000


@needs_ffmpeg
def test_extract_failure_helper_keeps_other_failures(video_with_audio):
    from services.ffmpeg_utils import raise_for_audio_extract_failure

    with _raises_no_audio():
        raise_for_audio_extract_failure(OWNER_STDERR.encode(), "")
    # A different ffmpeg failure on a file that HAS audio is left to the caller.
    raise_for_audio_extract_failure(b"Unknown encoder 'foo'", str(video_with_audio))


@needs_ffmpeg
def test_gallery_upload_refuses_video_without_audio(video_only, tmp_path, monkeypatch):
    from fastapi import UploadFile

    from api.routers import gallery

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(gallery, "VOICE_GALLERY_DIR", store)
    upload = UploadFile(io.BytesIO(video_only.read_bytes()), filename="silent.mp4")
    with _raises_no_audio():
        asyncio.run(gallery.upload_voice_clip(
            name="x", character="", category="import", description="", audio=upload,
        ))
    assert not list(store.iterdir()), "the refused upload must not be kept"


@needs_ffmpeg
def test_clone_profile_refuses_reference_without_audio(video_only, tmp_path, monkeypatch):
    from fastapi import UploadFile

    from api.routers import profiles

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(profiles, "VOICES_DIR", str(store))
    upload = UploadFile(io.BytesIO(video_only.read_bytes()), filename="ref.mp4")
    with _raises_no_audio():
        asyncio.run(profiles.create_profile(
            name="Silent", ref_audio=upload, ref_text="hello", instruct="",
            language="Auto", seed=None, personality="", kind="clone",
            vd_states=None, image=None,
        ))
    assert not list(store.iterdir()), "the refused reference must not be kept"


@needs_ffmpeg
def test_batch_extract_reports_no_audio_track(video_only, tmp_path, monkeypatch):
    from api.routers import batch

    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    job = {
        "video_path": str(video_only), "langs": ["es"], "status": "running",
        "progress": None,
    }
    with _raises_no_audio():
        asyncio.run(batch._run_batch_pipeline("noaudio", job))


class _SilentVideoASR:
    """An engine whose own decoder fails on a video-only file, as PyAV does."""

    id = "fake-asr"
    spec = None

    def transcribe(self, path, **_kwargs):
        raise RuntimeError("list index out of range")


def _fake_asr(monkeypatch):
    import services.asr_backend as asr

    async def _direct(_executor, fn, **_kwargs):
        return fn()

    monkeypatch.setattr(asr, "asr_model_missing_error", lambda *_a, **_k: None)
    monkeypatch.setattr(asr, "run_transcribe_guarded", _direct)
    monkeypatch.setattr(asr, "load_active_asr_backend", lambda *_a, **_k: _SilentVideoASR())
    monkeypatch.setattr(asr, "get_capture_asr_backend", lambda *_a, **_k: _SilentVideoASR())


@needs_ffmpeg
def test_transcribe_upload_of_video_without_audio_names_the_cause(video_only, monkeypatch):
    from fastapi import UploadFile

    from api.routers.capture import transcribe_audio

    _fake_asr(monkeypatch)
    upload = UploadFile(io.BytesIO(video_only.read_bytes()), filename="silent.mp4")
    with _raises_no_audio():
        asyncio.run(transcribe_audio(audio=upload, language=None, model=None, mode="accurate", refine=None))


@needs_ffmpeg
def test_openai_transcription_of_video_without_audio_is_a_400(video_only, monkeypatch):
    from fastapi import UploadFile

    from api.routers.openai_compat import OpenAIError, _transcribe_request

    _fake_asr(monkeypatch)
    upload = UploadFile(io.BytesIO(video_only.read_bytes()), filename="silent.mp4")
    with pytest.raises(OpenAIError) as excinfo:
        asyncio.run(_transcribe_request(
            task="transcribe", file=upload, model="whisper-1", language=None, prompt=None,
            response_format="json", temperature=None,
        ))
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "no_audio_track"
    assert excinfo.value.detail == NO_AUDIO_TRACK_MESSAGE


def test_openai_keeps_an_engine_raised_no_audio_error_when_the_probe_cannot_run(tmp_path, monkeypatch):
    """CodeRabbit #2308: the ASR decoder's stderr fallback raised NoAudioTrackError,
    the route's probe was undetermined, and the answer became a 500."""
    from fastapi import UploadFile

    import services.ffmpeg_utils as fu
    from api.routers.openai_compat import OpenAIError, _transcribe_request

    class _Raises(_SilentVideoASR):
        def transcribe(self, path, **_kwargs):
            raise NoAudioTrackError()

    _fake_asr(monkeypatch)
    monkeypatch.setattr("services.asr_backend.load_active_asr_backend", lambda *_a, **_k: _Raises())
    monkeypatch.setattr(fu, "has_audio_stream", lambda _path: None)
    upload = UploadFile(io.BytesIO(b"not probeable"), filename="clip.mp4")
    with pytest.raises(OpenAIError) as excinfo:
        asyncio.run(_transcribe_request(
            task="transcribe", file=upload, model="whisper-1", language=None, prompt=None,
            response_format="json", temperature=None,
        ))
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "no_audio_track"
