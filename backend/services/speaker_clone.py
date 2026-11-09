"""Speaker-clone extraction.

After diarisation assigns `speaker_id` to every segment, this module picks
the longest clean passage per speaker from the Demucs-isolated vocals track
and writes it as a per-speaker reference WAV. The reference, paired with the
corresponding transcript text, lets zero-shot TTS engines clone the
speaker's voice for dubbing — the central product promise of
"same speaker, new language."

Constraints we live with:
  * Zero-shot TTS wants 5–15 s of clean audio per reference. <5 s risks a
    thin clone; >15 s is wasted context.
  * The reference must be the actual speaker, not background music. Demucs
    handles that upstream — we read from `vocals.wav`, not the raw mix.
  * The accompanying transcript text must align with the audio slice or the
    TTS cloner will mis-align its phoneme lookups.

We don't promote these clones to the persistent voice library; they're
job-scoped (lives next to `seg_N.wav` under `dub_jobs/{id}/`). Users can
promote manually via "Save as Voice Profile" — out of scope here.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import soundfile as sf

logger = logging.getLogger("omnivoice.speaker_clone")

MIN_REF_DURATION_S = 5.0   # below this the clone is thin and unstable
MAX_REF_DURATION_S = 15.0  # above this is just wasted reference context
IDEAL_REF_DURATION_S = 8.0  # target window — long enough for prosody, short enough for coverage

# Per-segment clone refs (Wave 3.2): cutting a reference from a single
# subtitle line gives the dub of that line the prosody/emotion of its source
# line — but a single line is usually short. We use a lower floor than the
# per-speaker MIN (5.0): most dialogue lines are 2-6 s, and a 5 s floor would
# make per-segment refs almost never fire. Below this, the line falls back to
# the per-speaker reference (which always covers ≥ MIN_REF_DURATION_S). 3.0 s
# is the empirical floor below which our zero-shot clone gets unstable.
MIN_SEGMENT_REF_DURATION_S = 3.0

# Clone-purity guards (speaker-hint fix): a per-speaker reference cut from
# mislabeled or boundary-adjacent audio mixes two people's voices and the
# resulting clone sounds "made up".
#   * A slice below MIN_SLICE_DURATION_S is too short to be a reliable
#     single-speaker sample (and diarization boundary jitter dominates it).
#   * A slice whose edges come within ADJACENT_TURN_GUARD_S of a *different*
#     speaker's turn risks bleeding that speaker's audio across the imprecise
#     boundary — deprioritized (scoring preference, not a hard filter, so
#     extraction still succeeds on dense dialogue).
MIN_SLICE_DURATION_S = 1.5
ADJACENT_TURN_GUARD_S = 0.3

# Short-turn fallback (#3419): a speaker who only ever answers in short turns
# ("Good, and you?") has no slice of MIN_SLICE_DURATION_S, so the pass above
# gave them no clone and every one of their lines fell back to the default
# voice while the other speaker was cloned. When that pass finds nothing, the
# speaker's lines are trimmed to keep ADJACENT_TURN_GUARD_S away from every
# other speaker (the boundary-bleed risk the slice floor guards against) and
# pooled from slices of at least FALLBACK_MIN_SLICE_S. The pool must reach
# MIN_SEGMENT_REF_DURATION_S, the same floor a single-line reference needs.
FALLBACK_MIN_SLICE_S = 0.8


def extract_speaker_clones(
    vocals_path: str,
    segments: list[dict],
    out_dir: str,
    *,
    labels_source: str | None = None,
    strict: bool = False,
) -> dict[str, dict]:
    """Build a per-speaker reference sample from `vocals_path` + `segments`.

    Returns a dict keyed by `speaker_id`:
        {
          "Speaker 1": {
            "ref_audio": "/abs/path/voice_speaker_1.wav",
            "ref_text":  "…concatenated transcript of the chosen slices…",
            "duration":  7.83,
            "source_count": 2,
          },
          ...
        }

    Speakers without MIN_REF_DURATION_S of long slices fall back to their
    short turns, trimmed clear of other speakers (#3419). A speaker whose
    clean audio still totals < MIN_SEGMENT_REF_DURATION_S is skipped — we'd
    rather fall back to the default TTS voice than ship a bad clone.

    ``labels_source`` records where the ``speaker_id`` labels came from
    (``"pyannote"`` | ``"turns"`` | ``"heuristic"``; ``None`` = unknown,
    treated as trusted for backward compatibility). ``"heuristic"`` labels
    are silence-gap *estimates*, not voice identity — a reference cut from
    them routinely concatenates two people's audio, so extraction is skipped
    entirely (the caller warns the user and falls back to the default voice).
    """
    if labels_source == "heuristic":
        logger.info(
            "speaker_clone: skipping auto-clone extraction — speaker labels "
            "are gap-based heuristic estimates, not voice identity"
        )
        return {}
    if not vocals_path or not os.path.exists(vocals_path):
        if strict:
            raise FileNotFoundError("Source audio for speaker references is missing")
        logger.info("speaker_clone: no vocals track at %s; skipping", vocals_path)
        return {}
    if not segments:
        return {}

    try:
        audio, sr = sf.read(vocals_path, dtype="float32", always_2d=False)
    except Exception as e:
        logger.warning("speaker_clone: failed to read %s: %s", vocals_path, e)
        if strict:
            raise
        return {}
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # Group by speaker — preserve original segment order for text concat.
    by_speaker: dict[str, list[tuple[int, dict]]] = {}
    for idx, seg in enumerate(segments):
        spk = seg.get("speaker_id") or "Speaker 1"
        by_speaker.setdefault(spk, []).append((idx, seg))

    os.makedirs(out_dir, exist_ok=True)
    out: dict[str, dict] = {}

    for speaker_id, items in by_speaker.items():
        chosen = _pick_reference_slices(
            items,
            speaker_id=speaker_id,
            all_segments=segments,
            labels_source=labels_source,
        )
        if not chosen:
            logger.info(
                "speaker_clone: %s has <%ss of clean audio; will fall back to default voice",
                speaker_id, MIN_SEGMENT_REF_DURATION_S,
            )
            continue

        ref_audio_np = _concat_slices(audio, sr, chosen)
        if ref_audio_np.size == 0:
            continue

        safe_id = _safe_name(speaker_id)
        ref_path = os.path.join(out_dir, f"voice_{safe_id}.wav")
        try:
            sf.write(ref_path, ref_audio_np, sr)
        except Exception as e:
            logger.warning("speaker_clone: failed to write %s: %s", ref_path, e)
            if strict:
                raise
            continue

        if any(seg.get("text") is None for _, seg in chosen):
            # A trimmed slice without word timestamps: its transcript is
            # unknown, so leave it empty for the engine to transcribe (#3419).
            ref_text = ""
        else:
            ref_text = " ".join((seg.get("text") or "").strip() for _, seg in chosen).strip()
        out[speaker_id] = {
            "ref_audio": ref_path,
            "ref_text": ref_text,
            "duration": float(ref_audio_np.size) / float(sr),
            "source_count": len(chosen),
        }
        logger.info(
            "speaker_clone: wrote %s (%.2fs from %d slice%s)",
            ref_path, out[speaker_id]["duration"], len(chosen), "" if len(chosen) == 1 else "s",
        )

    return out


def extract_segment_refs(
    vocals_path: str,
    segments: list[dict],
    out_dir: str,
    *,
    seg_ids: list | None = None,
) -> dict[str, dict]:
    """Per-segment clone references (Wave 3.2 / Spec 4).

    Cut each segment's own slice from the isolated vocals at THAT segment's
    timestamps, so the dub of each line carries the prosody of its source
    line — finer-grained than one reference per speaker. Returns a dict keyed
    by segment id (``seg_ids[i]`` or ``"seg_{i}"``) for segments long enough
    to clone from:

        {"seg_3": {"ref_audio": "/abs/seg_ref_seg_3.wav",
                   "ref_text": "the source-language line",
                   "duration": 4.12}, ...}

    Segments shorter than ``MIN_SEGMENT_REF_DURATION_S``, and segments that
    overlap another speaker's segment, are omitted — the
    caller falls back to the per-speaker reference for those (a strict
    improvement over per-speaker-only, never a regression). Uses the
    *original* segment timestamps (pre slack-absorption); only the vocals are
    read, never the raw mix.
    """
    if not vocals_path or not os.path.exists(vocals_path) or not segments:
        return {}
    try:
        audio, sr = sf.read(vocals_path, dtype="float32", always_2d=False)
    except Exception as e:
        logger.warning("segment_refs: failed to read %s: %s", vocals_path, e)
        return {}
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    os.makedirs(out_dir, exist_ok=True)
    out: dict[str, dict] = {}
    for i, seg in enumerate(segments):
        seg_id = str(seg_ids[i]) if (seg_ids and i < len(seg_ids)) else f"seg_{i}"
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        if end - start < MIN_SEGMENT_REF_DURATION_S:
            continue
        if _overlaps_other_speaker(seg, segments):
            # Overlapping speech (#3419): the clip would carry both voices and
            # this line's dub would sound like a blend of them. The line uses
            # its speaker's pooled reference instead.
            continue
        s = max(0, int(start * sr))
        e = min(audio.size, int(end * sr))
        if e <= s:
            continue
        clip = audio[s:e].astype(np.float32, copy=False)
        ref_path = os.path.join(out_dir, f"seg_ref_{_safe_name(seg_id)}.wav")
        try:
            sf.write(ref_path, clip, sr)
        except Exception as e2:
            logger.warning("segment_refs: failed to write %s: %s", ref_path, e2)
            continue
        # The vocals slice is source-language audio, so the matching
        # reference transcript is the SOURCE text (text_original), not the
        # translated `text`. Falls back to text only if no original is kept.
        ref_text = (seg.get("text_original") or seg.get("text") or "").strip()
        out[seg_id] = {
            "ref_audio": ref_path,
            "ref_text": ref_text,
            "duration": float(clip.size) / float(sr),
        }
    if out:
        logger.info("segment_refs: wrote %d per-segment reference(s)", len(out))
    return out


def refine_ref_text(ref_audio_path: str, asr_backend, fallback_text: str) -> str:
    """Re-transcribe a written reference clip and return that transcript.

    `extract_speaker_clones`/`extract_segment_refs` pair each audio slice with
    the ASR segment's OWN text field, on the assumption that the segment's
    timestamps and its transcribed text agree. They routinely don't — Whisper
    (and friends) frequently drift on segment boundaries: a trailing word
    audible in `[start, end]` but missing from `text`, or vice versa. When the
    (ref_audio, ref_text) pair disagrees, zero-shot TTS prompt-priming breaks
    down and the clone can speak the mismatched reference text itself instead
    of the target-language text it was given to synthesize (issue #1004).

    Re-transcribing the *actual written clip* guarantees the pair matches by
    construction — the model doesn't care whether the original ASR text was
    right, only that ref_text is what's really in ref_audio. `asr_backend` is
    the caller's already-loaded active backend (duck-typed:
    `.transcribe(path, word_timestamps=...) -> dict` with a `chunks` list of
    `{"text": ...}`); the model is already warm, so this costs one more short
    transcribe call, not a fresh load. Falls back to `fallback_text` — never
    raises — so a re-transcribe failure is a strict no-op, never a regression
    from the original (matching) behavior.
    """
    if asr_backend is None:
        return fallback_text
    try:
        result = asr_backend.transcribe(ref_audio_path, word_timestamps=False)
        text = " ".join(
            (c.get("text") or "").strip() for c in (result.get("chunks") or [])
        ).strip()
        return text or fallback_text
    except Exception as e:
        logger.warning(
            "speaker_clone: re-transcribe of %s failed, keeping original ref_text: %s",
            ref_audio_path, e,
        )
        return fallback_text


def refine_ref_texts(clones: dict[str, dict], asr_backend) -> dict[str, dict]:
    """Apply `refine_ref_text` to every entry's `ref_text` in place.

    Batches the whole dict (per-speaker `clones` from `extract_speaker_clones`
    or per-segment `seg_clones` from `extract_segment_refs`) into the single
    executor round-trip the caller submits to the GPU pool, rather than one
    dispatch per reference. Mutates and returns `clones` for a convenient
    call-and-reassign at the call site.
    """
    for entry in clones.values():
        entry["ref_text"] = refine_ref_text(
            entry["ref_audio"], asr_backend, entry.get("ref_text", "")
        )
    return clones


# ── Internals ───────────────────────────────────────────────────────────────


def _adjacent_to_other_speaker(
    seg: dict, speaker_id: str, all_segments: list[dict] | None
) -> bool:
    """True when `seg`'s edges come within ADJACENT_TURN_GUARD_S of (or
    overlap) a segment attributed to a *different* speaker — a boundary where
    imprecise diarization timestamps risk bleeding the other voice into the
    reference slice."""
    if not all_segments:
        return False
    s0 = float(seg.get("start", 0.0))
    s1 = float(seg.get("end", 0.0))
    for other in all_segments:
        if other is seg:
            continue
        if (other.get("speaker_id") or "Speaker 1") == speaker_id:
            continue
        o0 = float(other.get("start", 0.0))
        o1 = float(other.get("end", 0.0))
        # Signed gap between the two spans; negative = overlap.
        if max(o0 - s1, s0 - o1) < ADJACENT_TURN_GUARD_S:
            return True
    return False


def _overlaps_other_speaker(seg: dict, all_segments: list[dict]) -> bool:
    """True when a segment of a different speaker overlaps ``seg`` in time."""
    speaker_id = seg.get("speaker_id") or "Speaker 1"
    s0 = float(seg.get("start", 0.0))
    s1 = float(seg.get("end", 0.0))
    for other in all_segments:
        if other is seg or (other.get("speaker_id") or "Speaker 1") == speaker_id:
            continue
        if min(s1, float(other.get("end", 0.0))) - max(s0, float(other.get("start", 0.0))) > 1e-3:
            return True
    return False


def _pick_reference_slices(
    items: list[tuple[int, dict]],
    *,
    speaker_id: str | None = None,
    all_segments: list[dict] | None = None,
    labels_source: str | None = None,
) -> list[tuple[int, dict]]:
    """Select the subset of a speaker's segments to use as reference audio.

    Strategy: rank candidates clean-first (not temporally adjacent to a
    different speaker's turn — see ``_adjacent_to_other_speaker``), longest
    first within each tier, and accumulate until IDEAL_REF_DURATION_S is
    cleared. Adjacency is a scoring preference, NOT a hard filter — on dense
    dialogue where every slice borders another speaker, extraction still
    succeeds using the adjacent ones. Two hard guards protect clone purity:

    * slices shorter than MIN_SLICE_DURATION_S are rejected outright
      (boundary jitter dominates them, so they're the likeliest to carry a
      second speaker's audio);
    * ``labels_source="heuristic"`` returns [] — gap-based labels are not
      voice identity, so no slice of them is safe to clone from.

    Cap at MAX_REF_DURATION_S. When this can't reach MIN_REF_DURATION_S,
    fall back to ``_pick_short_turn_slices`` (#3419). When ``all_segments``/``speaker_id`` are not
    provided (legacy callers), adjacency scoring degrades to duration-only —
    the pre-guard behavior.
    """
    if not items:
        return []
    if labels_source == "heuristic":
        return []
    if speaker_id is None:
        speaker_id = items[0][1].get("speaker_id") or "Speaker 1"

    def _dur(pair) -> float:
        return max(0.0, float(pair[1].get("end", 0.0)) - float(pair[1].get("start", 0.0)))

    # Rank: clean (non-adjacent) before adjacent, longest first within each
    # tier. Keep original indices so we can restore transcript order below.
    ranked = sorted(
        items,
        key=lambda pair: (
            _adjacent_to_other_speaker(pair[1], speaker_id, all_segments),
            -_dur(pair),
        ),
    )

    picked: list[tuple[int, dict]] = []
    total = 0.0
    for idx, seg in ranked:
        dur = _dur((idx, seg))
        if dur < MIN_SLICE_DURATION_S:
            continue
        if total + dur > MAX_REF_DURATION_S and picked:
            # Ranking is no longer duration-monotonic, so a later (shorter or
            # adjacent) slice may still fit — skip, don't stop.
            continue
        picked.append((idx, seg))
        total += dur
        if total >= IDEAL_REF_DURATION_S:
            break

    if total < MIN_REF_DURATION_S:
        return _pick_short_turn_slices(
            items, speaker_id=speaker_id, all_segments=all_segments,
        )

    # Restore original order so concatenated transcript reads left-to-right.
    picked.sort(key=lambda pair: pair[0])
    return picked


def _guarded_span(
    seg: dict, speaker_id: str, all_segments: list[dict] | None
) -> tuple[float, float] | None:
    """``seg``'s span trimmed to stay ADJACENT_TURN_GUARD_S clear of every
    segment of a different speaker; None when nothing usable remains."""
    s0 = float(seg.get("start", 0.0))
    s1 = float(seg.get("end", 0.0))
    start, end = s0, s1
    for other in all_segments or []:
        if other is seg or (other.get("speaker_id") or "Speaker 1") == speaker_id:
            continue
        o0 = float(other.get("start", 0.0))
        o1 = float(other.get("end", 0.0))
        if o1 <= s0 - ADJACENT_TURN_GUARD_S or o0 >= s1 + ADJACENT_TURN_GUARD_S:
            continue
        if o0 <= s0:
            start = max(start, o1 + ADJACENT_TURN_GUARD_S)
        elif o1 >= s1:
            end = min(end, o0 - ADJACENT_TURN_GUARD_S)
        else:
            return None  # another speaker talks inside this line
    return (start, end) if end > start else None


def _text_within(seg: dict, span: tuple[float, float]) -> str | None:
    """The words of ``seg`` whose timestamps lie inside the kept ``span``.

    A trimmed slice must not carry words whose audio was cut: a reference
    transcript naming unheard words makes the clone speak them. Returns None
    when the segment has no word timestamps, so the caller leaves ref_text
    empty and the engine transcribes the clip itself.
    """
    words = seg.get("words") or []
    timed = [w for w in words if isinstance(w, dict)
             and isinstance(w.get("start"), (int, float))
             and isinstance(w.get("end"), (int, float))]
    if not timed or len(timed) != len(words):
        return None
    eps = 1e-6
    kept = [str(w.get("text", w.get("word", ""))).strip() for w in timed
            if w["start"] >= span[0] - eps and w["end"] <= span[1] + eps]
    return " ".join(t for t in kept if t)


def _pick_short_turn_slices(
    items: list[tuple[int, dict]],
    *,
    speaker_id: str,
    all_segments: list[dict] | None,
) -> list[tuple[int, dict]]:
    """Fallback for a speaker with no slice long enough for the main pass.

    Returns trimmed copies of the speaker's segments (see ``_guarded_span``),
    longest first up to IDEAL_REF_DURATION_S, or [] when they cannot reach
    MIN_SEGMENT_REF_DURATION_S.
    """
    candidates = []
    for idx, seg in items:
        span = _guarded_span(seg, speaker_id, all_segments)
        if span and span[1] - span[0] >= FALLBACK_MIN_SLICE_S:
            trimmed = {**seg, "start": span[0], "end": span[1]}
            if span != (float(seg.get("start", 0.0)), float(seg.get("end", 0.0))):
                trimmed["text"] = _text_within(seg, span)
                if trimmed["text"] == "":
                    continue  # no whole word survives the trim
            candidates.append((idx, trimmed))
    candidates.sort(key=lambda pair: -(pair[1]["end"] - pair[1]["start"]))
    picked: list[tuple[int, dict]] = []
    total = 0.0
    for idx, seg in candidates:
        dur = seg["end"] - seg["start"]
        if total + dur > MAX_REF_DURATION_S and picked:
            continue
        picked.append((idx, seg))
        total += dur
        if total >= IDEAL_REF_DURATION_S:
            break
    if total < MIN_SEGMENT_REF_DURATION_S:
        return []
    picked.sort(key=lambda pair: pair[0])
    return picked


def _concat_slices(audio: np.ndarray, sr: int, picked: list[tuple[int, dict]]) -> np.ndarray:
    """Concatenate the picked segment audio slices into one reference array."""
    parts: list[np.ndarray] = []
    for _, seg in picked:
        start = int(float(seg.get("start", 0.0)) * sr)
        end = int(float(seg.get("end", 0.0)) * sr)
        if start < 0:
            start = 0
        if end > audio.size:
            end = audio.size
        if end <= start:
            continue
        parts.append(audio[start:end])
    if not parts:
        return np.zeros(0, dtype=np.float32)
    # A 20 ms silence pad between slices keeps the TTS reference clean and
    # gives the phoneme aligner something to anchor on at the boundary.
    gap = np.zeros(int(0.02 * sr), dtype=np.float32)
    out: list[np.ndarray] = []
    for i, part in enumerate(parts):
        if i > 0:
            out.append(gap)
        out.append(part.astype(np.float32, copy=False))
    return np.concatenate(out)


def _safe_name(speaker_id: str) -> str:
    """`Speaker 1` → `speaker_1`. Keeps filenames portable across OSes."""
    cleaned = []
    for ch in speaker_id.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in (" ", "-"):
            cleaned.append("_")
    return "".join(cleaned) or "speaker"


def auto_profile_id(speaker_id: str) -> str:
    """Stable profile id prefix so `_gen` can tell auto-clones apart from
    persistent voice-profile ids."""
    return f"auto:{_safe_name(speaker_id)}"


def build_cast_sources(
    segments: list[dict],
    speaker_clones: dict[str, dict] | None,
    segment_clones: dict[str, dict] | None,
) -> dict[str, dict]:
    """Return path-free metadata for every usable ``From video`` voice.

    A trusted diarizer can produce a pooled per-speaker clone.  When it
    cannot, the pipeline still extracts clean per-segment references; those
    references are valid voice prompts even though they are not reliable
    evidence for grouping identities.  The cast UI needs to know that a
    speaker label has at least one usable source without receiving host paths
    or transcript text.
    """
    sources: dict[str, dict] = {}
    for speaker_id, info in (speaker_clones or {}).items():
        sources[speaker_id] = {
            "duration": float(info.get("duration") or 0.0),
            "source_count": int(info.get("source_count") or 1),
            "kind": "speaker",
        }

    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        speaker_id = segment.get("speaker_id") or "Speaker 1"
        current = sources.get(speaker_id)
        if current and current.get("kind") == "speaker":
            continue
        info = (segment_clones or {}).get(str(segment.get("id", "")))
        if not info or not info.get("ref_audio"):
            continue
        duration = float(info.get("duration") or 0.0)
        if current is None or duration > current["duration"]:
            sources[speaker_id] = {
                "duration": duration,
                "source_count": 1,
                "kind": "segment",
            }
    return sources
