"""Turn measured speech habits of a source line into director vocabulary.

A line's acoustic summary is set against what is usual for its speaker, and
only pronounced differences are expressed as words. NumPy only; no learned
model, so results are repeatable on every platform.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Mapping, Optional, Sequence

import numpy as np
import soundfile as sf

from services.director import Direction

ANALYSIS_SR = 16000
MIN_BASELINE_LINES = 3
MAX_LINE_S = 60.0
MAX_TRACK_PASSES = 2.0

_FRAME = int(0.040 * ANALYSIS_SR)  # 640 samples
_HOP = int(0.010 * ANALYSIS_SR)  # 160 samples
_HOP_S = 0.010
_BLOCK_FRAMES = 4096  # how many frames are expanded to memory at one time
_PITCH_BLOCK = 1024
_FLOOR_DB = -55.0
_RANGE_DB = 30.0
_MIN_ACTIVE_S = 0.30
_F0_MIN, _F0_MAX = 70.0, 400.0
_VOICED_PEAK = 0.45
_MIN_VOICED_FRAMES = 5
_SMOOTH_FRAMES = 5
_NUCLEUS_RISE_DB = 3.0
_NUCLEUS_GAP_S = 0.080
_PITCH_JUMP_Z = 4.0
_LAG_LO = int(ANALYSIS_SR / _F0_MAX)
_LAG_HI = int(math.ceil(ANALYSIS_SR / _F0_MIN))
_FFT_SIZE = 2048

FEATURE_NAMES = ("pitch_st", "pitch_spread_st", "loudness_db", "rate_hz", "voiced_ratio")
_PITCH_FEATURES = ("pitch_st", "pitch_spread_st")
_SPREAD_FLOOR = {
    "pitch_st": 1.0,
    "pitch_spread_st": 1.0,
    "loudness_db": 2.0,
    "rate_hz": 0.5,
    "voiced_ratio": 0.08,
}


class AnalysisBudgetExceeded(ValueError):
    """Raised when the spans ask for implausibly more audio than the file has."""


# A channel average that keeps less than this share of the loudest channel's
# energy means the channels largely cancel (an out-of-phase pair).
_PHASE_CANCEL_RATIO = 0.5


def _downmix(audio: np.ndarray) -> np.ndarray:
    """Average channels, unless they cancel: then use the loudest channel.

    Out-of-phase stereo averages to near silence, which would read as "no
    speech". The loudest channel alone still carries the delivery.
    """
    mixed = audio.mean(axis=1)
    channel_rms = np.sqrt(np.mean(np.square(audio, dtype=np.float64), axis=0))
    loudest = int(np.argmax(channel_rms))
    mixed_rms = float(np.sqrt(np.mean(np.square(mixed, dtype=np.float64))))
    if mixed_rms < _PHASE_CANCEL_RATIO * float(channel_rms[loudest]):
        return audio[:, loudest]
    return mixed


@dataclass(frozen=True)
class SegmentSpan:
    id: str
    start: float
    end: float
    speaker_id: str = ""


@dataclass(frozen=True, slots=True)
class ProsodyFeatures:
    """Acoustic summary of one spoken line; pitch fields are absent when unvoiced."""

    active_s: float
    voiced_ratio: float
    rate_hz: float
    loudness_db: float
    pitch_st: Optional[float] = None
    pitch_spread_st: Optional[float] = None

    def get(self, name: str) -> Optional[float]:
        """Look a feature up by name; unknown names give ``None``."""
        return getattr(self, name, None)


@dataclass(frozen=True)
class Baseline:
    """What is typical for a group of lines: robust centre and spread per feature."""

    centre: Mapping[str, float]
    spread: Mapping[str, float]
    pitch_comparable: bool

    def z(self, features: ProsodyFeatures) -> dict[str, float]:
        """Score each available feature of ``features`` against this baseline."""
        scores: dict[str, float] = {}
        for name, mid in self.centre.items():
            if name not in self.spread:
                continue
            if name in _PITCH_FEATURES and not self.pitch_comparable:
                continue
            observed = features.get(name)
            if observed is None:
                continue
            scores[name] = (observed - mid) / self.spread[name]
        # A huge pitch excursion is more likely a tracking error (or another
        # voice) than real delivery, so pitch is not trusted for this line.
        if abs(scores.get("pitch_st", 0.0)) > _PITCH_JUMP_Z:
            for name in _PITCH_FEATURES:
                scores.pop(name, None)
        return scores


@dataclass(slots=True)
class MirrorResult:
    """Per-line outcome: the suggested direction text and the evidence for it."""

    id: str
    direction: str = ""
    tokens: dict[str, list[str]] = field(default_factory=dict)
    z: dict[str, float] = field(default_factory=dict)
    measured: bool = False

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-friendly form, with scores rounded to two places."""
        out = asdict(self)
        out["z"] = {name: round(value, 2) for name, value in self.z.items()}
        return out


