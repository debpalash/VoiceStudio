"""Hardware video encoding opt-in and the maximum-resolution cap (#3732)."""
import asyncio
import shutil
import subprocess

import pytest

from services import video_encoder as ve

ENCODERS_OUT = """Encoders:
 V....D libx264              libx264 H.264 / AVC
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V....D h264_qsv             H.264 (Intel Quick Sync Video acceleration)
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder
 A....D aac                  AAC
"""


def test_parse_encoder_list_orders_by_platform(monkeypatch):
    monkeypatch.setattr(ve.sys, "platform", "linux")
    assert ve.parse_encoder_list(ENCODERS_OUT) == ["h264_nvenc", "h264_qsv"]
    monkeypatch.setattr(ve.sys, "platform", "darwin")
    assert ve.parse_encoder_list(ENCODERS_OUT) == ["h264_videotoolbox"]
    assert ve.parse_encoder_list(" V....D libx264 x") == []


def test_detection_runs_once_and_is_cached(monkeypatch):
    monkeypatch.setattr(ve.sys, "platform", "linux")
    monkeypatch.setattr(ve, "_detected", {})
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, ENCODERS_OUT, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ve.available_hw_encoders("/x/ffmpeg") == ("h264_nvenc", "h264_qsv")
    assert ve.available_hw_encoders("/x/ffmpeg") == ("h264_nvenc", "h264_qsv")
    assert len(calls) == 1 and calls[0][-1] == "-encoders"


def test_selection_software_default_and_hardware_opt_in(monkeypatch):
    monkeypatch.setattr(ve, "_detected", {"f": ("h264_nvenc",), "none": ()})
    assert ve.select_encoder("f", "software") == "libx264"
    assert ve.select_encoder("f", "hardware") == "h264_nvenc"
    assert ve.select_encoder("none", "hardware") == "libx264"  # nothing listed
    assert ve.encoder_args("libx264")[:2] == ("-c:v", "libx264")
    assert "yuv420p" in ve.encoder_args("h264_nvenc")


def test_default_pref_is_software(monkeypatch):
    from core import prefs

    monkeypatch.setattr(prefs, "get", lambda k, d=None: d)
    assert ve.encoder_mode() == "software"
    assert ve.max_resolution() == "original"
    monkeypatch.setattr(prefs, "get", lambda k, d=None: "bogus")
    assert ve.encoder_mode() == "software"
    assert ve.max_resolution() == "original"


def _runner(results):
    seen = []

    async def run(cmd):
        seen.append(list(cmd))
        return results[len(seen) - 1]

    return run, seen


def test_hardware_failure_retries_with_libx264(caplog):
    enc = ve.encoder_args("h264_nvenc")
    cmd = ["ffmpeg", "-i", "in.mp4", *enc, "-c:a", "aac", "out.mp4"]
    run, seen = _runner([(1, b"", b"No NVENC capable devices"), (0, b"", b"")])
    with caplog.at_level("WARNING"):
        rc, _, _ = asyncio.run(ve.run_with_encoder_fallback(cmd, enc, run))
    assert rc == 0 and len(seen) == 2
    assert "h264_nvenc" in seen[0] and "h264_nvenc" not in seen[1]
    assert "libx264" in seen[1] and seen[1][-1] == "out.mp4"
    assert "No NVENC capable devices" in caplog.text


def test_encoder_is_marked_failed_only_when_a_fallback_succeeds(monkeypatch):
    monkeypatch.setattr(ve, "_failed", {})
    monkeypatch.setattr(ve, "available_hw_encoders", lambda ff: ["h264_nvenc"])
    enc = ve.encoder_args("h264_nvenc")
    run, _ = _runner([(1, b"", b"bad input"), (1, b"", b"bad input")])
    assert asyncio.run(ve.run_with_encoder_fallback(["f", *enc], enc, run, ffmpeg="ff"))[0] == 1
    assert ve.select_encoder("ff", "hardware") == "h264_nvenc"  # input error: not blamed
    run, _ = _runner([(1, b"", b"no device"), (0, b"", b"")])
    assert asyncio.run(ve.run_with_encoder_fallback(["f", *enc], enc, run, ffmpeg="ff"))[0] == 0
    assert ve.select_encoder("ff", "hardware") == "libx264"


