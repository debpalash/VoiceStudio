"""
Invisible + visible audio watermarking for VoiceStudio.

Two layers:
  1. **Invisible** — AudioSeal (Meta) embeds imperceptible neural watermarks
     that survive compression, resampling, and editing. Encodes a 16-bit
     message identifying VoiceStudio as the source.
  2. **Visible** — Optional audio signature tone prepended to exports;
     ffmpeg-based logo overlay for video exports.

Usage:
    from services.watermark import embed_watermark, detect_watermark

    # Embed (returns same shape tensor, watermarked)
    watermarked = embed_watermark(waveform, sample_rate)

    # Detect (returns dict with confidence + metadata)
    result = detect_watermark(waveform, sample_rate)
"""

from __future__ import annotations

import contextlib
import logging

from core.render_trace import timed as _render_timed
import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

import torch

from core.prefs import resolve

logger = logging.getLogger("omnivoice.watermark")


# ── Lazy-loaded AudioSeal models ──────────────────────────────────────────
# Loaded on first use so cold-start isn't penalised when watermarking is off.
_generator = None
_detector = None
_audioseal_available: Optional[bool] = None
# Monotonic stamp of the last embed/detect, for the idle release below.
_last_used = 0.0
# Per-model locks for the lazy builds below: the startup prefetch thread
# races the first embed, and both must share ONE build (a double load doubles
# the cold-start cost the prefetch exists to hide). One lock PER MODEL — a
# single shared lock made the ~42s generator prefetch block unrelated detector
# loads and the idle reaper behind it. release_idle_models acquires both, in
# this fixed order (nothing else nests them, so no cycle is possible).
_generator_lock = threading.Lock()
_detector_lock = threading.Lock()

# True when the generator exists ONLY because the startup prefetch built it
# and no embed/detect has used it since. The idle reaper grants one extra
# idle window before dropping such a model, so a first synthesis at minute
# 20 still finds it warm (code-review finding 2 on the prefetch PR).
_prefetched_unused = False

# 16-bit message: "OM" in ASCII = 0x4F 0x4D = 0100_1111 0100_1101
# This is our signature — every VoiceStudio-generated audio carries it.
OMNI_MESSAGE = [0, 1, 0, 0, 1, 1, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1]

# Watermark ops run chunk-by-chunk: AudioSeal's activation memory grows
# linearly with input length — a single multi-minute waveform demands a
# multi-GB CPU buffer, which OOM'd a 16 GB machine mid-generate (#1045).
# 30 s bounds each call to tens of MB; the 16-bit message repeats throughout
# the audio, so per-chunk embedding/detection is equivalent.
_CHUNK_SECONDS = 30


# AudioSeal vendors moshi's ``@torch_compile_lazy`` on SEANetEncoder.forward,
# so the first EMBED — not the model load, which prefetch already warms —
# calls torch.compile and drops into Inductor's C++ codegen. On hosts whose
# C++ toolchain can't serve Inductor that compile raises CppCompileError, the
# embed fail-opens, and audio ships unmarked: a macOS arm64 deployment lost
# provenance marking on 10/10 takes while paying 30-40 s for the first failed
# compile and 5-8 s for each later one (#1615).
#
# The compile is pure cost even where it succeeds. Measured on an M3 (5 s of
# 24 kHz audio, three consecutive embeds): compiled 9.70 / 0.26 / 0.23 s vs
# eager 0.30 / 0.28 / 0.27 s — a ~10 s first-embed tax to save ~0.03 s per
# later embed, on CPU work that is already bounded by the 30 s chunk loop.
# So watermarking runs eager on every platform.
def _moshi_compile_module():
    """AudioSeal's vendored moshi compile switch module, or None.

    Resolved per call rather than at import: ``_check_available()`` is what
    guarantees audioseal is importable, and it runs later than this module.
    """
    try:
        from audioseal.libs.moshi.utils import compile as moshi_compile
    except Exception:  # noqa: BLE001 — any import shape change degrades, not crashes
        return None
    return moshi_compile


_eager_lock = threading.Lock()
#: Depth of nested/concurrent eager scopes, and the switch value to put back
#: when the last one exits. One dict rather than two module scalars: the
#: fields are only meaningful together, and only under _eager_lock.
_eager_state: dict = {"depth": 0, "saved": None}
_eager_guard_warned = False


