import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    from core import config, db
    from services import longform_resume, ffmpeg_utils
    monkeypatch.setattr(config, "OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    db.init_db()
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)
    manifest = longform_resume.build_manifest(job_id="old", job_type="story", title="Book",
        plan_chapters=[{"title": "Chapter", "spans": [{"voice_id": "v", "text": "Saved prose", "pause_ms_after": 0, "speed": None}]}],
        params={"default_voice": "v", "voice_map": {"v": "voice"}})
    path = longform_resume.write_manifest(manifest)
    return Path(path), manifest


def test_unconsumed_or_closed_response_preserves_checkpoint(checkpoint):
    from api.routers import audiobook
    from services import longform_resume
    path, manifest = checkpoint
    async def run():
        response = await audiobook.resume_longform("old")
        assert longform_resume.load_manifest_file(str(path)) == manifest
        await response.body_iterator.aclose()
        assert longform_resume.load_manifest_file(str(path)) == manifest
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["replace", "raise"])
def test_failed_replacement_keeps_original(checkpoint, monkeypatch, failure):
    from api.routers import audiobook
    from services import longform_resume
    path, manifest = checkpoint
    if failure == "replace":
        monkeypatch.setattr(longform_resume.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    else:
        monkeypatch.setattr(longform_resume, "write_manifest", lambda *_: (_ for _ in ()).throw(RuntimeError("write failed")))
    async def run():
        response = await audiobook.resume_longform("old")
        assert "ffmpeg" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
    asyncio.run(run())
    assert longform_resume.load_manifest_file(str(path)) == manifest


def test_incomplete_resume_keeps_original_and_replacement_after_close(checkpoint):
    from api.routers import audiobook
    from services import longform_resume
    path, manifest = checkpoint
    async def run():
        response = await audiobook.resume_longform("old")
        assert "ffmpeg" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
    asyncio.run(run())
    replacements = [e for e in longform_resume.scan_resumable() if e["job_id"] != "old"]
    assert len(replacements) == 1
    replacement = longform_resume.load_manifest_file(replacements[0]["manifest_path"])
    assert replacement["plan"] == manifest["plan"]
    assert replacement["params"]["voice_map"] == manifest["params"]["voice_map"]
    assert longform_resume.load_manifest_file(str(path)) == manifest


def test_cancel_during_checkpoint_write_keeps_original(checkpoint, monkeypatch):
    from api.routers import audiobook
    from services import longform_resume
    path, manifest = checkpoint
    def cancel(_fd):
        raise asyncio.CancelledError()
    monkeypatch.setattr(longform_resume, "flush_fd", cancel)
    async def run():
        response = await audiobook.resume_longform("old")
        with pytest.raises(asyncio.CancelledError):
            await anext(response.body_iterator)
        await response.body_iterator.aclose()
    asyncio.run(run())
    assert longform_resume.load_manifest_file(str(path)) == manifest


@pytest.mark.parametrize("failure", ["file-sync", "directory-sync", "unreadable"])
def test_unconfirmed_checkpoint_keeps_original(checkpoint, monkeypatch, failure):
    from api.routers import audiobook
    from services import longform_resume
    path, manifest = checkpoint
    if failure == "file-sync":
        monkeypatch.setattr(longform_resume, "flush_fd", lambda _fd: False)
    elif failure == "directory-sync":
        monkeypatch.setattr(longform_resume, "flush_dir", lambda _path: False)
    else:
        real_load = longform_resume.load_manifest_file
        monkeypatch.setattr(longform_resume, "load_manifest_file", lambda candidate: real_load(candidate) if candidate == str(path) else None)
    async def run():
        response = await audiobook.resume_longform("old")
        assert "ffmpeg" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
    asyncio.run(run())
    assert longform_resume.load_manifest_file(str(path)) == manifest


def test_completed_resume_retires_old_checkpoint_if_replacement_failed(checkpoint, monkeypatch):
    from api.routers import audiobook
    from services import longform_resume, ffmpeg_utils, gpu_gateway
    path, _manifest = checkpoint
    monkeypatch.setattr(longform_resume, "write_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audiobook, "_resolve_default_language", lambda *_args: "en")
    monkeypatch.setattr(gpu_gateway, "decide", lambda *_args: object())
    async def chapter(*_args, **_kwargs):
        audio = path.parent / "cached.wav"
        audio.write_bytes(b"existing chapter")
        return str(audio), 1.0, True, None
    async def mux(command, **_kwargs):
        Path(command[-1]).write_bytes(b"completed output")
    monkeypatch.setattr(audiobook, "_run_chapter", chapter)
    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", mux)
    async def run():
        response = await audiobook.resume_longform("old")
        return [event async for event in response.body_iterator]
    events = asyncio.run(run())
    assert any('"type": "done"' in event for event in events)
    assert not path.exists()


@pytest.mark.parametrize("target", ["file", "directory"])
def test_sync_syscall_failure_does_not_retire_original(checkpoint, monkeypatch, target):
    import os
    import stat
    import sys
    from api.routers import audiobook
    from core import durable_io
    from services import longform_resume
    if target == "directory" and os.name == "nt":
        pytest.skip("Windows does not expose directory fsync handles")
    path, manifest = checkpoint
    calls = []
    def matches(fd):
        return stat.S_ISDIR(os.fstat(fd).st_mode) == (target == "directory")
    real_fsync = durable_io.os.fsync
    def fsync(fd):
        if matches(fd):
            calls.append("fsync")
            raise OSError("injected filesystem sync failure")
        return real_fsync(fd)
    monkeypatch.setattr(durable_io.os, "fsync", fsync)
    if sys.platform == "darwin":
        import fcntl
        real_fcntl = fcntl.fcntl
        def fullsync(fd, operation, *args):
            if operation == fcntl.F_FULLFSYNC and matches(fd):
                calls.append("fullsync")
                raise OSError("injected fullsync failure")
            return real_fcntl(fd, operation, *args)
        monkeypatch.setattr(fcntl, "fcntl", fullsync)
    async def run():
        response = await audiobook.resume_longform("old")
        assert "ffmpeg" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
    asyncio.run(run())
    assert "fsync" in calls
    assert longform_resume.load_manifest_file(str(path)) == manifest


def test_partial_completed_resume_retains_original_when_checkpoint_save_fails(checkpoint, monkeypatch):
    from api.routers import audiobook
    from services import longform_resume, ffmpeg_utils, gpu_gateway
    path, manifest = checkpoint
    manifest["plan"].append({"title": "Failed chapter", "spans": [{"voice_id": "v", "text": "Missing prose", "pause_ms_after": 0, "speed": None}]})
    manifest["total_chapters"] = 2
    assert longform_resume.write_manifest(manifest) == str(path)
    # Manifest persistence uses real files; model execution and mux are isolated.
    def replace(*_args):
        raise OSError("checkpoint disk full")
    monkeypatch.setattr(longform_resume.os, "replace", replace)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audiobook, "_resolve_default_language", lambda *_args: "en")
    monkeypatch.setattr(gpu_gateway, "decide", lambda *_args: object())
    async def chapter(chapter, **_kwargs):
        if chapter.title == "Failed chapter":
            raise RuntimeError("isolated chapter failure")
        audio = path.parent / "cached.wav"
        audio.write_bytes(b"existing chapter")
        return str(audio), 1.0, True, None
    async def mux(command, **_kwargs):
        Path(command[-1]).write_bytes(b"partial output")
    monkeypatch.setattr(audiobook, "_run_chapter", chapter)
    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", mux)
    async def run():
        response = await audiobook.resume_longform("old")
        return [event async for event in response.body_iterator]
    events = asyncio.run(run())
    assert any('"type": "done"' in event and '"failed_chapters": [1]' in event for event in events)
    assert longform_resume.load_manifest_file(str(path)) == manifest
