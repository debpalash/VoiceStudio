"""Visual-analysis workers own their frames through completion and cancellation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import tempfile
import threading

import pytest


@pytest.fixture
def analysis(tmp_path, monkeypatch):
    from services import ffmpeg_utils, video_context

    original = tempfile.mkdtemp
    owned = []

    def directory(suffix=None, prefix=None, dir=None):
        path = original(suffix or "", prefix or "", str(tmp_path))
        owned.append(Path(path))
        return path

    monkeypatch.setattr(video_context.tempfile, "mkdtemp", directory)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "fixture-ffmpeg")

    def extract(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"fixture frame")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", extract)
    monkeypatch.setattr(video_context, "_analyse_frame_basic", lambda path: {
        "brightness": "normal", "mood": "calm", "complexity": "simple"})
    return video_context, owned, tmp_path


@pytest.mark.asyncio
async def test_success_removes_the_whole_owned_frame_directory(analysis):
    module, owned, root = analysis
    unrelated = root / "unrelated.jpg"
    unrelated.write_bytes(b"keep")
    context = await module.analyse_video("clip.mp4", [{"start": 0, "end": 2}])
    assert context.global_mood == "calm"
    assert context.frame_analyses
    assert owned and all(not path.exists() for path in owned)
    assert unrelated.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_analysis_exception_removes_frames_and_directory(analysis, monkeypatch):
    module, owned, _ = analysis

    def fail(*args):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(module, "_build_segment_context", fail)
    with pytest.raises(RuntimeError, match="analysis failed"):
        await module.analyse_video("clip.mp4", [{"start": 0, "end": 2}])
    assert owned and all(not path.exists() for path in owned)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["extraction", "analysis"])
async def test_cancelled_request_leaves_cleanup_with_the_native_worker(analysis, monkeypatch, phase):
    module, owned, _ = analysis
    entered, release = threading.Event(), threading.Event()
    original = subprocess.run if phase == "extraction" else module._analyse_frame_basic

    def blocked(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    if phase == "extraction":
        monkeypatch.setattr(subprocess, "run", blocked)
    else:
        monkeypatch.setattr(module, "_analyse_frame_basic", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(module, "_analysis_pool", pool)
        task = asyncio.create_task(module.analyse_video("clip.mp4", [{"start": 0, "end": 2}]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # A cancelled waiter must not delete files while the worker owns them.
            assert owned and any(list(path.glob("*.jpg")) for path in owned)
        finally:
            release.set()
        # A one-worker queue makes this a completion fence, without sleeps.
        await asyncio.get_running_loop().run_in_executor(pool, lambda: None)
    assert all(not path.exists() for path in owned)
