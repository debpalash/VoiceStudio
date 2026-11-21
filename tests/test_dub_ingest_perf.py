"""Ingest perf paths: one-pass audio extraction and overlapped scene detection.

Both keep an automatic fallback to the previous behaviour.
"""
import asyncio
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SCENE_FILTER = "select='gt(scene,0.3)',showinfo"


def _run_ingest(dp, tmp_path, monkeypatch, *, device, fail_combined=False, order=None, hq_publish_fails=False, cancel_in_demucs=False):
    from core.db import init_db

    init_db()
    calls = []
    started = {}

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
            if SCENE_FILTER in cmd and cancel_in_demucs:
                started['ev'].set()
                try:
                    await asyncio.Event().wait()  # blocks until cancelled
                except asyncio.CancelledError:
                    # Slow ffmpeg teardown: finishes only when the test says so.
                    started['teardown_entered'].set()
                    await started['teardown_release'].wait()
                    order.append("scene_cleaned")
                    raise
            if SCENE_FILTER in cmd:
                if order is not None:
                    order.append("scene_start")
                if started.get('ev') is not None:
                    started['ev'].set()
                return P(), b"", b"pts_time:1.5 pts_time:3.0"
            return P(), b"", b""
        return run_proc

    async def _streaming(job_id, cmd, timeout=0):
        calls.append([str(c) for c in cmd])
        if order is not None:
            order.append("demucs_start")
        if cancel_in_demucs:
            await asyncio.wait_for(started['ev'].wait(), timeout=5)
            raise asyncio.CancelledError
        if order is not None and device not in ("cpu", "boom"):
            # Overlap proof: Demucs only finishes once the scene scan has begun.
            await asyncio.wait_for(started['ev'].wait(), timeout=5)
        if order is not None:
            order.append("demucs_end")
        yield ("done", 0, b"")

    monkeypatch.setattr(dp, "run_proc_factory", _factory)
    monkeypatch.setattr(dp, "run_proc_streaming_stderr", _streaming)
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")
    probes = []

    def _probe():
        probes.append(1)
        if device == "boom" and len(probes) == 1:
            raise ImportError("torch missing")  # only the scene-overlap probe fails
        return "cpu" if device == "boom" else device

    monkeypatch.setattr(dp, "get_best_device", _probe)
    if hq_publish_fails:
        real_replace = os.replace

        def _replace(src, dst, *a, **k):
            if str(dst).endswith("audio_hq.wav"):
                raise OSError("hq publish denied")
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", _replace)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52)

    async def _release_teardown():
        # Release the scene teardown only once it has really begun.
        await started['teardown_entered'].wait()
        order.append("teardown_began")
        started['teardown_release'].set()

    async def _drain():
        started['ev'] = asyncio.Event()
        started['teardown_entered'] = asyncio.Event()
        started['teardown_release'] = asyncio.Event()
        if cancel_in_demucs:
            asyncio.get_running_loop().create_task(_release_teardown())
        async for _ in dp.ingest_pipeline(
            job_id="t_perf", job_dir=str(tmp_path),
            source={"kind": "path", "path": str(video)},
        ):
            pass

    try:
        asyncio.run(_drain())
    except asyncio.CancelledError:
        if order is not None:
            order.append("generator_exited")
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


def test_hq_publish_failure_is_not_fatal(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    calls = _run_ingest(dp, tmp_path, monkeypatch, device="cpu", hq_publish_fails=True)
    assert (tmp_path / "audio.wav").exists() and not (tmp_path / "audio_hq.wav").exists()
    demucs = next(c for c in calls if "demucs.separate" in " ".join(c))
    assert not any(a.endswith("audio_hq.wav") for a in demucs)  # fell back to the ASR file


def test_device_probe_failure_stays_serial(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    order = []
    _run_ingest(dp, tmp_path, monkeypatch, device="boom", order=order)
    # Probe failure must not abort ingest: the scan still runs, after Demucs.
    assert order.index("scene_start") > order.index("demucs_end")


def test_cancel_waits_for_scene_task_before_cleanup(tmp_path, monkeypatch):
    from services import dub_pipeline as dp

    order = []
    _run_ingest(dp, tmp_path, monkeypatch, device="cuda", order=order, cancel_in_demucs=True)
    assert order.index("scene_cleaned") < order.index("generator_exited")
