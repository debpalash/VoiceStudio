"""Prosody Mirror — carry the original performance into the dub.

Directorial AI (``services/director.py``) lets a user type "make line 14
urgent" and routes that through translation, TTS ``instruct`` and the
speech-rate target. Prosody Mirror writes those directions *for* the user by
listening to the source actor: each line's pitch, pitch movement, loudness,
syllable rate and voicing are measured on the separated vocals track and
compared against the same speaker's own baseline across the whole video. A
line delivered markedly louder, higher and faster than that speaker usually
talks becomes ``urgent, quick``; a near-unvoiced line becomes ``whispered``.

Design constraints:

* **Speaker-relative.** A deep voice is not "calm" and a shouted line from a
  habitually loud speaker is not "energetic" — every feature is a robust
  z-score against the speaker's own median/MAD. Speakers with too few lines,
  and lines without a speaker, borrow the pooled baseline, and then pitch is
  ignored, since pitch levels do not transfer between voices. A pitch far
  outside the speaker's range is treated as a second voice sharing the label,
  and pitch never sets energy unless loudness agrees.
* **Measurable dimensions only.** Energy, pace and intimacy follow from
  acoustics. Emotion does not reliably follow from four scalar features, so
  it is never guessed; the user or the LLM director owns it.
* **Stable contract.** Output is plain taxonomy words that
  ``director._heuristic_parse`` recognises, so mirrored directions behave
  exactly like typed ones with or without an LLM configured.
* **Pure NumPy.** No model and no platform-specific code: identical output on
  macOS, Windows and Linux, and unit-testable with synthetic audio.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from services.director import Direction

ANALYSIS_SR = 16000
FRAME_S = 0.040
HOP_S = 0.010
F0_MIN_HZ = 70.0
F0_MAX_HZ = 400.0
VOICING_THRESHOLD = 0.45
SILENCE_FLOOR_DBFS = -55.0
ACTIVE_RANGE_DB = 30.0
MIN_ACTIVE_S = 0.30
MIN_VOICED_FRAMES = 5
NUCLEUS_PROMINENCE_DB = 3.0
NUCLEUS_MIN_GAP_S = 0.08
MIN_BASELINE_LINES = 3
# Beyond this many sigmas a line's pitch is another voice under the same
# label (diarization off or merged), not a delivery change.
VOICE_OUTLIER_Z = 4.0
# Frames analysed per block, bounding memory on arbitrarily long lines.
BLOCK_FRAMES = 2048
# A delivery is a line-level property; an unsegmented stretch longer than
# this is measured from its opening, which bounds read size and memory.
MAX_LINE_S = 60.0
# Total seconds one request may analyse, as a multiple of the track length:
# real timelines overlap only where speakers talk over each other, so this
# admits every genuine edit while refusing requests that re-read the same
# audio over and over.
MAX_TRACK_PASSES = 2.0


class AnalysisBudgetExceeded(ValueError):
    """The requested spans add up to far more audio than the track holds."""

_MAD_TO_SIGMA = 1.4826
_FEATURE_FLOORS = {
    "pitch_st": 1.0,
    "pitch_spread_st": 1.0,
    "loudness_db": 2.0,
    "rate_hz": 0.5,
    "voiced_ratio": 0.08,
}


@dataclass(frozen=True)
class ProsodyFeatures:
    """Acoustic summary of one spoken line."""

    loudness_db: float
    rate_hz: float
    voiced_ratio: float
    active_s: float
    pitch_st: Optional[float] = None
    pitch_spread_st: Optional[float] = None

    def get(self, name: str) -> Optional[float]:
        return getattr(self, name)


@dataclass(frozen=True)
class Baseline:
    """Robust per-feature centre and spread for one speaker (or the pool)."""

    centre: Mapping[str, float]
    spread: Mapping[str, float]
    pitch_comparable: bool

    def z(self, features: ProsodyFeatures) -> dict[str, float]:
        scores: dict[str, float] = {}
        for name, centre in self.centre.items():
            if name.startswith("pitch") and not self.pitch_comparable:
                continue
            value = features.get(name)
            if value is not None:
                scores[name] = (value - centre) / self.spread[name]
        if abs(scores.get("pitch_st", 0.0)) > VOICE_OUTLIER_Z:
            scores.pop("pitch_st")
            scores.pop("pitch_spread_st", None)
        return scores


@dataclass(frozen=True)
class SegmentSpan:
    """The part of a dub segment the analyzer needs."""

    id: str
    start: float
    end: float
    speaker_id: str = ""


@dataclass
class MirrorResult:
    id: str
    direction: str = ""
    tokens: dict[str, list[str]] = field(default_factory=dict)
    z: dict[str, float] = field(default_factory=dict)
    measured: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "tokens": self.tokens,
            "z": {name: round(value, 2) for name, value in self.z.items()},
            "measured": self.measured,
        }


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


def to_mono_analysis_rate(audio: np.ndarray, sr: int) -> np.ndarray:
    """Downmix and resample to ``ANALYSIS_SR`` with a box pre-filter.

    Pitch sits below 400 Hz, so an averaging anti-alias filter followed by
    linear interpolation is accurate enough and needs no resampling library.
    """
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = _downmix(mono)
    mono = mono.reshape(-1)
    if sr == ANALYSIS_SR or mono.size == 0:
        return mono
    if sr > ANALYSIS_SR:
        width = int(round(sr / ANALYSIS_SR))
        if width > 1:
            mono = np.convolve(mono, np.full(width, 1.0 / width, dtype=np.float32), mode="same")
    target_len = max(1, int(round(mono.size * ANALYSIS_SR / sr)))
    positions = np.linspace(0.0, mono.size - 1, target_len)
    return np.interp(positions, np.arange(mono.size), mono).astype(np.float32)


def _frame_pitch(frames: np.ndarray, sr: int) -> np.ndarray:
    """Per-frame F0 in Hz by normalised autocorrelation; NaN where unvoiced."""
    n = frames.shape[1]
    lo = int(sr / F0_MAX_HZ)
    hi = min(n - 2, int(sr / F0_MIN_HZ))
    centred = (frames - frames.mean(axis=1, keepdims=True)) * np.hanning(n)
    # Circular autocorrelation is exact for every lag up to nfft - n, and
    # only lags up to ``hi`` (plus one for interpolation) are ever read.
    nfft = 1 << (n + hi + 1).bit_length()
    spectrum = np.fft.rfft(centred, nfft, axis=1)
    acf = np.fft.irfft(np.abs(spectrum) ** 2, nfft, axis=1)[:, :n]
    energy = acf[:, :1]
    acf = np.divide(acf, energy, out=np.zeros_like(acf), where=energy > 0)

    window = acf[:, lo : hi + 1]
    best = np.argmax(window, axis=1)
    peak = window[np.arange(window.shape[0]), best]
    lag = (best + lo).astype(np.float64)

    left = acf[np.arange(acf.shape[0]), np.maximum(best + lo - 1, 0)]
    right = acf[np.arange(acf.shape[0]), np.minimum(best + lo + 1, n - 1)]
    curvature = left - 2 * peak + right
    offset = np.divide(
        0.5 * (left - right), curvature, out=np.zeros_like(curvature), where=curvature < 0
    )
    lag = lag + np.clip(offset, -0.5, 0.5)
    return np.where(peak >= VOICING_THRESHOLD, sr / lag, np.nan)


def _count_nuclei(envelope_db: np.ndarray) -> int:
    """Count syllable nuclei: envelope peaks that rise a clear step above the
    preceding valley and sit far enough apart to be separate syllables."""
    min_gap = max(1, int(round(NUCLEUS_MIN_GAP_S / HOP_S)))
    count = 0
    valley = math.inf
    last_peak = -min_gap
    for i in range(1, envelope_db.size - 1):
        value = envelope_db[i]
        valley = min(valley, value)
        is_peak = value >= envelope_db[i - 1] and value > envelope_db[i + 1]
        if is_peak and value - valley >= NUCLEUS_PROMINENCE_DB and i - last_peak >= min_gap:
            count += 1
            last_peak = i
            valley = value
    return count


def extract_features(audio: np.ndarray, sr: int) -> Optional[ProsodyFeatures]:
    """Measure one line. Returns None when too little speech is present."""
    signal = to_mono_analysis_rate(audio, sr)
    frame_len = int(FRAME_S * ANALYSIS_SR)
    hop = int(HOP_S * ANALYSIS_SR)
    if signal.size < frame_len:
        return None

    frames = sliding_window_view(signal, frame_len)[::hop]
    rms = np.concatenate([
        np.sqrt(np.mean(np.square(frames[i : i + BLOCK_FRAMES], dtype=np.float64), axis=1))
        for i in range(0, frames.shape[0], BLOCK_FRAMES)
    ])
    level_db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    floor = max(SILENCE_FLOOR_DBFS, float(level_db.max()) - ACTIVE_RANGE_DB)
    active = level_db > floor
    active_s = float(active.sum()) * HOP_S
    if active_s < MIN_ACTIVE_S:
        return None

    active_index = np.flatnonzero(active)
    f0 = np.concatenate([
        _frame_pitch(frames[active_index[i : i + BLOCK_FRAMES]].astype(np.float64), ANALYSIS_SR)
        for i in range(0, active_index.size, BLOCK_FRAMES)
    ])
    voiced = f0[~np.isnan(f0)]
    pitch_st = spread_st = None
    if voiced.size >= MIN_VOICED_FRAMES:
        semitones = 12.0 * np.log2(voiced / 100.0)
        pitch_st = float(np.median(semitones))
        p10, p90 = np.percentile(semitones, [10, 90])
        spread_st = float(p90 - p10)

    loudness_db = float(10.0 * np.log10(np.mean(np.square(rms[active])) + 1e-20))
    envelope = np.convolve(level_db, np.ones(5) / 5.0, mode="same")
    envelope = np.where(active, envelope, floor)
    return ProsodyFeatures(
        loudness_db=loudness_db,
        rate_hz=_count_nuclei(envelope) / active_s,
        voiced_ratio=float(voiced.size) / float(active.sum()),
        active_s=active_s,
        pitch_st=pitch_st,
        pitch_spread_st=spread_st,
    )


def build_baseline(samples: Iterable[ProsodyFeatures], *, pitch_comparable: bool) -> Baseline:
    """Median and MAD-derived sigma per feature, floored so a monotone
    speaker's tiny spread cannot turn noise into a strong direction."""
    rows = list(samples)
    centre: dict[str, float] = {}
    spread: dict[str, float] = {}
    for name, floor in _FEATURE_FLOORS.items():
        values = np.array([v for v in (row.get(name) for row in rows) if v is not None])
        if values.size == 0:
            continue
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median))) * _MAD_TO_SIGMA
        centre[name] = median
        spread[name] = max(mad, floor)
    return Baseline(centre=centre, spread=spread, pitch_comparable=pitch_comparable)


