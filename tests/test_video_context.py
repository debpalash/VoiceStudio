"""Pillow-backed video-context analysis stays deterministic across upgrades."""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from PIL import Image


def _analyse(frame_path):
    module = importlib.import_module("services.video_context")
    return module._analyse_frame_basic(str(frame_path))


def test_pillow_runtime_floor_is_declared():
    # TOML is UTF-8; a bare read_text() decodes in the locale code page, which
    # cannot read pyproject.toml's em dashes on a Chinese, Japanese or Korean
    # Windows, so this module could not run there at all.
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    requirements = [Requirement(item) for item in project["project"]["dependencies"]]
    pillow = next(req for req in requirements if req.name.lower() == "pillow")
    assert any(
        spec.operator == ">=" and spec.version == "12.1.0"
        for spec in pillow.specifier
    )
    assert pillow.specifier.contains("12.1.0")
    assert not pillow.specifier.contains("12.0.99")


def test_keyframe_extraction_spawns_the_resolved_ffmpeg(tmp_path, monkeypatch):
    """Frame extraction must run the binary `find_ffmpeg()` resolved.

    `shutil.which("ffmpeg")` only finds a system install; imageio-ffmpeg — the
    app's default source — ships its binary as `ffmpeg-<platform>-v<version>`,
    so this gate skipped extraction on hosts where the app's own ffmpeg was
    resolvable the whole time (the same class as #1256).
    """
    import subprocess

    from services import ffmpeg_utils

    module = importlib.import_module("services.video_context")
    bundled = str(tmp_path / "bundle" / "ffmpeg-linux-x86_64-v7.1")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: bundled)

    spawned = []

    def _record(cmd, **kw):
        spawned.append(cmd)
        Image.new("RGB", (8, 8), (10, 10, 10)).save(cmd[-1], format="JPEG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", _record)

    frames = module._extract_keyframes("clip.mp4", [0.0, 1.5], str(frames_dir))

    assert [ts for ts, _ in frames] == [0.0, 1.5]
    assert spawned and all(cmd[0] == bundled for cmd in spawned)


def test_keyframe_extraction_skips_when_no_ffmpeg_resolves(tmp_path, monkeypatch):
    import subprocess

    from services import ffmpeg_utils

    module = importlib.import_module("services.video_context")
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)
    spawned = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: spawned.append(cmd))

    assert module._extract_keyframes("clip.mp4", [0.0], str(tmp_path)) == []
    assert not spawned


# ── #2566: the frame directory never outlives the analysis worker ────────────


def _frame_fixture(tmp_path, monkeypatch, run=None):
    """One-worker pool, frame dirs created under ``tmp_path/frames`` and an
    ffmpeg stand-in that writes a real JPEG (or calls ``run`` first)."""
    import subprocess
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    from services import ffmpeg_utils

    module = importlib.import_module("services.video_context")
    root = tmp_path / "frames"
    root.mkdir()
    real_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(
        module.tempfile, "mkdtemp", lambda **kw: real_mkdtemp(dir=str(root), **kw)
    )
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")

    def _ffmpeg(cmd, **kw):
        if run is not None:
            run(cmd)
        Image.new("RGB", (8, 8), (10, 10, 10)).save(cmd[-1], format="JPEG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", _ffmpeg)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(module, "_analysis_pool", pool)
    return module, root, pool


_SEGMENTS = [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}, {"start": 2.0, "end": 3.0}]


def test_analysis_removes_its_frame_directory(tmp_path, monkeypatch):
    import asyncio

    module, root, pool = _frame_fixture(tmp_path, monkeypatch)
    ctx = asyncio.run(module.analyse_video("clip.mp4", _SEGMENTS))
    pool.shutdown(wait=True)

    assert ctx.to_dict()["frame_count"] == 3
    assert list(root.iterdir()) == []


def test_analysis_failure_removes_its_frames(tmp_path, monkeypatch):
    import asyncio

    import pytest

    module, root, pool = _frame_fixture(tmp_path, monkeypatch)

    def _boom(_path):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(module, "_analyse_frame_basic", _boom)
    with pytest.raises(RuntimeError, match="analysis failed"):
        asyncio.run(module.analyse_video("clip.mp4", _SEGMENTS))
    pool.shutdown(wait=True)

    assert list(root.iterdir()) == []


def test_cancelled_analysis_stops_and_removes_its_frames(tmp_path, monkeypatch):
    import asyncio
    import threading

    started, release = threading.Event(), threading.Event()
    calls = []

    def _block_first(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            started.set()
            assert release.wait(10)

    module, root, pool = _frame_fixture(tmp_path, monkeypatch, run=_block_first)

    async def _cancel_mid_extraction():
        task = asyncio.create_task(module.analyse_video("clip.mp4", _SEGMENTS))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("analysis was not cancelled")

    asyncio.run(_cancel_mid_extraction())
    release.set()
    pool.submit(lambda: None).result(timeout=10)  # the one worker has finished

    assert len(calls) == 1
    assert list(root.iterdir()) == []


def _save_jpeg(tmp_path, name: str, image: Image.Image):
    path = tmp_path / name
    image.save(path, format="JPEG", quality=100, subsampling=0)
    return path


def test_basic_analysis_decodes_and_resizes_real_jpegs(tmp_path):
    dark = _save_jpeg(tmp_path, "dark.jpg", Image.new("RGB", (16, 12), (20, 20, 20)))
    bright = _save_jpeg(tmp_path, "bright.jpg", Image.new("RGB", (640, 480), (230, 230, 230)))

    dark_result = _analyse(dark)
    bright_result = _analyse(bright)

    assert dark_result == {
        "brightness": "dark", "mood": "calm", "complexity": "simple",
        "avg_luminance": 20.0, "avg_saturation": 0.0,
    }
    assert bright_result == {
        "brightness": "bright", "mood": "calm", "complexity": "simple",
        "avg_luminance": 230.0, "avg_saturation": 0.0,
    }


def test_basic_analysis_preserves_color_and_edge_classes(tmp_path):
    vivid = _save_jpeg(tmp_path, "vivid.jpg", Image.new("RGB", (320, 240), (255, 0, 0)))
    stripes = Image.new("RGB", (320, 240))
    stripes.putdata([
        (255, 255, 255) if x % 2 else (0, 0, 0)
        for _y in range(240)
        for x in range(320)
    ])
    action = _save_jpeg(tmp_path, "action.jpg", stripes)

    assert _analyse(vivid)["mood"] == "vivid"
    assert _analyse(action)["complexity"] == "action"


def test_basic_analysis_degrades_cleanly_for_malformed_image(tmp_path):
    malformed = tmp_path / "frame.jpg"
    malformed.write_bytes(b"not an image")

    assert _analyse(malformed) == {
        "brightness": "unknown", "mood": "unknown", "complexity": "unknown",
    }
