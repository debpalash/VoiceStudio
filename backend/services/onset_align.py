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

VAD gate (#3419): energy alone still let a *sustained* non-speech sound —
a sigh, a breath, rustling — set a line's start, and Whisper's end-stretch
over trailing breaths and silence made slots longer than the line. Speech is
voiced (periodic vocal-fold vibration, 70–400 Hz); breaths, sighs, footsteps
and rustles are not. So when a window contains voiced speech, the start snaps
to the first *sustained voiced* onset (keeping a short unvoiced lead for
consonants such as "s" or "h"), long snaps are allowed over audible content
that holds no voiced speech, and ``trim_segment_ends`` pulls the end back to
the last voiced speech plus a short tail. A window with no voiced frames at
all (whispered lines) keeps the energy-only behaviour.

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


# ── Voicing (#3419) ────────────────────────────────────────────────────────
# Pitch search range and the normalised-autocorrelation peak a frame needs to
# count as voiced: the same method and threshold Prosody Mirror measures
# deliveries with. Analysis windows are two frames (40 ms) long so the lowest
# pitch still has a couple of periods in view.
F0_MIN_HZ = 70.0
F0_MAX_HZ = 400.0
VOICING_THRESHOLD = 0.45
# Voiced frames an onset needs within its sustain window to read as speech.
VOICED_MIN_S = 0.06
# Unvoiced lead kept before the first voiced frame (initial consonants).
CONSONANT_LEAD_S = 0.15
# Audio kept after the last voiced frame (final consonants, decay).
TAIL_PAD_S = 0.15
# Frames per voicing block, bounding memory on long windows.
_VOICING_BLOCK = 512


def _voiced_frames(x: np.ndarray, sr: int, frame_len: int, n_frames: int) -> np.ndarray:
    """Per-frame voicing for the ``n_frames`` ``frame_len``-sample frames of ``x``.

    Normalised autocorrelation of a Hann-windowed 2-frame window, divided by
    the window's own autocorrelation so low voices are not penalised. Noise,
    breaths and transients have no strong periodic peak in the pitch range.
    """
    if n_frames <= 0:
        return np.zeros(0, dtype=bool)
    win = 2 * frame_len
    lo = max(1, int(sr / F0_MAX_HZ))
    hi = min(win - 2, int(sr / F0_MIN_HZ))
    if hi <= lo:
        return np.zeros(n_frames, dtype=bool)
    need = n_frames * frame_len + frame_len
    data = np.asarray(x[:need], dtype=np.float64)
    if data.size < need:
        data = np.pad(data, (0, need - data.size))
    # Pre-emphasis: low rumble (thuds, handling noise) is strongly correlated
    # at short lags and would otherwise read as voiced; speech harmonics keep
    # their periodicity.
    data = np.concatenate(([data[0]], data[1:] - 0.97 * data[:-1]))
    hann = np.hanning(win)
    nfft = 1 << (win + hi + 1).bit_length()
    w_acf = np.fft.irfft(np.abs(np.fft.rfft(hann, nfft)) ** 2, nfft)[: hi + 1]
    w_norm = w_acf / w_acf[0]
    out = np.zeros(n_frames, dtype=bool)
    for b0 in range(0, n_frames, _VOICING_BLOCK):
        b1 = min(n_frames, b0 + _VOICING_BLOCK)
        frames = np.lib.stride_tricks.sliding_window_view(
            data[b0 * frame_len: b1 * frame_len + frame_len], win
        )[::frame_len][: b1 - b0]
        centred = (frames - frames.mean(axis=1, keepdims=True)) * hann
        acf = np.fft.irfft(np.abs(np.fft.rfft(centred, nfft, axis=1)) ** 2, nfft, axis=1)[:, : hi + 1]
        energy = acf[:, :1]
        acf = np.divide(acf, energy, out=np.zeros_like(acf), where=energy > 0)
        corr = acf[:, lo:] / w_norm[lo:]
        out[b0:b1] = corr.max(axis=1) >= VOICING_THRESHOLD
    return out


def _window_frames(audio: np.ndarray, sr: int, start_s: float, end_s: float):
    """(rms, voiced, above, frame_s) for ``[start_s, end_s]``, or None."""
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
        return None
    above = rms >= max(RELATIVE_THRESHOLD * peak, ABS_RMS_FLOOR)
    voiced = _voiced_frames(window, sr, frame_len, rms.size)
    return rms, voiced, above, frame_len / sr


def _region_has_voiced_speech(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> bool:
    """True when ``[start_s, end_s]`` holds audible voiced frames worth a syllable."""
    frames = _window_frames(audio, sr, start_s, end_s)
    if frames is None:
        return False
    rms, voiced, _above, frame_s = frames
    need = max(1, int(round(VOICED_MIN_S / frame_s)))
    return int(np.count_nonzero(voiced & (rms >= ABS_RMS_FLOOR))) >= need


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
    # #3419 VAD gate: with voiced speech in the window, a sustained but
    # unvoiced sound (sigh, breath, rustle) is not where the line starts.
    voiced = _voiced_frames(window, sr, frame_len, rms.size) & above
    if voiced.any():
        vcum = np.concatenate(([0], np.cumsum(voiced)))
        vc = np.nonzero(voiced)[0]
        ends = np.minimum(vc + win_frames, above.size)
        voiced_need = max(1, int(round(VOICED_MIN_S / frame_s)))
        ok = vc[((cum[ends] - cum[vc]) >= need_frames) & ((vcum[ends] - vcum[vc]) >= voiced_need)]
        if ok.size:
            first = int(ok[0])
            lead = max(0, int(round(CONSONANT_LEAD_S / frame_s)))
            k = first
            while k > 0 and first - k < lead and above[k - 1]:
                k -= 1
            return start_s + float(k) * frame_s
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


def _skipped_span_is_non_speech(
    audio: np.ndarray, sr: int, start_s: float, end_s: float, onset_s: float,
) -> bool:
    """A long snap may cross audible sound only when it holds no speech (#3419).

    Requires voiced speech after ``onset_s`` (so the window really has a voiced
    reference) and none in the span being skipped: a minute of rustling or a
    long sigh before the first word is skipped, quiet voiced speech is not.
    """
    if not _region_has_voiced_speech(audio, sr, onset_s, end_s):
        return False
    return not _region_has_voiced_speech(audio, sr, start_s, onset_s)


def trim_segment_ends(
    segments: Sequence[dict],
    audio: np.ndarray,
    sr: int,
    *,
    min_shift_s: float = MIN_SHIFT_S,
    separated_vocals: bool = True,
) -> int:
    """Pull each segment's ``end`` back to its last voiced speech (#3419).

    Whisper stretches ends over trailing breaths, sighs and silence, which
    makes a line's slot longer than the line. Backward-only and conservative:
    segments without voiced speech (whispered lines) are left alone, an
    unvoiced final consonant right after the last voiced frame is kept, a
    ``TAIL_PAD_S`` tail is kept, and a minimum duration is preserved. Like
    :func:`snap_segment_starts`, only on a separated vocals track.
    """
    if not separated_vocals or sr <= 0 or audio is None or len(audio) == 0:
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
            continue
        frames = _window_frames(audio, sr, start, end)
        if frames is None:
            continue
        _rms, voiced, above, frame_s = frames
        speech = voiced & above
        need = max(1, int(round(VOICED_MIN_S / frame_s)))
        if int(np.count_nonzero(speech)) < need:
            continue  # no voiced speech: nothing to anchor the end on
        last = int(np.nonzero(speech)[0][-1])
        lead = max(0, int(round(CONSONANT_LEAD_S / frame_s)))
        k = last
        while k + 1 < above.size and k - last < lead and above[k + 1]:
            k += 1
        new_end = start + (k + 1) * frame_s + TAIL_PAD_S
        new_end = max(new_end, start + MIN_SEG_DUR_S)
        if end - new_end < min_shift_s:
            continue
        seg["end"] = round(new_end, 3)
        adjusted += 1
    if adjusted:
        logger.info("onset-align: trimmed %d/%d segment end(s) to the last speech",
                    adjusted, len(segments))
    return adjusted


def snap_segments_to_speech(
    segments: Sequence[dict],
    audio: np.ndarray,
    sr: int,
    *,
    separated_vocals: bool = True,
) -> int:
    """Start lines on detected speech and end them on it (#280, #963, #3419)."""
    moved = snap_segment_starts(segments, audio, sr, separated_vocals=separated_vocals)
    return moved + trim_segment_ends(segments, audio, sr, separated_vocals=separated_vocals)


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
        if (
            shift > MAX_SNAP_S
            and not _region_mostly_silent(audio, sr, start, onset)
            and not _skipped_span_is_non_speech(audio, sr, start, end, onset)
        ):
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