def infer_direction(z: Mapping[str, float]) -> Direction:
    """Map speaker-relative z-scores onto the director taxonomy."""
    loud = z.get("loudness_db", 0.0)
    rate = z.get("rate_hz", 0.0)
    voicing = z.get("voiced_ratio", 0.0)
    pitch = z.get("pitch_st", 0.0)
    movement = z.get("pitch_spread_st", 0.0)
    tokens: dict[str, list[str]] = {}

    if voicing <= -2.0 and loud <= -1.0:
        # Unvoiced speech has no reliable syllable envelope, so pace is left
        # to the translation's own timing.
        tokens["intimacy"] = ["whispered"]
        return Direction(tokens=tokens, source="prosody-mirror", method="prosody")

    has_pitch = "pitch_st" in z
    arousal = (loud + pitch + movement) / 3.0 if has_pitch else loud
    # Pitch sharpens energy but never decides it alone: loudness must agree.
    if loud >= 1.0 and rate >= 1.0 and (pitch >= 0.5 or not has_pitch):
        tokens["energy"] = ["urgent"]
    elif arousal >= 1.0 and loud >= 0.5:
        tokens["energy"] = ["energetic"]
    elif arousal <= -1.0 and loud <= -0.5:
        tokens["energy"] = ["calm"]
    if loud >= 1.5 and rate <= -0.5:
        tokens["intimacy"] = ["announcing"]
    if rate >= 1.0:
        tokens["pace"] = ["quick"]
    elif rate <= -1.0:
        tokens["pace"] = ["slow"]
    return Direction(tokens=tokens, source="prosody-mirror", method="prosody")


