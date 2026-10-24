"""Smart Fit video retime executor — dub-length fitting v2, Phase B.

Renders the per-segment video retime described by a fit plan (the
``video_plan`` dict list persisted by Phase A in ``job["fit_plans"]``,
same chunk shape as the legacy ``video_stretch_plans``) into an actual
retimed video stream, with a two-tier strategy:

* **Single-pass** (chunk count ≤ :data:`RETIME_SINGLE_PASS_MAX_CHUNKS`):
  one ``split → trim → setpts → concat`` filter_complex graph, returned to
  the caller for inline use in its mux command. Lowest drift, proven by the
  legacy ``stretch_video`` path which uses the exact same graph shape.
* **Batched** (above the threshold): ffmpeg filter graphs degrade badly past
  ~50 splits of the same decoded stream (memory + filter-graph setup cost),
  so chunks are partitioned into batches of :data:`RETIME_BATCH_SIZE`, each
  batch rendered to an intermediate slice MP4 with identical codec params
  and a forced keyframe at t=0, then joined losslessly with the concat
  demuxer (``-f concat -c copy``).

Drift absorption: the *expected* retimed duration is computable from the
chunks, so when the fitted audio track outruns it the last slice (or the
single-pass graph) gets a ``tpad=stop_mode=clone`` freeze-frame tail; the
residual difference after encoding (fps rounding) is reported back via
:class:`RetimeDecision.video_dur` so the caller can ``apad`` the audio.

VFR guard: sources whose ``r_frame_rate`` and ``avg_frame_rate`` disagree
are normalised with an ``fps=`` filter before trim/setpts — trim by
timestamp on a VFR stream lands on unpredictable frames.

Clean-room note: like ``services.fit_planner`` this is implemented from a
published description of the audio-speedup + video-slowdown fitting
approach only (docs/competitive-analysis.md, "Dub-length fitting"). No GPL
source was consulted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass

# Module access (not ``from core.config import DUB_DIR``) so the containment
# guards below read the live value — tests reload core.config with a
# sandboxed data dir.
from services.ffmpeg_utils import probe_duration, probe_frame_rates, run_ffmpeg

logger = logging.getLogger("omnivoice.api")

#: Above this chunk count a single filter_complex graph becomes fragile
#: (one split branch per chunk, all decoded in lockstep) — switch to the
#: batched per-slice pipeline.
RETIME_SINGLE_PASS_MAX_CHUNKS = 48

#: Chunks per intermediate slice in the batched pipeline.
RETIME_BATCH_SIZE = 40

#: If the concat-demuxer join fails but the plan is only modestly over the
#: single-pass threshold, retry once as a single pass before giving up.
RETIME_SINGLE_PASS_RETRY_MAX = 96

#: Audio/video length differences below this are inaudible/invisible —
#: don't pad for them.
DRIFT_TOLERANCE_S = 0.05

#: Codec params for retimed video — MUST match the mux re-encode settings in
#: api.routers.dub_export so slices and single-pass output are identical.
# H.264 4:2:0 needs even dimensions. Keep every source pixel; pad at most
# one column/row instead of cropping odd-sized screen recordings.
H264_SIZE_FILTER = "pad=ceil(iw/2)*2:ceil(ih/2)*2"

VIDEO_ENC_ARGS = ("-c:v", "libx264", "-preset", "medium", "-crf", "20",
                  "-pix_fmt", "yuv420p")


class RetimeError(Exception):
    """A retime render step failed. ``stage`` is one of
    ``plan | encode | concat | aborted``."""

    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class RetimeDecision:
    """How the caller should consume the retimed video.

    ``mode == "filter"``: feed ``graph`` into the mux command's
    ``-filter_complex`` and map ``label``.
    ``mode == "file"``: a fully retimed MP4 exists at ``file_path``; map its
    video stream (stream-copyable). ``video_dur`` is the expected (filter)
    or ffprobe-measured (file) retimed duration in seconds.
    """
    mode: str
    graph: str = ""
    label: str = ""
    file_path: str = ""
    video_dur: float = 0.0


# ── Pure plan math ──────────────────────────────────────────────────────────


def expand_retime_chunks(
    plan: list[dict], orig_dur: float,
) -> list[tuple[float, float, float]]:
    """Expand a per-segment plan into contiguous (start, end, ratio) chunks.

    Gaps between plan entries — and the pre-roll / tail — are emitted at
    1.0× so silence and B-roll don't get squashed. This is the exact logic
    the legacy ``stretch_video`` graph builder used inline; both paths now
    share it so single-pass and batched renders agree on chunk boundaries.
    """
    chunks: list[tuple[float, float, float]] = []
    cursor = 0.0
    for entry in plan:
        a = float(entry["orig_start"])
        b = float(entry["orig_end"])
        if a > cursor + 1e-3:
            chunks.append((cursor, a, 1.0))  # gap or pre-roll at native rate
        ratio = float(entry["stretch_ratio"])
        if b > a:
            chunks.append((a, b, ratio))
        cursor = max(cursor, b)
    if orig_dur > cursor + 1e-3:
        chunks.append((cursor, orig_dur, 1.0))  # tail at native rate
    return [(a, b, r) for (a, b, r) in chunks if b > a]


def partition_batches(
    chunks: list[tuple[float, float, float]],
    batch_size: int = RETIME_BATCH_SIZE,
) -> list[list[tuple[float, float, float]]]:
    """Split chunks into order-preserving batches of at most ``batch_size``."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]


