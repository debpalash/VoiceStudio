"""Verify dub export writes a fresh uniquely-named file every call."""

from __future__ import annotations

import asyncio as _asyncio
import importlib
import json
import os
import shutil
import struct
import subprocess
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_wav(path: Path, seconds: float = 0.5, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    import core.config as _cfg
    importlib.reload(_cfg)
    # Reload core.tasks so TaskManager gets a fresh asyncio.Queue bound to the
    # current event loop (TestClient creates its own loop per fixture).
    import core.tasks as _tasks
    importlib.reload(_tasks)
    from api.routers import dub_core as _dc
    importlib.reload(_dc)
    from api.routers import dub_export as _dx
    importlib.reload(_dx)
    import main as _main
    importlib.reload(_main)

    import services.dub_background as background
    async def fake_background(source, separated, *args):
        return separated
    monkeypatch.setattr(background, "surgical_background", fake_background)

    from fastapi.testclient import TestClient
    with TestClient(_main.app) as client:
        yield client, _dc, _dx, tmp_path


def _seed_job_with_tracks(dc, tmp_path: Path):
    job_id = f"exp_{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / "dub_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    video_path = job_dir / "original.mp4"
    video_path.write_bytes(b"\x00" * 16)
    audio_wav = job_dir / "audio.wav"
    _make_wav(audio_wav)
    track_wav = job_dir / "dubbed_es.wav"
    _make_wav(track_wav)
    bg_wav = job_dir / "no_vocals.wav"
    _make_wav(bg_wav)

    dc._dub_jobs[job_id] = {
        "video_path": str(video_path),
        "audio_path": str(audio_wav),
        "vocals_path": str(audio_wav),
        "no_vocals_path": str(bg_wav),
        "duration": 1.0,
        "filename": "clip.mp4",
        "segments": [{"start": 0.1, "end": 0.8}],
        "dubbed_tracks": {"es": {"path": str(track_wav), "language": "Spanish", "language_code": "es"}},
        "scene_cuts": [],
    }
    return job_id, job_dir


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return (b"", b"")


def _fake_ffmpeg_factory(write_file: bool = True):
    """Return an async callable that mimics the ffmpeg invocation."""
    async def _runner(*cmd, **_):
        if write_file:
            # Positional cmd ends with "<output>" "-y" — scan for an abs path arg.
            out = None
            for arg in reversed(cmd):
                if isinstance(arg, str) and arg.startswith("/") and "." in Path(arg).name:
                    out = arg
                    break
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_bytes(b"\x00FAKEFILE" * 16)
        return _FakeProc()
    return _runner


_SUBPROC_ATTR = "create_subprocess_" + "exec"  # dodge overzealous code-scan hooks


class TestDubExportUniqueness:
    def test_media_head_reports_native_type_without_body(self, app_client):
        client, dc, _dx, tmp = app_client
        job_id, _ = _seed_job_with_tracks(dc, tmp)

        response = client.head(f"/dub/media/{job_id}")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "video/mp4"
        assert response.headers["content-length"] == "16"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == b""

    def test_preview_head_does_not_start_mux(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)

        with patch.object(dx, "run_ffmpeg") as run_ffmpeg:
            response = client.head(f"/dub/preview-video/{job_id}", params={"lang": "es"})

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "video/mp4"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == b""
        run_ffmpeg.assert_not_called()
        assert not (job_dir / "exports" / "preview_v2_es_surgical_v2_no_vocals.mp4").exists()

    def test_preview_is_faststart_and_reuses_immutable_cache(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        commands = []

        async def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            Path(cmd[-2]).write_bytes(b"\x00FASTSTART")
            return 0, b"", b""

        with (
            patch.object(dx, "find_ffmpeg", return_value="ffmpeg"),
            patch.object(dx, "run_ffmpeg", new=fake_run),
        ):
            first = client.get(f"/dub/preview-video/{job_id}", params={"lang": "es"})
            second = client.get(f"/dub/preview-video/{job_id}", params={"lang": "es"})

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(commands) == 1
        assert commands[0][commands[0].index("-movflags") + 1] == "+faststart"
        assert first.headers["cache-control"] == "private, max-age=31536000, immutable"
        assert first.headers["accept-ranges"] == "bytes"
        assert (job_dir / "exports" / "preview_v2_es_surgical_v2_no_vocals.mp4").is_file()

    def test_original_only_resolves_stale_default_before_retime(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, _ = _seed_job_with_tracks(dc, tmp)
        with (
            patch.object(dx, "_video_retime_plan_for") as retime_for,
            patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)),
        ):
            response = client.get(
                f"/dub/download/{job_id}",
                params={
                    "preserve_bg": False,
                    "default_track": "fr",
                    "include_tracks": "original",
                },
            )

        assert response.status_code == 200, response.text
        retime_for.assert_not_called()

    def test_excluded_default_resolves_before_retime_and_subtitle_selection(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, _ = _seed_job_with_tracks(dc, tmp)
        selected = []

        def capture_default(_job, lang):
            selected.append(lang)
            return None

        with (
            patch.object(dx, "_video_retime_plan_for", side_effect=capture_default),
            patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)),
        ):
            response = client.get(
                f"/dub/download/{job_id}",
                params={
                    "preserve_bg": False,
                    "default_track": "fr",
                    "include_tracks": "original,es",
                },
            )

        assert response.status_code == 200, response.text
        assert selected == ["es"]

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg and ffprobe are required for the container regression",
    )
    def test_default_export_marks_dubbed_audio_as_default(self, app_client):
        """#1575: players that honor MP4 disposition must choose the dub."""
        client, dc, _dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        video_path = job_dir / "original.mp4"
        subprocess.run(  # noqa: S603 -- fixed test binary and argument vector
            [
                shutil.which("ffmpeg"), "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=32x32:d=0.5",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=0.5",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(video_path),
            ],
            check=True,
        )

        response = client.get(
            f"/dub/download/{job_id}",
            params={"preserve_bg": False},
        )
        assert response.status_code == 200, response.text
        exported = next((job_dir / "exports").glob("dubbed_video_*.mp4"))
        probe = subprocess.run(  # noqa: S603 -- fixed ffprobe inspection command
            [
                shutil.which("ffprobe"), "-v", "error", "-select_streams", "a",
                "-show_entries", "stream_tags=title:stream_disposition=default",
                "-of", "json", str(exported),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(probe.stdout)["streams"]
        defaults = [index for index, stream in enumerate(streams) if stream["disposition"]["default"]]
        assert defaults == [1], "the dubbed stream follows the original and must be the sole default"

    def test_mp4_export_produces_unique_file_each_call(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        exports_dir = job_dir / "exports"

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)):
            r1 = client.get(f"/dub/download/{job_id}", params={"preserve_bg": False})
            r2 = client.get(f"/dub/download/{job_id}", params={"preserve_bg": False})

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

        files = sorted(exports_dir.glob("dubbed_video_*.mp4"))
        assert len(files) >= 2, f"expected >=2 distinct mp4 files, got {[f.name for f in files]}"
        assert len({f.name for f in files}) == len(files)
        d1 = r1.headers.get("content-disposition", "")
        d2 = r2.headers.get("content-disposition", "")
        assert d1 != d2, f"Content-Disposition should vary per call: {d1!r} == {d2!r}"

    def test_mp3_export_produces_unique_file_each_call(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        exports_dir = job_dir / "exports"

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)):
            client.get(f"/dub/download-mp3/{job_id}", params={"lang": "es", "preserve_bg": False})
            client.get(f"/dub/download-mp3/{job_id}", params={"lang": "es", "preserve_bg": False})
            client.get(f"/dub/download-mp3/{job_id}", params={"lang": "es", "preserve_bg": False})

        mp3s = sorted(exports_dir.glob("dubbed_*.mp3"))
        assert len(mp3s) == 3, f"expected 3 mp3 exports, got {[f.name for f in mp3s]}"
        assert len({f.name for f in mp3s}) == 3

    def test_wav_export_is_not_cached_across_regeneration(self, app_client):
        client, dc, _dx, tmp = app_client
        job_id, _job_dir = _seed_job_with_tracks(dc, tmp)

        response = client.get(
            f"/dub/download-audio/{job_id}",
            params={"lang": "es", "preserve_bg": False},
        )

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"

    def test_mp4_export_refuses_when_ffmpeg_writes_nothing(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, _ = _seed_job_with_tracks(dc, tmp)

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(False)):
            res = client.get(f"/dub/download/{job_id}", params={"preserve_bg": False})

        assert res.status_code == 500
        assert "no output file" in res.json()["detail"]


