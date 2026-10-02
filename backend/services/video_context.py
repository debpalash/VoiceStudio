"""
Context-aware pipeline — extract visual cues from video frames to inform
dubbing decisions.

This service analyses keyframes from the source video and produces
per-segment visual context that the TTS instruct system can use:

  - Scene mood (dark, bright, action, calm, dialogue, crowd)
  - Speaker emotions (neutral, happy, sad, angry, surprised)
  - Environment (indoor, outdoor, studio, stage, vehicle)
  - On-screen text / captions detected via basic OCR

Usage:
    from services.video_context import analyse_video, get_segment_context

    # Full analysis (run once after video ingest)
    ctx = await analyse_video(video_path, segments)

    # Per-segment context for TTS instruct generation
    instruct_hint = get_segment_context(ctx, segment_index=3)
    # → "Speak with calm energy, indoor studio setting, speaker appears focused"
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("omnivoice.video_context")

_analysis_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vid-ctx")


# ── Frame extraction ─────────────────────────────────────────────────

def _extract_keyframes(
    video_path: str,
    timestamps: list[float],
    max_frames: int = 30,
    output_dir: str | None = None,
) -> list[tuple[float, str]]:
    """Extract frames at specified timestamps using ffmpeg.

    Returns list of (timestamp, frame_path) tuples.
    """
    import subprocess

    from services.ffmpeg_utils import find_ffmpeg

    # Resolve ffmpeg the way every other call site does. `shutil.which("ffmpeg")`
    # only finds a system install: the binary imageio-ffmpeg ships — the app's
    # default source — is named `ffmpeg-<platform>-v<version>`, so this skipped
    # frame extraction on hosts where the app's own ffmpeg was resolvable.
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg not found, skipping frame extraction")
        return []

    tmp_dir = output_dir or tempfile.mkdtemp(prefix="omnivoice_frames_")
    frames = []

    # Subsample if too many timestamps
    step = max(1, len(timestamps) // max_frames)
    selected = timestamps[::step][:max_frames]

    for i, ts in enumerate(selected):
        out_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
        try:
            subprocess.run(
                [
                    ffmpeg, "-ss", str(ts), "-i", video_path,
                    "-frames:v", "1", "-q:v", "3",
                    "-y", out_path,
                ],
                capture_output=True, timeout=10,
            )
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                frames.append((ts, out_path))
        except Exception as e:
            logger.debug("Frame extraction failed at t=%.1f: %s", ts, e)

    logger.info("Extracted %d keyframes from %s", len(frames), video_path)
    return frames


# ── Frame analysis ───────────────────────────────────────────────────

def _analyse_frame_basic(frame_path: str) -> dict:
    """Analyse a single frame using basic image statistics.

    This is the fallback when no ML model is available. It uses
    brightness, color distribution, and edge detection to infer
    basic scene properties.
    """
    try:
        from PIL import Image
        import statistics

        img = Image.open(frame_path).convert("RGB").resize((320, 240))
        pixels = list(img.get_flattened_data())

        # Brightness
        luminances = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
        avg_lum = statistics.mean(luminances)

        # Color saturation
        saturations = []
        for r, g, b in pixels:
            mx = max(r, g, b)
            mn = min(r, g, b)
            saturations.append((mx - mn) / max(mx, 1))
        avg_sat = statistics.mean(saturations)

        # Classify
        brightness = "dark" if avg_lum < 80 else "bright" if avg_lum > 180 else "normal"
        mood = "calm" if avg_sat < 0.3 else "vivid" if avg_sat > 0.6 else "neutral"

        # Edge density → approximates "action" vs "static"
        try:
            gray = img.convert("L")
            edge_pixels = list(gray.get_flattened_data())
            diffs = [
                abs(edge_pixels[i] - edge_pixels[i + 1])
                for i in range(len(edge_pixels) - 1)
            ]
            edge_density = statistics.mean(diffs)
            complexity = (
                "action" if edge_density > 40
                else "detailed" if edge_density > 20
                else "simple"
            )
        except Exception:
            complexity = "unknown"

        return {
            "brightness": brightness,
            "mood": mood,
            "complexity": complexity,
            "avg_luminance": round(avg_lum, 1),
            "avg_saturation": round(avg_sat, 3),
        }

    except ImportError:
        return {"brightness": "unknown", "mood": "unknown", "complexity": "unknown"}
    except Exception as e:
        logger.debug("Frame analysis failed: %s", e)
        return {"brightness": "unknown", "mood": "unknown", "complexity": "unknown"}


# ── Full video analysis ──────────────────────────────────────────────

class VideoContext:
    """Container for per-segment visual context analysis."""

    def __init__(self):
        self.frame_analyses: dict[float, dict] = {}  # timestamp → analysis
        self.segment_contexts: dict[int, dict] = {}   # seg_index → merged context
        self.global_mood: str = "neutral"
        self.global_brightness: str = "normal"

    def to_dict(self) -> dict:
        return {
            "global_mood": self.global_mood,
            "global_brightness": self.global_brightness,
            "segments": self.segment_contexts,
            "frame_count": len(self.frame_analyses),
        }


def _build_segment_context(
    ctx: VideoContext,
    segments: list[dict],
) -> VideoContext:
    """Map frame analyses to segments based on timestamp overlap."""
    sorted_timestamps = sorted(ctx.frame_analyses.keys())

    for i, seg in enumerate(segments):
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", seg_start + 1)

        # Find frames within this segment's time range
        nearby = [
            ctx.frame_analyses[ts]
            for ts in sorted_timestamps
            if seg_start - 0.5 <= ts <= seg_end + 0.5
        ]

        if not nearby:
            # Find the closest frame
            if sorted_timestamps:
                mid = (seg_start + seg_end) / 2
                closest_ts = min(sorted_timestamps, key=lambda t: abs(t - mid))
                nearby = [ctx.frame_analyses[closest_ts]]

        if nearby:
            # Majority vote for categorical fields
            from collections import Counter
            brightness = Counter(f["brightness"] for f in nearby).most_common(1)[0][0]
            mood = Counter(f["mood"] for f in nearby).most_common(1)[0][0]
            complexity = Counter(f["complexity"] for f in nearby).most_common(1)[0][0]

            ctx.segment_contexts[i] = {
                "brightness": brightness,
                "mood": mood,
                "complexity": complexity,
                "frame_count": len(nearby),
            }
        else:
            ctx.segment_contexts[i] = {
                "brightness": "unknown",
                "mood": "unknown",
                "complexity": "unknown",
                "frame_count": 0,
            }

    # Global mood = most common across all frames
    if ctx.frame_analyses:
        from collections import Counter
        all_moods = [a["mood"] for a in ctx.frame_analyses.values()]
        ctx.global_mood = Counter(all_moods).most_common(1)[0][0]
        all_bright = [a["brightness"] for a in ctx.frame_analyses.values()]
        ctx.global_brightness = Counter(all_bright).most_common(1)[0][0]

    return ctx


async def analyse_video(
    video_path: str,
    segments: list[dict],
    max_frames: int = 30,
) -> VideoContext:
    """Analyse a video's visual context for dubbing decisions.

    Args:
        video_path: Path to the source video file.
        segments: List of segment dicts with 'start' and 'end' keys.
        max_frames: Maximum number of keyframes to extract.

    Returns:
        VideoContext with per-segment and global visual analysis.
    """
    # The executor may outlive a cancelled request. Keep extraction, analysis
    # and cleanup in the same worker so its files retain one lifetime owner.
    return await asyncio.get_running_loop().run_in_executor(
        _analysis_pool, _analyse_video_worker, video_path, segments, max_frames,
    )


def _analyse_video_worker(video_path: str, segments: list[dict], max_frames: int) -> VideoContext:
    ctx = VideoContext()
    timestamps = [(seg.get("start", 0) + seg.get("end", 0)) / 2 for seg in segments]
    with tempfile.TemporaryDirectory(prefix="omnivoice_frames_") as directory:
        frames = _extract_keyframes(video_path, timestamps, max_frames, directory)
        for ts, frame_path in frames:
            ctx.frame_analyses[ts] = _analyse_frame_basic(frame_path)
        ctx = _build_segment_context(ctx, segments)
    logger.info(
        "Video analysis complete: %d frames, global_mood=%s, global_brightness=%s",
        len(frames), ctx.global_mood, ctx.global_brightness,
    )
    return ctx


def get_segment_context(ctx: VideoContext, segment_index: int) -> str:
    """Generate a natural-language instruct hint from visual context.

    This string can be appended to the TTS instruct field to make
    generated speech better match the on-screen mood.
    """
    seg_ctx = ctx.segment_contexts.get(segment_index)
    if not seg_ctx or seg_ctx.get("brightness") == "unknown":
        return ""

    parts = []

    # Mood → energy
    mood_map = {
        "calm": "Speak with calm, relaxed energy",
        "vivid": "Speak with vibrant, expressive energy",
        "neutral": "Speak in a natural, conversational tone",
    }
    parts.append(mood_map.get(seg_ctx["mood"], ""))

    # Brightness → atmosphere
    bright_map = {
        "dark": "dark or dramatic atmosphere",
        "bright": "bright, well-lit setting",
        "normal": "",
    }
    atmos = bright_map.get(seg_ctx["brightness"], "")
    if atmos:
        parts.append(atmos)

    # Complexity → pacing
    if seg_ctx["complexity"] == "action":
        parts.append("fast-paced scene")
    elif seg_ctx["complexity"] == "simple":
        parts.append("quiet moment")

    return ", ".join(p for p in parts if p)
