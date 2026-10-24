"""
Speech-onset alignment for transcript segments (issue #280, item 1).

Whisper-family ASR models are prone to stretching a segment's *start* back
over leading non-speech (intro music, room tone, silence). The classic
symptom from the issue report: the speaker starts talking at 0:02–0:03,
but the first transcript segment says ``start=0.0`` — so the dubbed line
plays the moment the video begins and everything feels desynchronised.

``snap_segment_starts`` post-processes segments against the actual audio
(ideally the Demucs-isolated vocals track, which the dub pipeline already
produces): for each segment it scans the waveform inside ``[start, end]``
for the first *sustained* rise of frame RMS above an adaptive threshold
and moves ``start`` forward to just before that onset.

Design constraints:

* **Forward-only.** A segment start is never moved earlier — that could
  collide with the previous speaker. We only trim leading non-speech.
* **Conservative.** Shifts below ``min_shift_s`` are ignored (word-level
  timestamps are usually within ~100 ms already); a minimum segment
  duration is always preserved; segments whose window looks silent
  (no frame above the absolute floor) are left untouched.
* **Pure NumPy.** No model, no platform-specific code — identical
  behaviour on macOS / Windows / Linux, trivially unit-testable.

Robustness against non-speech onsets (#963): a field report showed dubbed
lines starting seconds off because "when a noise is heard (a sigh or
footsteps), it's interpreted as the start of the conversation". Three
guards address that class of failure:

* **Sustained energy.** A frame only counts as an onset when the energy
  stays up for a speech-like duration (``SUSTAIN_MIN_S`` within the
  following ``SUSTAIN_WINDOW_S``). Footsteps/door thuds/clicks light up
  one or two 20 ms frames and die; syllables keep the energy up.
* **Bounded snap distance.** Shifts beyond ``MAX_SNAP_S`` are only
  trusted when everything being skipped is (near-)silence — the genuine
  #280 whisper start-stretch, where Demucs removed the leading music and
  left real silence on the vocals track. Jumping far over *audible*
  content (e.g. quiet speech sitting under the relative threshold) would
  play the dub seconds late, so it is refused.
* **Source-aware.** Snapping only runs on a separated vocals track
  (``separated_vocals=True``). On mixed/original audio — Demucs skipped
  or failed — music, ambience and room tone are all legitimate sustained
  energy, so any detected "onset" is as likely the score as the speaker;
  whisper's own timestamps beat a confidently wrong snap.
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

logger = logging.getLogger("omnivoice.onset_align")

# Analysis frame for RMS energy. 20 ms is fine-grained enough to localise
# a syllable onset while staying cheap (a 10-min track is ~30k frames).
FRAME_S = 0.02
# Keep this much audio before the detected onset so plosives/breaths that
# sit just under the threshold aren't clipped off.
PRE_ROLL_S = 0.05
# Shifts smaller than this are noise — word-level ASR timestamps are
# usually accurate to ~0.1 s, so don't churn segment data for less.
MIN_SHIFT_S = 0.15
# Never shrink a segment below this duration when shifting its start.
MIN_SEG_DUR_S = 0.30
# A frame must exceed `RELATIVE_THRESHOLD × peak RMS of the window` to
# count as speech onset…
RELATIVE_THRESHOLD = 0.10
# …and the window's peak RMS must exceed this absolute floor, otherwise
# the whole window is treated as silence and left alone (we'd only be
# snapping to noise).
ABS_RMS_FLOOR = 1e-3
# An onset must be *sustained* to count as speech (#963): within the
# SUSTAIN_WINDOW_S that follows a candidate frame, at least SUSTAIN_MIN_S
# worth of frames must also sit above the threshold. A ~100 ms footstep
# burst fails this; real speech (syllables every few hundred ms) passes.
SUSTAIN_WINDOW_S = 0.30
SUSTAIN_MIN_S = 0.16
# Snaps larger than this are only trusted when the skipped span is
# (near-)silence — see _region_mostly_silent (#963).
MAX_SNAP_S = 1.5
# The skipped span counts as "mostly silent" when at most this fraction of
# its frames is audible. Non-zero so an isolated transient bleeding through
# separation (a footstep) doesn't block a genuine long silence-trim…
SKIPPED_AUDIBLE_FRAC = 0.10
# …where "audible" = above max(ABS_RMS_FLOOR, this fraction of the span's
# own peak); the relative term keeps a slightly raised residual noise floor
# from reading as content.
SKIPPED_FLOOR_PEAK_FRAC = 0.02


def _frame_rms(x: np.ndarray, frame_len: int) -> np.ndarray:
    """RMS per non-overlapping frame; the ragged tail frame is dropped."""
    n = (len(x) // frame_len) * frame_len
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    frames = x[:n].reshape(-1, frame_len).astype(np.float64, copy=False)
    return np.sqrt((frames * frames).mean(axis=1)).astype(np.float32)


def detect_speech_onset(
    audio: np.ndarray,
    sr: int,
    start_s: float,
    end_s: float,
) -> float | None:
    """Return the absolute time (s) of the first speech-like frame inside
    ``[start_s, end_s]``, or ``None`` when the window is empty / silent.

    "Speech-like" requires *sustained* energy (#963): within the
    ``SUSTAIN_WINDOW_S`` look-ahead after a candidate frame, at least
    ``SUSTAIN_MIN_S`` worth of frames must also exceed the threshold.
    Short broadband transients — footsteps, door thuds, mouse clicks —
    light up one or two 20 ms frames and then die, so they no longer read
    as "the conversation started here"; real speech keeps the energy up
    across syllables. A candidate too close to the window's end to prove
    sustain is rejected (conservative: the ASR timestamp stands).
    """
    if sr <= 0 or end_s <= start_s:
        return None
    i0 = max(0, int(start_s * sr))
    i1 = min(len(audio), int(end_s * sr))
    if i1 <= i0:
        return None
    window = audio[i0:i1]
    frame_len = max(1, int(FRAME_S * sr))
    rms = _frame_rms(window, frame_len)
    if rms.size == 0:
        return None
    peak = float(rms.max())
    if peak < ABS_RMS_FLOOR:
        return None  # whole window is effectively silent
    threshold = max(RELATIVE_THRESHOLD * peak, ABS_RMS_FLOOR)
    above = rms >= threshold
    candidates = np.nonzero(above)[0]
    if candidates.size == 0:
        return None
    frame_s = frame_len / sr
    win_frames = max(1, int(round(SUSTAIN_WINDOW_S / frame_s)))
    need_frames = max(1, int(round(SUSTAIN_MIN_S / frame_s)))
    # counts[k] = above-threshold frames within rms[c : c + win_frames]
    # for candidate c — O(n) via a cumulative sum, no per-candidate scan.
    cum = np.concatenate(([0], np.cumsum(above)))
    counts = cum[np.minimum(candidates + win_frames, above.size)] - cum[candidates]
    sustained = candidates[counts >= need_frames]
    if sustained.size == 0:
        return None  # only transient bursts in this window
    return start_s + float(sustained[0]) * frame_s


# Hysteresis for full-track onset listing: after a frame crosses the
# threshold, the energy must stay *below* it for at least this long before
# the next rise counts as a new onset. Stops syllable-internal dips from
# spamming the timeline with ticks.
MIN_ONSET_GAP_S = 0.15


def detect_speech_onsets(audio: np.ndarray, sr: int) -> list[float]:
    """Return the times (s) of every speech-like onset across the whole track.

    Powers the timeline editor's snap-to-onset ticks (issue #280, item 3):
    frame RMS over the full track, single adaptive threshold
    ``max(RELATIVE_THRESHOLD × peak, ABS_RMS_FLOOR)``, and hysteresis — a
    new onset registers only when the energy rises above the threshold
    after at least ``MIN_ONSET_GAP_S`` below it.

    Pure NumPy, identical behaviour on every platform. Returns ``[]`` for
    empty/silent audio.
    """
    if sr <= 0 or audio is None or len(audio) == 0:
        return []
    if audio.ndim > 1:
        # Energy analysis must not cancel opposite-polarity stereo channels.
        audio = np.max(np.abs(audio), axis=1)
    frame_len = max(1, int(FRAME_S * sr))
    rms = _frame_rms(audio, frame_len)
    if rms.size == 0:
        return []
    peak = float(rms.max())
    if peak < ABS_RMS_FLOOR:
        return []  # whole track is effectively silent
    threshold = max(RELATIVE_THRESHOLD * peak, ABS_RMS_FLOOR)
    gap_frames = max(1, int(round(MIN_ONSET_GAP_S / FRAME_S)))
    frame_s = frame_len / sr

    onsets: list[float] = []
    below_run = gap_frames  # armed, so speech at t=0 still counts
    for i, v in enumerate(rms):
        if v >= threshold:
            if below_run >= gap_frames:
                onsets.append(round(i * frame_s, 3))
            below_run = 0
        else:
            below_run += 1
    return onsets


def _region_mostly_silent(
    audio: np.ndarray,
    sr: int,
    start_s: float,
    end_s: float,
) -> bool:
    """True when ``[start_s, end_s]`` contains (almost) no audible content.

    Gates long snaps (> ``MAX_SNAP_S``, #963): jumping far forward is only
    trustworthy when everything being skipped is silence — the genuine
    whisper start-stretch of #280, where Demucs stripped the leading music
    and left real silence on the vocals track. A small fraction of audible
    frames is tolerated so an isolated transient bleeding through
    separation (a footstep) doesn't block the trim; *sustained* audible
    content — e.g. quiet speech sitting below the relative onset
    threshold — does block it, because skipping past it would desync the
    dub by the full jump.
    """
    i0 = max(0, int(start_s * sr))
    i1 = min(len(audio), int(end_s * sr))
    if i1 <= i0:
        return True
    rms = _frame_rms(audio[i0:i1], max(1, int(FRAME_S * sr)))
    if rms.size == 0:
        return True
    floor = max(ABS_RMS_FLOOR, SKIPPED_FLOOR_PEAK_FRAC * float(rms.max()))
    return float((rms >= floor).mean()) <= SKIPPED_AUDIBLE_FRAC


def snap_segment_starts(
    segments: Sequence[dict],
    audio: np.ndarray,
    sr: int,
    *,
    min_shift_s: float = MIN_SHIFT_S,
    separated_vocals: bool = True,
) -> int:
    """Snap each segment's ``start`` forward to the actual speech onset.

    Mutates the segment dicts in place (the shape the dub pipeline passes
    around). Returns the number of segments adjusted.

    ``audio`` should be the mono-float **separated vocals** track. When the
    caller only has mixed/original audio (Demucs skipped or failed), pass
    ``separated_vocals=False``: snapping is then disabled entirely (#963) —
    on a mixed track music, ambience and footsteps are all sustained energy,
    so a detected "onset" is as likely the score as the speaker, and
    whisper's own timestamps beat a confidently wrong snap.
    """
    if not separated_vocals:
        logger.info(
            "onset-align: skipped — audio is not a separated vocals track "
            "(Demucs unavailable/failed); keeping ASR timestamps as-is")
        return 0
    if sr <= 0 or audio is None or len(audio) == 0:
        return 0
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    adjusted = 0
    for seg in segments:
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if end - start < MIN_SEG_DUR_S + min_shift_s:
            continue  # too short for a meaningful shift
        onset = detect_speech_onset(audio, sr, start, end)
        if onset is None:
            continue
        new_start = max(start, onset - PRE_ROLL_S)
        shift = new_start - start
        if shift < min_shift_s:
            continue
        if shift > MAX_SNAP_S and not _region_mostly_silent(audio, sr, start, onset):
            # Long jump over audible content (#963): the "onset" is more
            # likely a louder late event than the true start — quiet speech
            # under the relative threshold would be skipped wholesale and
            # the dub would play seconds LATE. Bounded corrections are fine;
            # unbounded ones only over true silence (the #280 case).
            continue
        # Preserve a minimum playable duration.
        new_start = min(new_start, end - MIN_SEG_DUR_S)
        if new_start - start < min_shift_s:
            continue
        seg["start"] = round(new_start, 3)
        adjusted += 1

    if adjusted:
        logger.info("onset-align: snapped %d/%d segment start(s) to speech onset",
                    adjusted, len(segments))
    return adjusted