def test_no_retry_for_software_failure_or_abort():
    sw = ve.software_args()
    run, seen = _runner([(1, b"", b"boom")])
    assert asyncio.run(ve.run_with_encoder_fallback(["f", *sw], sw, run))[0] == 1
    assert len(seen) == 1  # a software failure is a real failure
    hw = ve.encoder_args("h264_amf")
    run, seen = _runner([(-9, b"", b"")])
    assert asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run))[0] == -9
    assert len(seen) == 1  # aborted, not retried


def test_signal_death_retries_unless_user_aborted():
    hw = ve.encoder_args("h264_amf")
    run, seen = _runner([(-11, b"", b"driver crash"), (0, b"", b"")])
    rc = asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run, aborted=lambda: False))[0]
    assert rc == 0 and len(seen) == 2  # not cancelled: fell back to libx264
    run, seen = _runner([(-9, b"", b"")])
    rc = asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run, aborted=lambda: True))[0]
    assert rc == -9 and len(seen) == 1


def test_windows_style_exit_code_one_after_abort_is_not_retried():
    hw = ve.encoder_args("h264_amf")
    run, seen = _runner([(1, b"", b"killed")])
    rc = asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run, aborted=lambda: True))[0]
    assert rc == 1 and len(seen) == 1


def test_abort_flag_set_just_after_exit_is_seen():
    hw = ve.encoder_args("h264_amf")
    flag = []
    run, seen = _runner([(1, b"", b"killed")])

    async def go():
        asyncio.get_running_loop().call_soon(flag.append, 1)  # abort lands after exit
        return await ve.run_with_encoder_fallback(
            ["f", *hw], hw, run, aborted=lambda: bool(flag))

    assert asyncio.run(go())[0] == 1 and len(seen) == 1


def test_hardware_timeout_falls_back_unless_aborted():
    hw = ve.encoder_args("h264_amf")
    calls = []

    async def run(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            raise asyncio.TimeoutError
        return 0, b"", b""

    assert asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run))[0] == 0
    assert len(calls) == 2 and "libx264" in calls[1]
    calls.clear()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(ve.run_with_encoder_fallback(["f", *hw], hw, run, aborted=lambda: True))
    assert len(calls) == 1
    calls.clear()
    sw = ve.software_args()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(ve.run_with_encoder_fallback(["f", *sw], sw, run))
    assert len(calls) == 1


def test_abort_endpoint_sets_flag_before_killing(monkeypatch):
    from api.routers import dub_core

    job = {}
    order = []
    monkeypatch.setitem(dub_core._dub_jobs, "j-order", job)
    monkeypatch.setattr(dub_core, "_kill_job_procs", lambda jid: order.append(bool(job.get("aborted"))))
    monkeypatch.setattr(dub_core.task_manager, "cancel_task", lambda jid: False)
    dub_core.dub_abort("j-order")
    assert order == [True]


@pytest.mark.parametrize("w,h,limit,expected", [
    (3840, 2160, "1080p", (1920, 1080)),
    (1920, 1080, "1080p", (1920, 1080)),      # already inside: untouched
    (1280, 720, "1080p", (1280, 720)),        # never upscales
    (1080, 1920, "1080p", (1080, 1920)),      # portrait inside the box
    (2160, 3840, "1080p", (1080, 1920)),      # portrait scaled by its long side
    (4096, 2160, "1080p", (1920, 1012)),      # rounded down to even
    (3840, 2160, "original", (3840, 2160)),
    (1920, 1080, "720p", (1280, 720)),
    (1281, 721, "2160p", (1281, 721)),        # odd source untouched when no scale
])
def test_capped_size(w, h, limit, expected):
    out = ve.capped_size(w, h, limit)
    assert out == expected
    assert ve.needs_downscale(w, h, limit) == (out != (w, h))
    if out != (w, h):
        assert out[0] % 2 == 0 and out[1] % 2 == 0