def mirror(
    features: Mapping[str, Optional[ProsodyFeatures]],
    speakers: Mapping[str, str],
) -> dict[str, MirrorResult]:
    """Turn measured lines into directions against speaker baselines.

    ``features`` maps segment id to its measurement (None when unmeasurable);
    ``speakers`` maps segment id to speaker id, where an empty id is unknown.
    """
    measured = {sid: f for sid, f in features.items() if f is not None}
    results = {sid: MirrorResult(id=sid) for sid in features}
    if len(measured) < MIN_BASELINE_LINES:
        return results

    by_speaker: dict[str, list[ProsodyFeatures]] = defaultdict(list)
    for sid, feat in measured.items():
        by_speaker[speakers.get(sid, "")].append(feat)
    unknown = by_speaker.pop("", [])
    pooled = build_baseline(
        measured.values(), pitch_comparable=len(by_speaker) == 1 and not unknown
    )
    baselines = {
        speaker: build_baseline(rows, pitch_comparable=True)
        for speaker, rows in by_speaker.items()
        if len(rows) >= MIN_BASELINE_LINES
    }

    for sid, feat in measured.items():
        baseline = baselines.get(speakers.get(sid, ""), pooled)
        z = baseline.z(feat)
        direction = infer_direction(z)
        results[sid] = MirrorResult(
            id=sid,
            direction=direction.instruct_prompt(),
            tokens=direction.tokens,
            z=z,
            measured=True,
        )
    return results


