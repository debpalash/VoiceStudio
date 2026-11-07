import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_extract_error_preserves_failure_after_long_banner(tmp_path, monkeypatch):
    from services import dub_pipeline
    stderr = ("ffmpeg version configuration " * 100 + "\n/home/private/media/secret.mp4: No audio stream found").encode()
    async def run_proc(args):
        return SimpleNamespace(returncode=1), b"", stderr
    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dub_pipeline, "run_proc_factory", lambda job: run_proc)
    events = [event async for event in dub_pipeline.ingest_pipeline(
        "diagnostic", str(tmp_path), {"kind": "upload", "path": str(tmp_path / "input.mp4")},
    )]
    output = "\n".join(events)
    assert "No audio stream found" in output
    assert "code 1" in output
    assert "/home/private/" not in output


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_progress_frames_do_not_bury_native_error(eol):
    from services import dub_pipeline
    bar = "".join(f"\r {i}%|█████| {i}/100 [02:25<00:01, 24.7seconds/s]" for i in range(101))
    stderr = (bar + eol + "Traceback (most recent call last):" + eol + "MemoryError" + eol).encode()
    error = dub_pipeline._media_process_error("Demucs", 1, stderr)
    assert "MemoryError" in error
    assert "Traceback" in error
    assert " 99%" not in error
    assert "100%" in error


def test_empty_native_diagnostic_retains_exit_code():
    from services import dub_pipeline
    assert "code 7" in dub_pipeline._media_process_error("FFmpeg", 7, b"")


@pytest.mark.parametrize("path", ["/mnt/media/private.mp4", "D:\\Media\\private.mp4", "/opt/data/private.mp4"])
def test_native_error_redacts_non_home_command_paths(path):
    from services import dub_pipeline
    error = dub_pipeline._media_process_error("FFmpeg", 1, (path + ": Permission denied").encode(), paths=(path,))
    assert path not in error
    assert "Permission denied" in error


def test_trailing_carriage_return_keeps_the_last_progress_frame():
    from services import dub_pipeline
    error = dub_pipeline._media_process_error("FFmpeg", -9, b"size=1kB\rsize=99kB time=00:01:02\r")
    assert "size=99kB time=00:01:02" in error
    assert "size=1kB" not in error


@pytest.mark.asyncio
async def test_nonprocess_demucs_error_scrubs_private_paths(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    from services import dub_pipeline

    async def extracted(args):
        Path(args[-2]).write_bytes(b"audio")
        return SimpleNamespace(returncode=0), b"", b""

    async def fail_demucs(*args, **kwargs):
        raise OSError("Cannot move /Users/private-person/voices/stem.wav")
        yield  # Make this the same async iterator as the real stderr reader.

    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dub_pipeline, "validate_media_source", lambda *_: None)
    monkeypatch.setattr(dub_pipeline, "require_audio_stream", lambda *_: None)
    monkeypatch.setattr(dub_pipeline, "run_proc_factory", lambda *_: extracted)
    monkeypatch.setattr(dub_pipeline, "find_cached_job", lambda *_: None)
    monkeypatch.setattr(dub_pipeline, "begin_ingest", lambda *_: None)
    monkeypatch.setattr(dub_pipeline, "end_ingest", lambda *_: None)
    monkeypatch.setattr(dub_pipeline, "put_and_save_job", lambda *_, **__: True)
    monkeypatch.setattr(dub_pipeline, "merge_and_save_job", lambda *_, **__: True)
    monkeypatch.setattr(dub_pipeline, "get_best_device", lambda: "cpu")
    monkeypatch.setattr(dub_pipeline, "run_proc_streaming_stderr", fail_demucs)
    events = [event async for event in dub_pipeline.ingest_pipeline(
        "diagnostic-private", str(tmp_path),
        {"kind": "upload", "path": str(tmp_path / "input.wav"), "input_type": "audio"},
    )]
    assert any("demucs_done" in event for event in events)
    warning = "\n".join(record.getMessage() for record in caplog.records if "Demucs failed" in record.getMessage())
    assert "Cannot move" in warning
    assert "private-person" not in warning
    assert "private-person" not in "\n".join(events)