def _warn_missing_eager_guard() -> None:
    global _eager_guard_warned
    _eager_guard_warned = True
    logger.info(
        "audioseal's no_compile switch is unavailable — watermarking may run "
        "through torch.compile and pay (or fail) an Inductor C++ compile (#1615)."
    )


@contextlib.contextmanager
def _eager_audioseal():
    """Run the AudioSeal model eagerly, restoring the switch on the way out.

    Upstream's own ``no_compile()`` saves and restores ``_compile_disabled``
    per call, which is not safe when two watermark calls overlap: the first to
    exit restores False while the second is still mid-embed, handing it back
    the compile this whole fix exists to avoid. So the flag is reference
    counted here — it goes True on the outermost entry and only comes back on
    the outermost exit — rather than serializing embeds behind a lock, which
    would cost real throughput on concurrent generations.

    Degrades to a plain call if a future audioseal drops the helper
    (``tests/test_watermark_no_torch_compile_1615.py`` fails loudly on that
    upgrade rather than letting the compile creep back in).
    """
    moshi = _moshi_compile_module()
    if moshi is None:
        if not _eager_guard_warned:
            _warn_missing_eager_guard()
        yield
        return
    with _eager_lock:
        if _eager_state["depth"] == 0:
            _eager_state["saved"] = moshi._compile_disabled
        _eager_state["depth"] += 1
        moshi._compile_disabled = True
    try:
        yield
    finally:
        with _eager_lock:
            _eager_state["depth"] -= 1
            if _eager_state["depth"] == 0:
                moshi._compile_disabled = _eager_state["saved"]
                _eager_state["saved"] = None


def _iter_chunks(audio: torch.Tensor, sample_rate: int):
    """Yield ≤ ~_CHUNK_SECONDS slices of (batch, channels, samples) audio
    along the time axis. A sub-second tail is folded into the previous chunk
    (AudioSeal embeds poorly on very short segments)."""
    total = audio.shape[-1]
    step = _CHUNK_SECONDS * sample_rate
    starts = list(range(0, total, step))
    if len(starts) > 1 and total - starts[-1] < sample_rate:
        starts.pop()
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        yield audio[..., start:end]


def _check_available() -> bool:
    """Check if AudioSeal is installed and importable."""
    global _audioseal_available
    if _audioseal_available is None:
        try:
            import audioseal  # noqa: F401
            _audioseal_available = True
        except ImportError:
            _audioseal_available = False
            logger.info("audioseal not installed — invisible watermarking disabled")
    return _audioseal_available


def _get_generator(mark_prefetched: bool = False):
    """Lazy-load the AudioSeal generator model.

    Owns the idle-reaper grace in ONE critical section: the startup prefetch
    claims it (``mark_prefetched=True``) only when THIS call builds the model,
    and every other call (a real embed) consumes it — no call-site blocks, no
    window between two lock scopes where the claim could land on an
    already-used model.
    """
    global _generator, _last_used, _prefetched_unused
    with _generator_lock:
        _last_used = time.monotonic()
        if _generator is None:
            from audioseal import AudioSeal
            _generator = AudioSeal.load_generator("audioseal_wm_16bits")
            _generator.eval()
            logger.info("AudioSeal generator loaded (16-bit message mode)")
            _prefetched_unused = mark_prefetched
        elif not mark_prefetched:
            _prefetched_unused = False
        return _generator


def _get_detector():
    """Lazy-load the AudioSeal detector model."""
    global _detector, _last_used
    with _detector_lock:
        _last_used = time.monotonic()
        if _detector is None:
            from audioseal import AudioSeal
            _detector = AudioSeal.load_detector("audioseal_detector_16bits")
            _detector.eval()
            logger.info("AudioSeal detector loaded (16-bit message mode)")
        return _detector


