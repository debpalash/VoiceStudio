"""Video encoder selection and output-size cap for dub exports (#3732).

One place decides how an export's video is encoded:

* **Software (default)** — ``libx264``, identical on every OS.
* **Hardware when available (opt-in)** — NVENC / QSV / AMF / VideoToolbox,
  picked from what the bundled ffmpeg lists in ``-encoders`` (queried once,
  cached). A listed encoder can still fail on a machine without the matching
  GPU or driver, so callers run through :func:`run_with_encoder_fallback`,
  which retries the same command with the next listed hardware encoder, then
  libx264, and logs why.
* **Maximum resolution** — only ever downscales, keeps the aspect ratio and
  rounds both sides down to even numbers (H.264 4:2:0).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Awaitable, Callable

logger = logging.getLogger("omnivoice.api")

ENCODER_PREF = "video_encoder"
MAX_RES_PREF = "video_max_resolution"

ENCODER_MODES = ("software", "hardware")
#: label -> (long side, short side) in pixels. ``original`` = no cap.
MAX_RESOLUTIONS = {
    "original": None,
    "2160p": (3840, 2160),
    "1440p": (2560, 1440),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}

SOFTWARE = "libx264"
_PIX_FMT = ("-pix_fmt", "yuv420p")

#: Hardware H.264 encoders and their quality knobs, aimed at roughly the
#: software ``-crf 20`` look.
_HW_ARGS: dict[str, tuple[str, ...]] = {
    "h264_videotoolbox": ("-c:v", "h264_videotoolbox", "-q:v", "65"),
    "h264_nvenc": ("-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"),
    "h264_qsv": ("-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20"),
    "h264_amf": ("-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                 "-qp_i", "20", "-qp_p", "20"),
}


def _hw_order() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("h264_videotoolbox",)
    return ("h264_nvenc", "h264_qsv", "h264_amf")


def software_args(preset: str = "medium", crf: int = 20) -> tuple[str, ...]:
    return ("-c:v", SOFTWARE, "-preset", preset, "-crf", str(crf), *_PIX_FMT)


# ── Settings ───────────────────────────────────────────────────────────────


def encoder_mode() -> str:
    from core import prefs

    value = prefs.get(ENCODER_PREF, "software")
    return value if value in ENCODER_MODES else "software"


def max_resolution() -> str:
    from core import prefs

    value = prefs.get(MAX_RES_PREF, "original")
    return value if value in MAX_RESOLUTIONS else "original"


# ── Encoder detection (cached per ffmpeg binary) ───────────────────────────

_detected: dict[str, tuple[str, ...]] = {}


def parse_encoder_list(output: str) -> list[str]:
    """Hardware H.264 encoder names in ``ffmpeg -encoders`` output, best first."""
    names = set()
    for line in output.splitlines():
        parts = line.split()
        # " V....D h264_nvenc   NVIDIA NVENC H.264 encoder"
        if len(parts) >= 2 and parts[0].startswith("V") and parts[1] in _HW_ARGS:
            names.add(parts[1])
    return [n for n in _hw_order() if n in names]


def available_hw_encoders(ffmpeg: str) -> tuple[str, ...]:
    """Listed hardware encoders for this ffmpeg, queried once. Blocking."""
    if ffmpeg in _detected:
        return _detected[ffmpeg]
    import subprocess

    found: tuple[str, ...] = ()
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        if proc.returncode == 0:
            found = tuple(parse_encoder_list(proc.stdout))
    except Exception as e:  # noqa: BLE001 — detection is best-effort
        logger.debug("ffmpeg -encoders probe failed: %s", e)
    _detected[ffmpeg] = found
    return found


#: Hardware encoders that failed on this ffmpeg this session; skipped later.
_failed: dict[str, set[str]] = {}


def mark_failed(ffmpeg: str, name: str) -> None:
    _failed.setdefault(ffmpeg, set()).add(name)


def _usable_hw(ffmpeg: str) -> list[str]:
    bad = _failed.get(ffmpeg, ())
    return [n for n in available_hw_encoders(ffmpeg) if n not in bad]


def select_encoder(ffmpeg: str, mode: str | None = None) -> str:
    """libx264 unless hardware is opted in and ffmpeg lists a hardware encoder
    that has not already failed on this machine."""
    mode = mode or encoder_mode()
    if mode != "hardware":
        return SOFTWARE
    hw = _usable_hw(ffmpeg)
    return hw[0] if hw else SOFTWARE


def encoder_args(name: str, *, preset: str = "medium", crf: int = 20) -> tuple[str, ...]:
    if name in _HW_ARGS:
        return (*_HW_ARGS[name], *_PIX_FMT)
    return software_args(preset, crf)


# ── Fallback ───────────────────────────────────────────────────────────────


def replace_args(cmd: list[str], old: tuple[str, ...], new: tuple[str, ...]) -> list[str]:
    """``cmd`` with the contiguous run ``old`` swapped for ``new`` (unchanged if absent)."""
    n = len(old)
    for i in range(len(cmd) - n + 1):
        if tuple(cmd[i:i + n]) == old:
            return [*cmd[:i], *new, *cmd[i + n:]]
    return list(cmd)


async def user_cancelled(rc: int, aborted: Callable[[], bool] | None) -> bool:
    """True when a non-zero exit is the user's cancel, not an encoder failure.

    The abort flag is authoritative (Windows ``kill()`` exits with code 1, not
    a signal); one loop yield lets a concurrent abort request finish setting
    it. Without a flag, a signal death is treated as a cancel.
    """
    if aborted is None:
        return rc < 0
    await asyncio.sleep(0)
    return bool(aborted())


async def run_with_encoder_fallback(
    cmd: list[str],
    enc_args: tuple[str, ...],
    runner: Callable[[list[str]], Awaitable[tuple[int, bytes, bytes]]],
    aborted: Callable[[], bool] | None = None,
    ffmpeg: str | None = None,
) -> tuple[int, bytes, bytes]:
    """Run ``cmd`` (which contains ``enc_args``); if a hardware encode fails
    or times out, retry with the next listed hardware encoder (each at most
    once; failures are remembered per ``ffmpeg``), then libx264, logging why.

    A user abort (``aborted`` flag, any exit code) is returned as-is, never
    retried. Without ``aborted``, every negative code counts as an abort.
    """
    tried: set[str] = set()
    while True:
        hardware = len(enc_args) >= 2 and enc_args[1] in _HW_ARGS
        try:
            rc, out, err = await runner(cmd)
            reason = f"failed (rc={rc}): " + (err or b"").decode(errors="replace")[-300:]
        except asyncio.TimeoutError:
            if not hardware or await user_cancelled(1, aborted):
                raise
            rc, out, err, reason = 1, b"", b"", "timed out"
        else:
            if rc == 0 or not hardware or await user_cancelled(rc, aborted):
                if rc == 0 and ffmpeg:
                    # A later attempt succeeding is what shows the earlier
                    # encoders were at fault; an input/disk error that fails
                    # every attempt must not disable hardware for the session.
                    for failed_name in tried:
                        mark_failed(ffmpeg, failed_name)
                return rc, out, err
        name = enc_args[1]
        tried.add(name)
        nxt = ""
        if ffmpeg:
            nxt = next((n for n in _usable_hw(ffmpeg) if n not in tried), "")
        new = encoder_args(nxt) if nxt else software_args()
        logger.warning(
            "Hardware encoder %s %s; retrying with %s", name, reason, nxt or SOFTWARE,
        )
        cmd = replace_args(cmd, enc_args, new)
        enc_args = new


# ── Maximum resolution ─────────────────────────────────────────────────────


def capped_size(width: int, height: int, limit: str) -> tuple[int, int]:
    """Output size under ``limit``: downscale only, aspect kept, even sides.
    Mirrors :func:`scale_filter` (long side vs. long limit, short vs. short)."""
    box = MAX_RESOLUTIONS.get(limit)
    if not box or width <= 0 or height <= 0:
        return width, height
    f = min(1.0, box[0] / max(width, height), box[1] / min(width, height))
    if f >= 1.0:
        return width, height
    return int(width * f) // 2 * 2, int(height * f) // 2 * 2


def needs_downscale(width: int, height: int, limit: str) -> bool:
    box = MAX_RESOLUTIONS.get(limit)
    if not box or width <= 0 or height <= 0:
        return False
    return max(width, height) > box[0] or min(width, height) > box[1]


def scale_filter(limit: str) -> str:
    """ffmpeg ``scale`` filter that caps the size (portrait-safe), or ``""``."""
    box = MAX_RESOLUTIONS.get(limit)
    if not box:
        return ""
    f = f"min(1,min({box[0]}/max(iw,ih),{box[1]}/min(iw,ih)))"
    return (
        f"scale=w='if(lt({f},1),trunc(iw*{f}/2)*2,iw)'"
        f":h='if(lt({f},1),trunc(ih*{f}/2)*2,ih)'"
    )


async def probe_video_size(path: str) -> "tuple[int, int] | None":
    """(width, height) of the first video stream, or None. Never raises."""
    from services.ffmpeg_utils import find_ffprobe, spawn_subprocess

    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.isfile(path):
        return None
    try:
        proc = await spawn_subprocess(
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        w, h = stdout.decode().strip().split(",")[:2]
        return int(w), int(h)
    except Exception as e:  # noqa: BLE001
        logger.debug("probe_video_size failed: %s", e)
        return None