def mirror_file(path: str, spans: Sequence[SegmentSpan]) -> list[MirrorResult]:
    """Read each span from ``path`` (seeking, never loading the whole track,
    at most ``MAX_LINE_S`` per span) and return one result per span in
    input order."""
    import soundfile as sf

    features: dict[str, Optional[ProsodyFeatures]] = {}
    speakers: dict[str, str] = {}
    with sf.SoundFile(path) as track:
        sr = track.samplerate
        total = track.frames
        track_s = total / sr
        requested_s = sum(
            max(0.0, min(span.end, span.start + MAX_LINE_S, track_s) - span.start)
            for span in spans
        )
        if requested_s > MAX_TRACK_PASSES * track_s + MAX_LINE_S:
            raise AnalysisBudgetExceeded(
                f"{requested_s:.0f} s requested from a {track_s:.0f} s track"
            )
        for span in spans:
            speakers[span.id] = span.speaker_id
            first = min(total, max(0, int(span.start * sr)))
            end = min(span.end, span.start + MAX_LINE_S)
            last = min(total, max(first, int(math.ceil(end * sr))))
            if last <= first:
                features[span.id] = None
                continue
            track.seek(first)
            chunk = track.read(last - first, dtype="float32", always_2d=True)
            features[span.id] = extract_features(chunk, sr)
    results = mirror(features, speakers)
    return [results[span.id] for span in spans]