def _generator_checkpoint_cached() -> bool:
    """Return whether AudioSeal can warm without contacting Hugging Face.

    AudioSeal 0.2 stores the checkpoint in ``<cache>/audioseal`` even though
    it uses huggingface_hub to fetch it. Keep startup local-first: an ordinary
    boot may consume that file, but must never turn prefetch into a download.
    """
    cache_root = os.environ.get("AUDIOSEAL_CACHE_DIR") or os.environ.get(
        "XDG_CACHE_HOME"
    )
    root = Path(cache_root).expanduser() if cache_root else Path.home() / ".cache"
    return (root / "audioseal" / "generator_base.pth").is_file()


def prefetch_generator(*, allow_download: bool = False) -> None:
    """Warm the AudioSeal generator eagerly (startup background thread).

    The first ``mark_synthetic`` otherwise pays the audioseal import plus the
    generator load inline — measured at ~42 s on a cold filesystem (2026-08-17
    macOS deployment), serialized inside the first synthesis and 3 s short of
    a 90 s client timeout. Warming here overlaps that span with the TTS model
    load. No-op when watermarking is off or audioseal is absent; a failure
    logs and leaves the lazy path to retry on first embed. Default startup is
    also cache-only; a download is allowed only when the user explicitly set
    ``OMNIVOICE_PRELOAD_WATERMARK=1``.
    """
    try:
        if not will_mark():
            logger.debug("Watermark prefetch skipped (disabled or audioseal absent)")
            return
        if not allow_download and not _generator_checkpoint_cached():
            logger.info("Watermark prefetch skipped: AudioSeal checkpoint is not cached")
            return
        _get_generator(mark_prefetched=True)
        logger.info("AudioSeal generator prefetched in the background")
    except Exception:
        logger.warning(
            "Watermark prefetch failed; the first embed will retry inline",
            exc_info=True,
        )


def release_idle_models(idle_seconds: float, *, now: Optional[float] = None) -> bool:
    """Drop the AudioSeal models if nothing has watermarked for ``idle_seconds``.

    These load on the first embed or detect and then stayed resident for the
    life of the process — the same bargain the TTS model and the capture ASR
    both stopped making. Modest next to those (they run on CPU, so this is
    system RAM rather than VRAM), but a batch job that watermarks once leaves
    them held forever afterwards, and the machines that hit memory pressure are
    the ones running batches.

    Returns True if anything was released. Never raises: this runs from the
    idle reaper, which must survive it.
    """
    global _generator, _detector, _prefetched_unused
    with _generator_lock, _detector_lock:
        if _generator is None and _detector is None:
            return False
        stamp = time.monotonic() if now is None else float(now)
        if stamp - _last_used < idle_seconds:
            return False
        if _prefetched_unused:
            # The startup prefetch built the generator and nothing has used
            # it yet. Drop the grace (one extra idle window only) instead of
            # the model, so a first synthesis shortly after boot still finds
            # it warm — the exact scenario the prefetch exists for.
            _prefetched_unused = False
            logger.info(
                "Idle watermark models are prefetch-warmed but unused; "
                "granting one more idle window before releasing."
            )
            return False
        # Under the locks so a release racing the prefetch or a first embed
        # can't wipe a model the lazy path just built.
        _generator = None
        _detector = None
    logger.info("Idle timeout reached. Released the AudioSeal watermark models.")
    return True


def is_enabled() -> bool:
    """Check if invisible watermarking is enabled in user preferences."""
    return resolve("watermark.invisible", default=True) is not False


def will_mark() -> bool:
    """True when :func:`mark_synthetic` would actually embed right now —
    the user pref is on AND AudioSeal is importable. Producers that cache
    rendered audio fold this into their cache keys so a clip rendered while
    marking was off/unavailable can never be served for a request made while
    marking is on (see audiobook.py's chapter cache, #1169)."""
    return is_enabled() and _check_available()


def is_visible_audio_enabled() -> bool:
    """Check if audible branding tone is enabled for exports."""
    return resolve("watermark.visible_audio", default=False) is True


def is_visible_video_enabled() -> bool:
    """Check if video logo overlay is enabled for exports."""
    return resolve("watermark.visible_video", default=True) is not False


# ── Invisible Watermark ───────────────────────────────────────────────────