def build_chunk_filter_graph(
    chunks: list[tuple[float, float, float]],
    in_label: str,
    *,
    fps_norm: str | None = None,
    tail_pad_s: float = 0.0,
    out_fps: str | None = None,
) -> tuple[str, str]:
    """Build the split/trim/setpts/concat filter graph for ``chunks``.

    The legacy ``_build_video_stretch_filter_graph`` delegates here, so both
    paths apply the same timing and H.264-compatible frame padding.

    ``fps_norm`` prepends an ``fps=`` normalisation stage (VFR guard).
    ``out_fps`` resamples the concat output back to a constant frame rate.
    setpts leaves VFR-ish timestamps behind, and on some ffmpeg builds
    (observed on 7.x) ``tpad`` after such a stream is a silent no-op and
    slice durations drift by a frame per retimed chunk — CFR output fixes
    both, so the batched pipeline and any ``tail_pad_s`` user should set it.
    ``tail_pad_s`` appends a freeze-last-frame ``tpad`` (drift absorption),
    placed after the ``out_fps`` resample.
    Returns (graph, output_label); output label is always ``[vstretched]``.
    """
    if not chunks:
        return "", in_label
    src = in_label
    parts: list[str] = []
    if fps_norm:
        parts.append(f"{src}fps={fps_norm}[vcfr]")
        src = "[vcfr]"
    labels: list[str] = []
    # `split` lets us tap the same source stream once per chunk without re-
    # decoding. setpts={ratio}*PTS slows down (ratio > 1) or speeds up
    # (ratio < 1) each chunk; PTS-STARTPTS first to normalise the timestamp
    # base after the trim.
    split_labels = [f"[vsplit{idx}]" for idx in range(len(chunks))]
    parts.append(f"{src}split={len(chunks)}{''.join(split_labels)}")
    for idx, ((a, b, ratio), split_lbl) in enumerate(zip(chunks, split_labels)):
        out_label = f"[vstr{idx}]"
        labels.append(out_label)
        parts.append(
            f"{split_lbl}trim=start={a:.4f}:end={b:.4f},"
            f"setpts=PTS-STARTPTS,setpts={ratio:.6f}*PTS{out_label}"
        )
    terminal = "".join(labels) + f"concat=n={len(chunks)}:v=1:a=0"
    post: list[str] = [H264_SIZE_FILTER]
    if out_fps:
        post.append(f"fps={out_fps}")
    if tail_pad_s > 0:
        post.append(f"tpad=stop_mode=clone:stop_duration={tail_pad_s:.4f}")
    if post:
        parts.append(terminal + "[vcat]")
        parts.append("[vcat]" + ",".join(post) + "[vstretched]")
    else:
        parts.append(terminal + "[vstretched]")
    return ";".join(parts), "[vstretched]"