def to_mono_analysis_rate(audio: np.ndarray, sr: int) -> np.ndarray:
    """Collapse to one channel and bring the rate to 16 kHz."""
    data = np.asarray(audio, dtype=np.float32)
    if data.ndim == 2:
        if data.shape[0] == 0:
            return np.zeros(0, dtype=np.float32)
        data = data[:, 0] if data.shape[1] == 1 else _downmix(data)
    data = np.asarray(data, dtype=np.float32).reshape(-1)
    if data.size == 0 or sr == ANALYSIS_SR:
        return data
    if sr > ANALYSIS_SR:
        width = int(sr // ANALYSIS_SR)
        if width > 1:
            # Cheap low-pass: average each sample with its neighbours.
            padded = np.concatenate(
                [np.zeros(1, dtype=np.float64), np.cumsum(data, dtype=np.float64)]
            )
            n = data.size
            hi = np.minimum(np.arange(n) + (width - width // 2), n)
            lo = np.maximum(hi - width, 0)
            data = ((padded[hi] - padded[lo]) / np.maximum(hi - lo, 1)).astype(np.float32)
    out_len = int(data.size * ANALYSIS_SR / sr)
    if out_len <= 0:
        return np.zeros(0, dtype=np.float32)
    positions = np.arange(out_len) * (sr / ANALYSIS_SR)
    return np.interp(positions, np.arange(data.size), data).astype(np.float32)


def _frame_block(signal: np.ndarray, first: int, count: int) -> np.ndarray:
    start = first * _HOP
    chunk = signal[start : start + (count - 1) * _HOP + _FRAME]
    return np.lib.stride_tricks.sliding_window_view(chunk, _FRAME)[::_HOP]


_WINDOW = np.hanning(_FRAME + 2)[1:-1]


def _block_pitch(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame fundamental estimate and the strength of its periodicity."""
    y = frames.astype(np.float64)
    y -= y.mean(axis=1, keepdims=True)
    y *= _WINDOW
    spectrum = np.fft.rfft(y, n=_FFT_SIZE, axis=1)
    corr = np.fft.irfft(spectrum * np.conj(spectrum), n=_FFT_SIZE, axis=1)
    lags = np.arange(_LAG_LO - 1, _LAG_HI + 2)
    energy = np.concatenate(
        [np.zeros((y.shape[0], 1)), np.cumsum(y * y, axis=1)], axis=1
    )
    lead = energy[:, _FRAME - lags]
    tail = energy[:, _FRAME][:, None] - energy[:, lags]
    norm = corr[:, lags] / np.sqrt(np.maximum(lead * tail, 1e-18))
    mid = norm[:, 1:-1]
    top = mid.max(axis=1)
    is_peak = (mid >= norm[:, :-2]) & (mid > norm[:, 2:]) & (mid >= 0.9 * top[:, None])
    # Prefer the shortest lag that is nearly as strong as the best one, so a
    # repeat of the true period is not mistaken for the period.
    pick = np.where(is_peak.any(axis=1), is_peak.argmax(axis=1), mid.argmax(axis=1))
    rows = np.arange(y.shape[0])
    a, b, c = norm[rows, pick], norm[rows, pick + 1], norm[rows, pick + 2]
    denom = a - 2.0 * b + c
    shift = np.where(np.abs(denom) > 1e-12, 0.5 * (a - c) / np.where(denom == 0, 1, denom), 0.0)
    lag = lags[pick + 1] + np.clip(shift, -1.0, 1.0)
    return ANALYSIS_SR / lag, b


def _count_nuclei(level: np.ndarray, floor_db: float) -> int:
    kernel = np.ones(_SMOOTH_FRAMES) / _SMOOTH_FRAMES
    padded = np.pad(level, _SMOOTH_FRAMES // 2, mode="constant", constant_values=floor_db)
    env = np.convolve(padded, kernel, mode="valid")
    if env.size < 3:
        return 0
    rising = env[1:-1] > env[:-2]
    candidates: list[int] = [int(k) + 1 for k in np.flatnonzero(rising & (env[1:-1] >= env[2:]))]
    min_gap = int(round(_NUCLEUS_GAP_S / _HOP_S))
    count = 0
    last = -1
    for i in candidates:
        valley = float(env[max(last, 0) : i + 1].min())
        if env[i] - valley >= _NUCLEUS_RISE_DB and (last < 0 or i - last >= min_gap):
            count += 1
            last = int(i)
    return count


def extract_features(audio: np.ndarray, sr: int) -> Optional[ProsodyFeatures]:
    signal = to_mono_analysis_rate(audio, sr)
    if signal.size < _FRAME:
        return None
    n_frames = 1 + (signal.size - _FRAME) // _HOP
    power = np.empty(n_frames, dtype=np.float64)
    for first in range(0, n_frames, _BLOCK_FRAMES):
        count = min(_BLOCK_FRAMES, n_frames - first)
        block = _frame_block(signal, first, count).astype(np.float64)
        power[first : first + count] = np.einsum("ij,ij->i", block, block) / _FRAME
    level = 10.0 * np.log10(np.maximum(power, 1e-12))
    threshold = max(_FLOOR_DB, float(level.max()) - _RANGE_DB)
    active = level > threshold
    n_active = int(active.sum())
    active_s = n_active * _HOP_S
    if active_s < _MIN_ACTIVE_S:
        return None

    voiced_f0: list[np.ndarray] = []
    active_idx = np.nonzero(active)[0]
    for offset in range(0, active_idx.size, _PITCH_BLOCK):
        chunk = active_idx[offset : offset + _PITCH_BLOCK]
        frames = np.stack([signal[i * _HOP : i * _HOP + _FRAME] for i in chunk])
        f0, peak = _block_pitch(frames)
        voiced_f0.append(f0[peak >= _VOICED_PEAK])
    f0_all = np.concatenate(voiced_f0) if voiced_f0 else np.zeros(0)
    n_voiced = int(f0_all.size)

    pitch_st = spread = None
    if n_voiced >= _MIN_VOICED_FRAMES:
        semitones = 12.0 * np.log2(f0_all / 100.0)
        pitch_st = float(np.median(semitones))
        spread = float(np.percentile(semitones, 90) - np.percentile(semitones, 10))

    loudness = float(10.0 * np.log10(max(float(power[active].mean()), 1e-12)))
    held = np.where(active, level, threshold)
    rate = _count_nuclei(held, threshold) / active_s
    return ProsodyFeatures(
        loudness_db=loudness,
        rate_hz=float(rate),
        voiced_ratio=n_voiced / n_active,
        active_s=float(active_s),
        pitch_st=pitch_st,
        pitch_spread_st=spread,
    )


def build_baseline(samples: Sequence[ProsodyFeatures], *, pitch_comparable: bool) -> Baseline:
    centre: dict[str, float] = {}
    spread: dict[str, float] = {}
    for name in FEATURE_NAMES:
        column = np.array(
            [x for x in map(lambda f: f.get(name), samples) if x is not None], dtype=float
        )
        if column.size == 0:
            continue
        mid = float(np.median(column))
        scaled_mad = 1.4826 * float(np.median(np.abs(column - mid)))
        centre[name] = mid
        spread[name] = scaled_mad if scaled_mad > _SPREAD_FLOOR[name] else _SPREAD_FLOOR[name]
    return Baseline(centre=centre, spread=spread, pitch_comparable=pitch_comparable)


# Each rule: (dimension, word, predicate over the score view). Within one
# dimension the first matching rule wins; dimensions are independent.
class _Scores:
    """Read-only view of z-scores with the derived quantities rules need."""

    def __init__(self, z: Mapping[str, float]):
        self.loud = z.get("loudness_db", 0.0)
        self.speed = z.get("rate_hz", 0.0)
        self.voice = z.get("voiced_ratio", 0.0)
        self.has_f0 = "pitch_st" in z or "pitch_spread_st" in z
        self.f0_level = z.get("pitch_st", 0.0)
        self.f0_motion = z.get("pitch_spread_st", 0.0)
        self.drive = (
            (self.loud + self.f0_level + self.f0_motion) / 3.0 if self.has_f0 else self.loud
        )


_Rule = tuple[str, str, Callable[[_Scores], bool]]

_RULES: tuple[_Rule, ...] = (
    ("energy", "urgent", lambda s: s.loud >= 1 and s.speed >= 1 and (s.f0_level >= 0.5 or not s.has_f0)),
    ("energy", "energetic", lambda s: s.drive >= 1 and s.loud >= 0.5),
    ("energy", "calm", lambda s: s.drive <= -1 and s.loud <= -0.5),
    ("intimacy", "announcing", lambda s: s.loud >= 1.5 and s.speed <= -0.5),
    ("pace", "quick", lambda s: s.speed >= 1),
    ("pace", "slow", lambda s: s.speed <= -1),
)


def infer_direction(z: Mapping[str, float]) -> Direction:
    """Map a line's z-scores onto director tokens."""
    view = _Scores(z)
    chosen: dict[str, list[str]] = {}
    if view.voice <= -2 and view.loud <= -1:
        # Mostly unvoiced and quiet: a whisper overrides every other reading.
        chosen["intimacy"] = ["whispered"]
    else:
        for dimension, word, applies in _RULES:
            if dimension not in chosen and applies(view):
                chosen[dimension] = [word]
    return Direction(tokens=chosen, source="prosody-mirror", method="prosody")


def mirror(
    features: Mapping[str, Optional[ProsodyFeatures]],
    speakers: Mapping[str, str],
) -> dict[str, MirrorResult]:
    """Suggest a direction for each measured line relative to its speaker's norm."""
    results = {key: MirrorResult(id=key) for key in features}
    measured = {key: value for key, value in features.items() if value is not None}
    if len(measured) < MIN_BASELINE_LINES:
        return results

    owner = {key: speakers.get(key) or "" for key in measured}
    by_speaker: dict[str, list[ProsodyFeatures]] = {}
    for key, value in measured.items():
        if owner[key]:
            by_speaker.setdefault(owner[key], []).append(value)

    # Pitch is only comparable across the pooled lines when they are known to
    # come from one and the same voice.
    single_voice = len(by_speaker) == 1 and all(owner.values())
    pooled = build_baseline(list(measured.values()), pitch_comparable=single_voice)
    per_speaker = {
        name: build_baseline(lines, pitch_comparable=True)
        for name, lines in by_speaker.items()
        if len(lines) >= MIN_BASELINE_LINES
    }

    for key, value in measured.items():
        baseline = per_speaker.get(owner[key], pooled)
        scores = baseline.z(value)
        direction = infer_direction(scores)
        result = results[key]
        result.direction = direction.instruct_prompt()
        result.tokens = {dim: list(words) for dim, words in direction.tokens.items() if words}
        result.z = scores
        result.measured = True
    return results


def mirror_file(path: str, spans: Sequence[SegmentSpan]) -> list[MirrorResult]:
    with sf.SoundFile(path) as handle:
        rate = int(handle.samplerate)
        n_total = int(handle.frames)
        longest = int(MAX_LINE_S * rate)

        def clamp(span: SegmentSpan) -> tuple[int, int]:
            lo = min(max(int(span.start * rate), 0), n_total)
            hi = min(max(int(span.end * rate), 0), n_total, lo + longest)
            return lo, hi

        ranges = [clamp(span) for span in spans]
        wanted_s = sum(hi - lo for lo, hi in ranges) / rate
        limit_s = MAX_TRACK_PASSES * n_total / rate + MAX_LINE_S
        if wanted_s > limit_s:
            raise AnalysisBudgetExceeded(
                f"{wanted_s:.0f}s of segments requested from a {n_total / rate:.0f}s track"
            )

        measured: dict[str, Optional[ProsodyFeatures]] = {}
        for span, (lo, hi) in zip(spans, ranges):
            if hi <= lo:
                measured[span.id] = None
                continue
            handle.seek(lo)
            block = handle.read(hi - lo, dtype="float32", always_2d=True)
            measured[span.id] = extract_features(block, rate)
    by_id = mirror(measured, {span.id: span.speaker_id for span in spans})
    return [by_id[span.id] for span in spans]