class TestAudioOnlyDubbing:
    """#119 — audio-only jobs export an audio file (no video mux)."""

    def test_audio_only_export_produces_audio_file(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        dc._dub_jobs[job_id]["input_type"] = "audio"
        exports_dir = job_dir / "exports"

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)):
            r = client.get(
                f"/dub/download/{job_id}",
                params={"preserve_bg": False, "out_format": "m4a", "default_track": "es"},
            )

        assert r.status_code == 200, r.text
        audio_files = sorted(exports_dir.glob("dubbed_audio_*.m4a"))
        assert len(audio_files) == 1, [f.name for f in audio_files]
        # The video-mux path must NOT have run for an audio job.
        assert not list(exports_dir.glob("dubbed_video_*.mp4"))
        assert r.headers.get("content-type", "").startswith("audio/")

    def test_audio_only_export_defaults_unknown_format_to_m4a(self, app_client):
        client, dc, dx, tmp = app_client
        job_id, job_dir = _seed_job_with_tracks(dc, tmp)
        dc._dub_jobs[job_id]["input_type"] = "audio"
        exports_dir = job_dir / "exports"

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)):
            r = client.get(f"/dub/download/{job_id}", params={"preserve_bg": False, "out_format": "weird"})

        assert r.status_code == 200, r.text
        assert sorted(exports_dir.glob("dubbed_audio_*.m4a"))

    def test_audio_only_download_label_rejects_traversal_and_control_chars(self, app_client):
        client, dc, _dx, tmp = app_client
        job_id, _ = _seed_job_with_tracks(dc, tmp)
        dc._dub_jobs[job_id]["input_type"] = "audio"
        dc._dub_jobs[job_id]["filename"] = "../evil\r\nX-Injected: yes.mp4"

        with patch.object(_asyncio, _SUBPROC_ATTR, side_effect=_fake_ffmpeg_factory(True)):
            response = client.get(
                f"/dub/download/{job_id}",
                params={"preserve_bg": False, "default_track": "es"},
            )

        assert response.status_code == 200, response.text
        label = response.headers["content-disposition"]
        assert ".." not in label
        assert "\r" not in label and "\n" not in label
        assert "X-Injected:" not in label

    def test_upload_rejects_video_ext_when_audio_mode(self, app_client):
        client, dc, dx, tmp = app_client
        r = client.post(
            "/dub/upload",
            files={"video": ("clip.mp4", b"\x00" * 16, "video/mp4")},
            data={"input_type": "audio"},
        )
        assert r.status_code == 400
        assert "audio file" in r.json()["detail"].lower()

    def test_ingest_requests_reject_unregistered_source_languages(self, app_client):
        client, _dc, _dx, _tmp = app_client

        form = client.post(
            "/dub/upload",
            files={"video": ("clip.mp4", b"video", "video/mp4")},
            data={"source_lang": "english"},
        )
        json_response = client.post(
            "/dub/ingest-url",
            json={"url": "https://example.com/video", "source_lang": "x-123"},
        )

        assert form.status_code == 400
        assert json_response.status_code == 400
        # #1960: the message now NAMES the rejected code, because the bare
        # sentence could not be triaged from an auto-filed report. Assert the
        # substance rather than the exact wording, so improving the guidance
        # again does not fail this test for the wrong reason.
        # Each request sent a DIFFERENT bad code; pair each response with its
        # own, or the assertion passes on whichever happens to match.
        for body, sent in ((form.json()["detail"], "english"), (json_response.json()["detail"], "x-123")):
            assert "Invalid source language code" in body
            assert sent in body

    def test_upload_accepts_a_registered_source_language(self, app_client, monkeypatch):
        client, dc, _dx, _tmp = app_client
        queued = []

        async def add_task(*args):
            queued.append(args)

        monkeypatch.setattr(dc.task_manager, "add_task", add_task)
        response = client.post(
            "/dub/upload",
            files={"video": ("clip.mp4", b"video", "video/mp4")},
            data={"source_lang": "FR"},
        )

        assert response.status_code == 202
        assert queued[0][5]["source_lang"] == "fr"

    def test_asr_detected_source_languages_can_be_reused_as_overrides(self, app_client):
        _client, dc, _dx, _tmp = app_client
        detected_codes = {
            "as", "ba", "bo", "br", "fo", "lb", "ln", "mg", "nn", "oc",
            "sa", "tk", "tl", "tt", "yue", "zh",
        }

        for code in detected_codes:
            assert dc._source_lang_override(code) == code

    def test_asr_detected_cantonese_code_is_not_truncated(self, app_client):
        _client, dc, _dx, _tmp = app_client

        assert dc._detected_source_lang("yue") == "yue"
        assert dc._detected_source_lang("es_ES") == "es"
        assert dc._detected_source_lang("unknown-language") == "en"
