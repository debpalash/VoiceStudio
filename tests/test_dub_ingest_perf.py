"""Ingest perf paths: one-pass audio extraction and overlapped scene detection.

Both keep an automatic fallback to the previous behaviour.
"""
import asyncio
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SCENE_FILTER = "select='gt(scene,0.3)',showinfo"


def _run_ingest(dp, tmp_path, monkeypatch, *, device, fail_combined=False, order=None):
    from core.db import init_db

    init_db()
    calls = []

    def _factory(job_id):
        async def run_proc(cmd, **kw):
            cmd = [str(c) for c in cmd]
            calls.append(cmd)
            wavs = [c for c in cmd if c.endswith(".wav")]

            class P:
                returncode = 0

            if len(wavs) == 2 and fail_combined:
                P.returncode = 1
                return P(), b"", b"boom"
            for w in wavs:
                open(w, "wb").write(b"RIFF")
            if SCENE_FILTER in cmd:
                if order is not None:
                    order.append("scene_start")
                    await asyncio.sleep(0.05)
                    order.append("scene_end")
                return P(), b"", b"pts_time:1.5 pts_time:3.0"
            return P(), b"", b""
        return run_proc

    async def _streaming(job_id, cmd, timeout=0):
        if order is not None:
            order.append("demucs_start")
            await asyncio.sleep(0.01)
            order.append("demucs_end")
        yield ("done", 0, b"")

    monkeypatch.setattr(dp, "run_proc_factory", _factory)
    monkeypatch.setattr(dp, "run_proc_streaming_stderr", _streaming)
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dp, "get_best_device", lambda: device)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52)

    async def _drain():
        async for _ in dp.ingest_pipeline(
            job_id="t_perf", job_dir=str(tmp_path),
            source={"kind": "path", "path": str(video)},
        ):
            pass

    try:
        asyncio.run(_drain())
    except Exception:
        pass  # later stages may bail on fake media; the commands are recorded
    return calls


def test_combined_extraction_is_one_ffmpeg_call(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    calls = _run_ingest(dp, tmp_path, monkeypatch, device="cpu")
    assert len([c for c in calls if "-vn" in c]) == 1
    assert (tmp_path / "audio.wav").exists() and (tmp_path / "audio_hq.wav").exists()


def test_combined_failure_falls_back_to_two_calls(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    calls = _run_ingest(dp, tmp_path, monkeypatch, device="cpu", fail_combined=True)
    extracts = [c for c in calls if "-vn" in c]
    assert len(extracts) == 3  # failed combined + ASR + HQ
    assert sum("16000" in c for c in extracts[1:]) == 1
    assert sum("44100" in c for c in extracts[1:]) == 1
    assert (tmp_path / "audio.wav").exists() and (tmp_path / "audio_hq.wav").exists()


def test_scene_detection_overlaps_demucs_on_gpu_only(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    order = []
    calls = _run_ingest(dp, tmp_path, monkeypatch, device="cuda", order=order)
    assert order.index("scene_start") < order.index("demucs_end")
    scene = next(c for c in calls if SCENE_FILTER in c)
    assert "-an" in scene and "-sn" in scene

    order.clear()
    _run_ingest(dp, tmp_path, monkeypatch, device="cpu", order=order)
    assert order.index("scene_start") > order.index("demucs_end")