def _parse_rate(rate: str | None) -> float | None:
    """Parse an ffprobe frame-rate string ('30000/1001' or '25') to fps."""
    if not rate:
        return None
    try:
        if "/" in rate:
            num_s, den_s = rate.split("/", 1)
            num, den = float(num_s), float(den_s)
            if den == 0:
                return None
            value = num / den
        else:
            value = float(rate)
    except ValueError:
        return None
    return value if value > 0 else None


def is_vfr(r_frame_rate: str | None, avg_frame_rate: str | None) -> bool:
    """True when the container's nominal and average frame rates disagree
    by more than 0.1% — the practical signature of variable frame rate."""
    r = _parse_rate(r_frame_rate)
    avg = _parse_rate(avg_frame_rate)
    if r is None or avg is None:
        return False
    return abs(r - avg) / max(avg, 1e-9) > 1e-3


def _concat_escape(path: str) -> str:
    """Escape a path for a concat-demuxer list `file '<path>'` directive."""
    return path.replace("'", "'\\''")


# ── Batched executor ────────────────────────────────────────────────────────


async def render_retimed_video(
    *,
    job_id: str | None,
    ffmpeg: str,
    video_path: str,
    chunks: list[tuple[float, float, float]],
    out_path: str,
    batch_size: int = RETIME_BATCH_SIZE,
    fps_norm: str | None = None,
    out_fps: str | None = None,
    tail_pad_s: float = 0.0,
    timeout_per_batch: float = 1800.0,
    abort_check=None,
) -> None:
    """Render ``chunks`` to a retimed MP4 at ``out_path`` via batched slices.

    ``out_fps`` (the source's probed frame rate) is applied to every slice
    so each is strictly CFR — slice durations stay frame-exact across the
    concat-demuxer join and ``tail_pad_s`` behaves on all ffmpeg builds.

    Each batch is encoded with :data:`VIDEO_ENC_ARGS` and a forced keyframe
    at t=0 so the concat-demuxer join (`-c copy`) lands on keyframes.
    Subprocesses run through ``run_ffmpeg(job_id=...)`` so ``/dub/abort``
    can kill them; ``abort_check`` is polled between batches for an early
    cooperative stop. Temp slices are removed on success AND failure.

    Raises :class:`RetimeError` on any failure (stage encode/concat/aborted).
    """
    # ``out_path`` is server-built (under DUB_DIR) by every caller, but
    # realpath-normalise + containment-check inline at the sink anyway so
    # slices_dir / slice_path / list_path all derive from the validated
    # value (CodeQL does not track guards through helper return values).
    from core.config import DUB_DIR as _dub_root
    _base = os.path.realpath(_dub_root)
    out_path = os.path.realpath(out_path)
    if out_path != _base and not out_path.startswith(_base + os.sep):
        raise RetimeError("retime output path escapes the dub workspace",
                          stage="plan")
    batches = partition_batches(chunks, batch_size)
    if not batches:
        raise RetimeError("empty retime plan", stage="plan")
    slices_dir = out_path + ".slices"
    os.makedirs(slices_dir, exist_ok=True)
    try:
        slice_paths: list[str] = []
        for bi, batch in enumerate(batches):
            if abort_check is not None and abort_check():
                raise RetimeError("export aborted", stage="aborted")
            is_last = bi == len(batches) - 1
            # #382: seek the input to this batch's window instead of decoding
            # from frame 0 every time — without this, batch N pays for
            # decoding everything before it and long exports go O(n²).
            # `-ss` before `-i` is frame-accurate under re-encode (ffmpeg
            # decodes from the prior keyframe and discards up to the target);
            # decoded timestamps then start at ~0, so chunk times are shifted
            # into window-relative coordinates for the filter graph.
            win_start = batch[0][0]
            win_dur = batch[-1][1] - win_start
            shifted = [(max(0.0, a - win_start), b - win_start, r) for a, b, r in batch]
            graph, label = build_chunk_filter_graph(
                shifted, "[0:v]",
                fps_norm=fps_norm,
                out_fps=out_fps,
                tail_pad_s=tail_pad_s if is_last else 0.0,
            )
            slice_path = os.path.join(slices_dir, f"slice_{bi:04d}.mp4")
            seek_args = (
                ["-ss", f"{win_start:.4f}"] if win_start > 1e-4 else []
            )
            cmd = [
                ffmpeg, "-hide_banner", "-y",
                *seek_args,
                # +0.5s read margin: trim's end is exclusive and fps_norm can
                # shift a frame; reading slightly past the window is cheap.
                "-t", f"{win_dur + 0.5:.4f}",
                "-i", video_path,
                "-filter_complex", graph, "-map", label, "-an",
                *VIDEO_ENC_ARGS,
                "-force_key_frames", "0",
                slice_path,
            ]
            try:
                rc, _, stderr = await run_ffmpeg(
                    cmd, timeout=timeout_per_batch, job_id=job_id,
                )
            except asyncio.TimeoutError:
                raise RetimeError(
                    f"retime batch {bi + 1}/{len(batches)} timed out",
                    stage="encode",
                )
            if rc != 0:
                # Negative rc = killed by signal (user cancel via
                # kill_job_procs) — report as aborted, not a render failure.
                if rc < 0:
                    raise RetimeError("retime cancelled", stage="aborted")
                tail = (stderr or b"").decode(errors="replace")[-300:]
                raise RetimeError(
                    f"retime batch {bi + 1}/{len(batches)} failed: {tail}",
                    stage="encode",
                )
            if not os.path.exists(slice_path) or os.path.getsize(slice_path) == 0:
                raise RetimeError(
                    f"retime batch {bi + 1}/{len(batches)} produced no output",
                    stage="encode",
                )
            slice_paths.append(slice_path)
            logger.info(
                "video retime: slice %d/%d rendered (%d chunks)",
                bi + 1, len(batches), len(batch),
            )

        list_path = os.path.join(slices_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in slice_paths:
                f.write(f"file '{_concat_escape(p)}'\n")
        join_cmd = [
            ffmpeg, "-hide_banner", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", out_path,
        ]
        try:
            rc, _, stderr = await run_ffmpeg(
                join_cmd, timeout=timeout_per_batch, job_id=job_id,
            )
        except asyncio.TimeoutError:
            raise RetimeError("retime concat join timed out", stage="concat")
        if rc != 0:
            if rc < 0:
                raise RetimeError("retime cancelled", stage="aborted")
            tail = (stderr or b"").decode(errors="replace")[-300:]
            raise RetimeError(f"retime concat join failed: {tail}", stage="concat")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RetimeError("retime concat join produced no output", stage="concat")
    except BaseException:
        # Partial output is worse than none — a later cache/exists check
        # must never pick up a half-joined file.
        try:
            os.remove(out_path)
        except OSError as e:
            logger.debug("cleanup remove failed: %s", e)
        raise
    finally:
        shutil.rmtree(slices_dir, ignore_errors=True)


async def prepare_smart_fit_video(
    *,
    job_id: str | None,
    ffmpeg: str,
    video_path: str,
    plan: list[dict],
    orig_dur: float,
    track_dur: float,
    work_path: str,
    single_pass_max: int = RETIME_SINGLE_PASS_MAX_CHUNKS,
    batch_size: int = RETIME_BATCH_SIZE,
    abort_check=None,
) -> RetimeDecision | None:
    """Decide and (for the batched tier) execute the video retime for a
    Smart Fit plan.

    Returns ``None`` when no chunk actually needs retiming (audio-only fit:
    every video_ratio is 1.0) — the caller should stream-copy the source.
    ``track_dur`` is the fitted dub track's duration; when it exceeds the
    expected retimed video length the tail is freeze-frame padded.

    Raises :class:`RetimeError` when rendering fails — the caller owns the
    fallback ladder (un-retimed export + structured warning).
    """
    # ``work_path`` is server-built (under DUB_DIR) by every caller, but
    # realpath-normalise + containment-check inline anyway so the batched
    # render and the returned ``RetimeDecision.file_path`` derive from the
    # validated value (CodeQL does not track guards through helpers).
    from core.config import DUB_DIR as _dub_root
    _base = os.path.realpath(_dub_root)
    work_path = os.path.realpath(work_path)
    if work_path != _base and not work_path.startswith(_base + os.sep):
        raise RetimeError("retime work path escapes the dub workspace",
                          stage="plan")

    chunks = expand_retime_chunks(plan, orig_dur)
    if not chunks or not any(r > 1.0 + 1e-6 for _a, _b, r in chunks):
        return None

    # VFR guard: trim-by-timestamp on a VFR stream lands on unpredictable
    # frames. Normalise to the average rate first. Probe failure → proceed
    # un-normalised (best effort; CFR sources are the overwhelming norm).
    # The probed rate doubles as the slice/tpad CFR target (out_fps).
    fps_norm: str | None = None
    out_fps: str | None = None
    rates = await probe_frame_rates(video_path)
    if rates:
        out_fps = (rates[1] if _parse_rate(rates[1]) else None) or \
                  (rates[0] if _parse_rate(rates[0]) else None)
        if is_vfr(*rates):
            fps_norm = out_fps
            logger.info(
                "video retime: VFR source (r_frame_rate=%s avg_frame_rate=%s) "
                "— normalising with fps=%s", rates[0], rates[1], fps_norm,
            )

    expected = sum((b - a) * r for a, b, r in chunks)
    tail_pad = 0.0
    if track_dur and track_dur - expected > DRIFT_TOLERANCE_S:
        tail_pad = track_dur - expected

    if len(chunks) <= single_pass_max:
        graph, label = build_chunk_filter_graph(
            chunks, "[0:v]", fps_norm=fps_norm, tail_pad_s=tail_pad,
            # CFR resample only when tpad needs it — without a tail pad the
            # single-pass graph stays exactly the proven legacy shape.
            out_fps=out_fps if tail_pad > 0 else None,
        )
        return RetimeDecision(
            mode="filter", graph=graph, label=label,
            video_dur=expected + tail_pad,
        )

    try:
        await render_retimed_video(
            job_id=job_id, ffmpeg=ffmpeg, video_path=video_path,
            chunks=chunks, out_path=work_path, batch_size=batch_size,
            fps_norm=fps_norm, out_fps=out_fps, tail_pad_s=tail_pad,
            abort_check=abort_check,
        )
    except RetimeError as e:
        # Concat-demuxer rejection (odd container quirks) → one single-pass
        # retry while the chunk count is still tractable; encode failures
        # and aborts propagate to the caller's fallback ladder.
        if e.stage == "concat" and len(chunks) <= RETIME_SINGLE_PASS_RETRY_MAX:
            logger.warning(
                "video retime: concat join failed (%s); retrying as a "
                "single pass with %d chunks", e, len(chunks),
            )
            graph, label = build_chunk_filter_graph(
                chunks, "[0:v]", fps_norm=fps_norm, tail_pad_s=tail_pad,
                out_fps=out_fps if tail_pad > 0 else None,
            )
            return RetimeDecision(
                mode="filter", graph=graph, label=label,
                video_dur=expected + tail_pad,
            )
        raise
    actual = await probe_duration(work_path, allowed_root=_base)
    return RetimeDecision(
        mode="file", file_path=work_path,
        video_dur=float(actual) if actual else expected + tail_pad,
    )