def mark_synthetic(
    waveform: torch.Tensor,
    sample_rate: int,
    *,
    context: str,
    force: bool = False,
) -> torch.Tensor:
    """THE provenance chokepoint for synthetic audio (#1169).

    Every path that produces synthetic speech routes its output through this
    call at the tensor stage, right before the audio leaves the app (HTTP
    response, WebSocket/SSE frame, or a file the app saves/serves/exports).
    EU AI Act Art. 50(2) — applicable 2026-08-02, expressly carved out of the
    open-source exemption (Art. 2(12)) — requires synthetic audio to carry a
    machine-readable mark; the invisible AudioSeal watermark is that mark.

    Coverage used to grow call-site-by-call-site (three separate
    ``embed_watermark`` calls) and a fourth producer shipped unmarked
    (#1169: ``/v1/audio/speech``). Producers now share this single named seam,
    and ``tests/test_watermark_route_coverage.py`` structurally asserts every
    synthesis call site references it — a new audio route can't silently ship
    without provenance marking again.

    Semantics are exactly :func:`embed_watermark`'s (named delegation, not new
    policy): gated on the user's ``watermark.invisible`` pref unless
    ``force=True``, a no-op when AudioSeal isn't installed, and it NEVER
    raises — on any failure the original audio passes through unchanged, so
    marking can never break generation (the same degrade-don't-block contract
    generation.py has always had).

    Args:
        waveform: Audio tensor, any shape embed_watermark accepts.
        sample_rate: Sample rate of the audio.
        context: Names the producing route/service (e.g.
            ``"openai_compat.speech"``) for debug logs and the coverage test.
        force: Bypass the user pref (persona bundles mandate a mark).

    Returns:
        The watermarked waveform (same shape), or the input unchanged when
        marking is off/unavailable/failed.
    """
    marked = embed_watermark(waveform, sample_rate, force=force)
    if marked is not waveform:
        logger.debug("synthetic audio provenance-marked (%s)", context)
    return marked


@_render_timed('watermark')
async def mark_synthetic_async(
    waveform: torch.Tensor,
    sample_rate: int,
    *,
    context: str,
    force: bool = False,
    timeout: float | None = None,
) -> torch.Tensor:
    """Dispatch marking without letting a draining pool lose finished audio."""
    import asyncio
    import functools

    from services.model_manager import (
        GpuJobTimeoutError,
        GpuPoolBusyError,
        get_watermark_pool,
        run_on_gpu_pool_guarded,
    )

    try:
        pool = get_watermark_pool()
    except RuntimeError:
        logger.warning("Watermark skipped while the prior worker is shutting down")
        return waveform

    job = functools.partial(
        mark_synthetic, waveform, sample_rate, context=context, force=force
    )
    try:
        if timeout is not None:
            return await run_on_gpu_pool_guarded(
                job, what="Audio watermark", timeout=timeout, executor=pool
            )
        return await asyncio.get_running_loop().run_in_executor(pool, job)
    except (GpuJobTimeoutError, GpuPoolBusyError):
        # Watermarking is provenance best-effort: a typed execution overrun or
        # queue saturation must not discard synthesis that already completed.
        logger.warning("Watermark skipped after its bounded dispatch expired")
        return waveform
    except asyncio.CancelledError:
        # A queued future is cancelled during pool teardown. Caller-driven
        # cancellation while the pool is live must retain normal semantics.
        if not pool.is_shutdown():
            raise
        logger.warning("Watermark skipped while the pool is shutting down")
        return waveform
    except RuntimeError:
        # Shutdown may begin after admission but before Executor.submit().
        # Preserve unrelated worker failures; only lifecycle rejection is
        # fail-open because finished synthesis must not be lost to teardown.
        if not pool.is_shutdown():
            raise
        logger.warning("Watermark skipped while the pool is shutting down")
        return waveform


_AUDIOSEAL_SAMPLE_RATE = 16000


def _audioseal_resample(audio: torch.Tensor, source: int, target: int) -> torch.Tensor:
    """Normalize model input without changing the exported audio's sample rate."""
    if source <= 0 or target <= 0:
        raise ValueError("Audio sample rates must be positive")
    if source == target:
        return audio
    from torchaudio.functional import resample
    return resample(audio, source, target)