def test_scale_filter_shape():
    assert ve.scale_filter("original") == ""
    f = ve.scale_filter("1080p")
    assert f.startswith("scale=w='") and ":h='" in f
    assert "1920/max(iw,ih)" in f and "1080/min(iw,ih)" in f and "trunc(" in f


@pytest.mark.parametrize("src,expected", [("1280x720", "960x540"), ("720x1280", "540x960"),
                                           ("640x360", "640x360")])
def test_scale_filter_matches_ffmpeg(tmp_path, src, expected):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg needed")
    # 960x540 box via the real filter template (limit values swapped in)
    f = ve.scale_filter("1080p").replace("1920", "960").replace("1080", "540")
    out = tmp_path / "o.mp4"
    r = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={src}:rate=5:duration=0.4",
         "-vf", f, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True)
    w, h = p.stdout.strip().split(",")
    assert f"{w}x{h}" == expected


def test_settings_roundtrip(monkeypatch):
    from pydantic import ValidationError

    from api.routers import settings as sr
    from core import prefs

    store = {}
    monkeypatch.setattr(prefs, "get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(prefs, "set_", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)
    base = sr.get_video_export()
    assert base["encoder"] == "software" and base["max_resolution"] == "original"
    r = sr.set_video_export(sr._VideoExportBody(encoder="hardware", max_resolution="1080p"))
    assert r["encoder"] == "hardware" and r["max_resolution"] == "1080p"
    r = sr.set_video_export(sr._VideoExportBody(max_resolution="720p"))
    assert r["encoder"] == "hardware" and r["max_resolution"] == "720p"
    with pytest.raises(ValidationError):
        sr._VideoExportBody(encoder="gpu")


def test_retime_resolves_encoder_off_the_event_loop(monkeypatch, tmp_path):
    import threading

    from services import video_retime as vr

    seen = []

    def fake(ffmpeg):
        seen.append(threading.current_thread() is threading.main_thread())
        return vr.VIDEO_ENC_ARGS

    monkeypatch.setattr(vr, "_slice_encoder_args", fake)
    with pytest.raises(vr.RetimeError):
        asyncio.run(vr.render_retimed_video(
            job_id=None, ffmpeg="ffmpeg", video_path="x.mp4", chunks=[],
            out_path=str(tmp_path / "o.mp4"),
        ))
    assert seen == [False]


@pytest.mark.parametrize("rc1, aborted, falls_back", [
    (1, True, False), (1, False, True),
    ("timeout", False, True), ("timeout", True, False),
])
def test_retime_first_slice_exit_one_or_timeout_falls_back_unless_aborted(
        monkeypatch, tmp_path, rc1, aborted, falls_back):
    import core.config
    from services import video_retime as vr

    monkeypatch.setattr(core.config, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(vr, "_slice_encoder_args", lambda ff: ("-c:v", "h264_nvenc"))
    encoders = []

    async def fake_run(cmd, timeout=None, job_id=None):
        encoders.append(cmd[cmd.index("-c:v") + 1])
        if len(encoders) == 1:
            if rc1 == "timeout":
                raise asyncio.TimeoutError
            return rc1, b"", b""
        return 0, b"", b""

    monkeypatch.setattr(vr, "run_ffmpeg", fake_run)
    with pytest.raises(vr.RetimeError) as ei:
        asyncio.run(vr.render_retimed_video(
            job_id=None, ffmpeg="ffmpeg", video_path="x.mp4",
            chunks=[(0.0, 1.0, 1.0)], out_path=str(tmp_path / "o.mp4"),
            abort_check=lambda: aborted and len(encoders) >= 1,
        ))
    assert (len(encoders) > 1) is falls_back
    if not falls_back:
        assert ei.value.stage in ("aborted", "encode")


@pytest.mark.parametrize("aborted, falls_back", [(False, True), (True, False)])
def test_retime_first_slice_signal_death_falls_back_unless_aborted(
        monkeypatch, tmp_path, aborted, falls_back):
    import core.config
    from services import video_retime as vr

    monkeypatch.setattr(core.config, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(vr, "_slice_encoder_args", lambda ff: ("-c:v", "h264_nvenc"))
    encoders = []

    async def fake_run(cmd, timeout=None, job_id=None):
        encoders.append(cmd[cmd.index("-c:v") + 1])
        return (-9 if len(encoders) == 1 else 0), b"", b""

    monkeypatch.setattr(vr, "run_ffmpeg", fake_run)
    with pytest.raises(vr.RetimeError) as ei:
        asyncio.run(vr.render_retimed_video(
            job_id=None, ffmpeg="ffmpeg", video_path="x.mp4",
            chunks=[(0.0, 1.0, 1.0)], out_path=str(tmp_path / "o.mp4"),
            abort_check=lambda: aborted and len(encoders) >= 1,
        ))
    assert (len(encoders) > 1) is falls_back
    if falls_back:
        assert encoders[1] == "libx264"  # retried in software, failed later on missing output
    else:
        assert ei.value.stage == "aborted"


def _multi_vendor(monkeypatch, ffmpeg="/m/ffmpeg"):
    monkeypatch.setattr(ve.sys, "platform", "linux")
    monkeypatch.setattr(ve, "_detected", {ffmpeg: ("h264_nvenc", "h264_qsv", "h264_amf")})
    monkeypatch.setattr(ve, "_failed", {})
    return ffmpeg


def _fallback(ff, enc, run, **kw):
    return asyncio.run(ve.run_with_encoder_fallback(["f", *enc, "o.mp4"], enc, run, ffmpeg=ff, **kw))


def test_failed_nvenc_tries_qsv_and_remembers(monkeypatch):
    ff = _multi_vendor(monkeypatch)
    enc = ve.encoder_args(ve.select_encoder(ff, "hardware"))
    run, seen = _runner([(1, b"", b"no nvenc"), (0, b"", b"")])
    assert _fallback(ff, enc, run)[0] == 0
    assert "h264_qsv" in seen[1] and len(seen) == 2
    assert ve.select_encoder(ff, "hardware") == "h264_qsv"


def test_all_hardware_failing_ends_on_libx264_each_tried_once(monkeypatch):
    ff = _multi_vendor(monkeypatch)
    enc = ve.encoder_args("h264_nvenc")
    run, seen = _runner([(1, b"", b""), (1, b"", b""), (1, b"", b""), (0, b"", b"")])
    assert _fallback(ff, enc, run)[0] == 0
    names = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
    assert [[n in c for n in names].index(True) for c in seen] == [0, 1, 2, 3]
    assert ve.select_encoder(ff, "hardware") == ve.SOFTWARE


def test_abort_during_any_attempt_stops_everything(monkeypatch):
    ff = _multi_vendor(monkeypatch)
    enc = ve.encoder_args("h264_nvenc")
    state = {"abort": False}
    seen = []

    async def run(cmd):
        seen.append(cmd)
        state["abort"] = "h264_qsv" in cmd
        return 1, b"", b""

    rc = _fallback(ff, enc, run, aborted=lambda: state["abort"])[0]
    assert rc == 1 and len(seen) == 2


def test_timeout_moves_on_to_next_encoder(monkeypatch):
    ff = _multi_vendor(monkeypatch)
    enc = ve.encoder_args("h264_nvenc")
    calls = []

    async def run(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            raise asyncio.TimeoutError
        return 0, b"", b""

    assert _fallback(ff, enc, run)[0] == 0 and "h264_qsv" in calls[1]