def _embed_chunk(generator, audio, sample_rate, message):
    """Resample only the watermark residual back, preserving original high frequencies."""
    model_audio = _audioseal_resample(audio, sample_rate, _AUDIOSEAL_SAMPLE_RATE)
    marked = generator(model_audio, sample_rate=_AUDIOSEAL_SAMPLE_RATE, message=message)
    if sample_rate == _AUDIOSEAL_SAMPLE_RATE:
        return marked
    delta = _audioseal_resample(marked - model_audio, _AUDIOSEAL_SAMPLE_RATE, sample_rate)
    missing = audio.shape[-1] - delta.shape[-1]
    if missing > 0:
        delta = torch.nn.functional.pad(delta, (0, missing))
    return audio + delta[..., :audio.shape[-1]]


@torch.no_grad()
def embed_watermark(
    waveform: torch.Tensor,
    sample_rate: int,
    message: Optional[list[int]] = None,
    *,
    force: bool = False,
) -> torch.Tensor:
    """
    Embed an imperceptible watermark into the audio waveform.

    Args:
        waveform: Audio tensor of shape (channels, samples) or (1, channels, samples)
        sample_rate: Sample rate of the audio
        message: Optional 16-bit message (list of 0/1). Defaults to OMNI_MESSAGE.
        force: Keyword-only. When True, bypass the user's invisible-watermark
            preference (``is_enabled()``) and embed regardless — used by the
            persona-preview path, which mandates a watermark at package time.
            It does NOT bypass availability: when AudioSeal isn't installed the
            call still no-ops and returns the input unchanged. Existing
            positional call sites default to ``force=False`` (unchanged).

    Returns:
        Watermarked waveform (same shape as input).
    """
    if (not force and not is_enabled()) or not _check_available():
        return waveform

    try:
        generator = _get_generator()
        msg = torch.tensor(message or OMNI_MESSAGE, dtype=torch.int32).unsqueeze(0)

        # AudioSeal expects (batch, channels, samples) — normalise input
        original_shape = waveform.shape
        if waveform.dim() == 2:
            audio = waveform.unsqueeze(0)  # (1, C, S)
        elif waveform.dim() == 1:
            audio = waveform.unsqueeze(0).unsqueeze(0)  # (1, 1, S)
        else:
            audio = waveform

        # AudioSeal 0.2 ignores sample_rate: normalize each bounded chunk
        # explicitly, then add only the resampled watermark to the source.
        with _eager_audioseal():
            watermarked = torch.cat(
                [
                    _embed_chunk(generator, seg, sample_rate, msg)
                    for seg in _iter_chunks(audio, sample_rate)
                ],
                dim=-1,
            )

        # Restore original shape
        if len(original_shape) == 2:
            watermarked = watermarked.squeeze(0)
        elif len(original_shape) == 1:
            watermarked = watermarked.squeeze(0).squeeze(0)

        return watermarked

    except Exception as e:
        logger.warning("Watermark embedding failed (passing through original): %s", e, exc_info=True)
        return waveform


@torch.no_grad()
def detect_watermark(
    waveform: torch.Tensor,
    sample_rate: int,
) -> dict:
    """
    Detect whether audio contains a VoiceStudio watermark.

    Args:
        waveform: Audio tensor of shape (channels, samples)
        sample_rate: Sample rate of the audio

    Returns:
        Dict with keys:
            is_watermarked: bool
            confidence: float (0.0–1.0)
            message_bits: str (decoded 16-bit message)
            is_omnivoice: bool (true if message matches OMNI_MESSAGE)
    """
    if not _check_available():
        return {
            "is_watermarked": False,
            "confidence": 0.0,
            "message_bits": "",
            "is_omnivoice": False,
            "error": "audioseal not installed",
        }

    try:
        detector = _get_detector()

        # Normalise shape to (batch, channels, samples)
        if waveform.dim() == 2:
            audio = waveform.unsqueeze(0)
        elif waveform.dim() == 1:
            audio = waveform.unsqueeze(0).unsqueeze(0)
        else:
            audio = waveform

        # Detect per chunk and keep the best hit: bounds memory the same way
        # embedding does, and a splice where only part of the file is
        # VoiceStudio audio still registers (a whole-file average would dilute it).
        best_conf, decoded_msg = -1.0, None
        with _eager_audioseal():
            for seg in _iter_chunks(audio, sample_rate):
                model_audio = _audioseal_resample(seg, sample_rate, _AUDIOSEAL_SAMPLE_RATE)
                result = detector.detect_watermark(model_audio, sample_rate=_AUDIOSEAL_SAMPLE_RATE, message_threshold=0.5)
                seg_conf = float(result[0]) if isinstance(result, tuple) else 0.0
                if seg_conf > best_conf:
                    best_conf = seg_conf
                    decoded_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else None
        confidence = max(best_conf, 0.0)

        # Decode message bits
        message_bits = ""
        is_omnivoice = False
        if decoded_msg is not None:
            try:
                bits = decoded_msg.squeeze().tolist()
                if isinstance(bits, list):
                    message_bits = "".join(str(int(b > 0.5)) for b in bits)
                    decoded_list = [int(b > 0.5) for b in bits]
                    is_omnivoice = decoded_list == OMNI_MESSAGE
            except Exception:
                pass

        return {
            "is_watermarked": confidence > 0.5,
            "confidence": round(confidence, 4),
            "message_bits": message_bits,
            "is_omnivoice": is_omnivoice,
            "source": "VoiceStudio" if is_omnivoice else "unknown",
        }

    except Exception as e:
        logger.warning("Watermark detection failed: %s", e, exc_info=True)
        return {
            "is_watermarked": False,
            "confidence": 0.0,
            "message_bits": "",
            "is_omnivoice": False,
            "error": str(e),
        }


# ── Visible Audio Brand ──────────────────────────────────────────────────

def generate_brand_tone(sample_rate: int = 24000, duration_s: float = 0.4) -> torch.Tensor:
    """
    Generate a short, distinctive audio signature tone.

    A soft ascending three-note chime (C5→E5→G5) that serves as the
    VoiceStudio "sound logo". Gentle enough for professional use.

    Returns:
        Tensor of shape (1, samples).
    """
    notes_hz = [523.25, 659.25, 783.99]  # C5, E5, G5
    note_dur = duration_s / len(notes_hz)
    samples_per_note = int(note_dur * sample_rate)
    total_samples = samples_per_note * len(notes_hz)

    tone = torch.zeros(1, total_samples)
    t = torch.linspace(0, note_dur, samples_per_note)

    for idx, freq in enumerate(notes_hz):
        # Sine wave with exponential decay envelope
        envelope = torch.exp(-t * 6.0) * 0.15  # quiet — 15% amplitude
        wave = torch.sin(2 * math.pi * freq * t) * envelope
        start = idx * samples_per_note
        tone[0, start : start + samples_per_note] = wave

    # Fade out the last 20%
    fade_len = int(total_samples * 0.2)
    if fade_len > 0:
        tone[0, -fade_len:] *= torch.linspace(1.0, 0.0, fade_len)

    return tone


def apply_audio_brand(
    waveform: torch.Tensor,
    sample_rate: int,
) -> torch.Tensor:
    """
    Prepend the VoiceStudio brand tone to a waveform (for final exports only).

    Returns:
        Tensor with brand tone + original audio concatenated.
    """
    if not is_visible_audio_enabled():
        return waveform

    brand = generate_brand_tone(sample_rate=sample_rate)
    # Add 100ms silence gap between brand and content
    gap = torch.zeros(1, int(0.1 * sample_rate))
    return torch.cat([brand, gap, waveform], dim=-1)


# ── Video Logo Overlay ────────────────────────────────────────────────────

def get_ffmpeg_overlay_args(logo_path: str, duration_s: float = 5.0) -> list[str]:
    """
    Build ffmpeg filter args to overlay the VoiceStudio logo in the bottom-right
    corner with a fade-out after `duration_s` seconds.

    Returns:
        List of ffmpeg filter_complex args.
    """
    if not is_visible_video_enabled():
        return []

    # Scale logo to 64px height, place bottom-right with 20px padding,
    # fade out after duration_s seconds.
    filter_str = (
        f"[1:v]scale=-1:64,format=rgba,"
        f"fade=t=out:st={duration_s - 1}:d=1:alpha=1[logo];"
        f"[0:v][logo]overlay=W-w-20:H-h-20:enable='lte(t,{duration_s})'"
    )
    return ["-filter_complex", filter_str]
