"""Broadcast-grade segmentation for dubbing.

Rules (in priority order):
  1. Never split mid-word. Whitespace or nothing.
  2. Prefer sentence punctuation > clause punctuation (, ; : —) > word boundaries.
  3. Reject any candidate split that leaves either side below the minimum floor.
  4. Fragments below the floor merge into same-speaker neighbor; gap < MERGE_GAP
     prefers previous, else next.
  5. Scene-cut assisted splits apply only when both halves remain viable.
  6. Never merge across a speaker boundary.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence


MIN_DUR = 1.5         # seconds — below this, a segment must merge
MIN_CHARS = 12        # characters — below this, a segment must merge (Latin-ish)
MIN_WORDS = 3         # words — below this, a segment is considered a fragment
STITCH_DUR = 2.5      # seconds — pair of short neighbors under this combine even when each is legal
STITCH_GAP = 0.9      # seconds — max silence between two stitch candidates
IDEAL_DUR = 4.5       # seconds — target length for splits
MAX_DUR = 9.0         # seconds — above this, force a split
MAX_CHARS = 140       # characters — above this, force a split
MERGE_GAP = 0.6       # seconds — tolerated silence when folding a fragment backward
MERGE_GAP_ULTRA = 2.0 # seconds — wider gap tolerated for ultra-short (< 0.5s or < 3 chars)
ULTRA_SHORT_DUR = 0.5 # seconds — threshold for "always fold" regardless of neighbor match
ULTRA_SHORT_CHARS = 4 # chars — same tier
SPEAKER_GAP = 1.2     # seconds — heuristic speaker-change gap (no pyannote)

# Sentence-end punctuation across Latin, CJK, Bengali, Arabic, Thai, Armenian, Hindi, etc.
_SENTENCE_END = re.compile(
    r'([.!?。！？।؟…؛܀։՝።။၊।]["\')\]]?)(\s+|$)'
)
_CLAUSE_END = re.compile(r'([,;:—、،؍])(\s+|$)')
_WS = re.compile(r'\s+')


def _word_count(text: str) -> int:
    if not text:
        return 0
    # Latin-like scripts use whitespace; CJK scripts count each glyph as a word.
    tokens = [t for t in text.split() if t]
    if len(tokens) >= MIN_WORDS:
        return len(tokens)
    # For scripts without spaces (CJK), approximate word count as graphemes / 2.
    non_space = sum(1 for ch in text if not ch.isspace())
    approx = max(len(tokens), non_space // 2)
    return approx


def _is_short(seg) -> bool:
    return (
        seg.duration < MIN_DUR
        or seg.char_count < MIN_CHARS
        or _word_count(seg.text) < MIN_WORDS
    )


def _is_ultra_short(seg) -> bool:
    return seg.duration < ULTRA_SHORT_DUR or seg.char_count < ULTRA_SHORT_CHARS


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker_id: str = "Speaker 1"
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            **self.extra,
            "id": self.id,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "speaker_id": self.speaker_id,
        }


def _serialize_words(words: Sequence[Word]) -> list[dict]:
    """Word objects → the ``{text, start, end}`` dicts persisted on segments.

    Per-word timing is kept on each segment (``Segment.extra["words"]``, so
    ``to_dict`` carries it onto the job) to drive the karaoke hardsub export.
    """
    return [
        {"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}
        for w in words
    ]


def _merge_segment_extra(target: Segment, incoming: Segment, *, prepend: bool) -> None:
    """Preserve editor metadata when cleanup folds ``incoming`` into ``target``."""
    # Word lists must CONCATENATE in text order (the setdefault below would
    # otherwise adopt the incoming list wholesale when the target has none,
    # then double it). Capture both sides before setdefault runs.
    raw_target_words = target.extra.get("words")
    raw_incoming_words = incoming.extra.get("words")
    for key, value in incoming.extra.items():
        target.extra.setdefault(key, value)
    target_words = raw_target_words if isinstance(raw_target_words, list) else []
    incoming_words = raw_incoming_words if isinstance(raw_incoming_words, list) else []
    if target_words or incoming_words:
        target.extra["words"] = (
            incoming_words + target_words if prepend else target_words + incoming_words
        )

    def joined(left: object, right: object) -> str:
        return _clean(f"{left or ''} {right or ''}")

    target_original = target.extra.get("text_original")
    incoming_original = incoming.extra.get("text_original")
    if target_original is not None or incoming_original is not None:
        target.extra["text_original"] = (
            joined(incoming_original, target_original)
            if prepend
            else joined(target_original, incoming_original)
        )

    raw_target_translations = target.extra.get("translations")
    raw_incoming_translations = incoming.extra.get("translations")
    target_translations = raw_target_translations if isinstance(raw_target_translations, dict) else {}
    incoming_translations = (
        raw_incoming_translations if isinstance(raw_incoming_translations, dict) else {}
    )
    if target_translations or incoming_translations:
        merged = {}
        languages = {
            *target_translations.keys(),
            *incoming_translations.keys(),
        }
        for language in languages:
            target_text = target_translations.get(language)
            incoming_text = incoming_translations.get(language)
            merged[language] = (
                joined(incoming_text, target_text)
                if prepend
                else joined(target_text, incoming_text)
            )
        target.extra["translations"] = merged


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def _best_boundary(text: str, ideal_pos: int) -> int:
    """Return a character offset to split at. Prefer sentence > clause > word.

    Scans the full text for each candidate class and picks the one whose offset
    is closest to `ideal_pos`. Sentence endings always beat clause endings, which
    always beat bare word boundaries.
    """
    if not text:
        return 0
    length = len(text)
    if length <= 1:
        return length

    def _closest(offsets: List[int]) -> Optional[int]:
        if not offsets:
            return None
        return min(offsets, key=lambda o: abs(o - ideal_pos))

    sentence_offsets = [m.end(1) for m in _SENTENCE_END.finditer(text)]
    pick = _closest(sentence_offsets)
    if pick is not None:
        return pick

    clause_offsets = [m.end(1) for m in _CLAUSE_END.finditer(text)]
    pick = _closest(clause_offsets)
    if pick is not None:
        return pick

    # Bare word boundaries: every space position.
    space_offsets = [i for i, ch in enumerate(text) if ch == " "]
    pick = _closest(space_offsets)
    if pick is not None:
        return pick
    return length


def _words_from_whisper(result: dict) -> List[Word]:
    """Extract word-level timing if available, otherwise fall back to chunk-level."""
    words: List[Word] = []
    segs = result.get("segments") if isinstance(result, dict) else None
    if segs:
        for seg in segs:
            for w in seg.get("words", []) or []:
                wt = (w.get("word") or w.get("text") or "").strip()
                if not wt:
                    continue
                ws = float(w.get("start", seg.get("start", 0.0)))
                we = float(w.get("end", seg.get("end", ws + 0.1)))
                if we <= ws:
                    we = ws + 0.05
                words.append(Word(start=ws, end=we, text=wt))
        if words:
            return words

    # Fallback: chunk-level timings (no per-word granularity)
    for chunk in result.get("chunks", []) or []:
        ts = chunk.get("timestamp") or (0.0, 0.0)
        s = float(ts[0] or 0.0)
        e = float(ts[1] or s + 0.1)
        text = _clean(chunk.get("text", ""))
        if not text or e <= s:
            continue
        # Distribute time evenly across the tokens inside the chunk
        tokens = text.split(" ")
        dur = (e - s) / max(len(tokens), 1)
        t = s
        for tok in tokens:
            words.append(Word(start=t, end=t + dur, text=tok))
            t += dur
    return words


def _build_segments_from_words(words: Sequence[Word]) -> List[Segment]:
    """Greedy grouping of words into IDEAL_DUR sentences, cut at natural boundaries."""
    segments: List[Segment] = []
    if not words:
        return segments

    buf: List[Word] = []
    buf_start = words[0].start

    def flush_buf(force: bool = False) -> None:
        nonlocal buf, buf_start
        if not buf:
            return
        text = _clean(" ".join(w.text for w in buf))
        if not text:
            buf = []
            return
        segments.append(Segment(
            start=buf_start, end=buf[-1].end, text=text,
            extra={"words": _serialize_words(buf)},
        ))
        buf = []
        if not force:
            buf_start = 0.0

    for i, w in enumerate(words):
        if not buf:
            buf_start = w.start
        buf.append(w)
        buf_dur = buf[-1].end - buf_start
        buf_chars = sum(len(x.text) + 1 for x in buf)
        next_gap = 0.0
        if i + 1 < len(words):
            next_gap = max(0.0, words[i + 1].start - w.end)

        ends_sentence = bool(_SENTENCE_END.search(w.text))
        ends_clause = bool(_CLAUSE_END.search(w.text))

        too_long = buf_dur >= MAX_DUR or buf_chars >= MAX_CHARS
        at_ideal = buf_dur >= IDEAL_DUR and buf_chars >= MIN_CHARS

        # Natural-boundary flush at target length.
        if at_ideal and ends_sentence:
            flush_buf()
        elif too_long and (ends_sentence or ends_clause):
            flush_buf()
        elif too_long and next_gap >= 0.35:
            flush_buf()
        elif too_long:
            # Last-resort split on a word boundary. Choose the word whose
            # cumulative position is closest to IDEAL_DUR from buf_start.
            best_idx = None
            best_score = float("inf")
            for k, bw in enumerate(buf[:-1]):  # must leave ≥1 word on right
                left_dur = bw.end - buf_start
                if left_dur < MIN_DUR:
                    continue
                right_dur = buf[-1].end - buf[k + 1].start
                if right_dur < MIN_DUR:
                    continue
                # Prefer words ending in sentence / clause punctuation.
                boundary_bonus = 0.0
                if _SENTENCE_END.search(bw.text):
                    boundary_bonus = -2.0
                elif _CLAUSE_END.search(bw.text):
                    boundary_bonus = -0.8
                score = abs(left_dur - IDEAL_DUR) + boundary_bonus
                if score < best_score:
                    best_score = score
                    best_idx = k

            if best_idx is not None:
                left_buf = buf[: best_idx + 1]
                right_buf = buf[best_idx + 1 :]
                segments.append(Segment(
                    start=buf_start,
                    end=left_buf[-1].end,
                    text=_clean(" ".join(x.text for x in left_buf)),
                    extra={"words": _serialize_words(left_buf)},
                ))
                buf = list(right_buf)
                buf_start = right_buf[0].start
            else:
                flush_buf()

    flush_buf(force=True)
    return segments


def _merge_short(segments: List[Segment]) -> List[Segment]:
    """Fold fragments below the floor into adjacent same-speaker segment.

    Runs multi-pass until no further merges happen. Ultra-short segments
    (< 0.5s or < 4 chars) fold across larger gaps and across speakers when
    no same-speaker neighbor is close — stray tokens like "STR" are never
    allowed to survive as standalone segments.
    """
    if not segments:
        return segments

    for _ in range(64):  # bounded iterations so misuse can't hang
        did_merge = False
        i = 0
        while i < len(segments):
            s = segments[i]
            if not _is_short(s):
                i += 1
                continue

            prev = segments[i - 1] if i > 0 else None
            nxt = segments[i + 1] if i + 1 < len(segments) else None
            gap_tolerance = MERGE_GAP_ULTRA if _is_ultra_short(s) else MERGE_GAP

            prev_same = bool(prev and prev.speaker_id == s.speaker_id)
            next_same = bool(nxt and nxt.speaker_id == s.speaker_id)
            prev_gap = (s.start - prev.end) if prev else float("inf")
            next_gap = (nxt.start - s.end) if nxt else float("inf")

            prev_ok = prev_same and prev_gap <= gap_tolerance
            next_ok = next_same and next_gap <= gap_tolerance

            target = None
            if prev_ok and next_ok:
                target = prev if prev.duration <= nxt.duration else nxt
            elif prev_ok:
                target = prev
            elif next_ok:
                target = nxt
            elif prev_same:
                target = prev
            elif next_same:
                target = nxt
            elif _is_ultra_short(s):
                # Stray token — fold into closest neighbor regardless of speaker.
                if prev and nxt:
                    target = prev if prev_gap <= next_gap else nxt
                else:
                    target = prev or nxt
            elif prev:
                target = prev
            elif nxt:
                target = nxt

            if target is None:
                i += 1
                continue
            if target is prev:
                _merge_segment_extra(prev, s, prepend=False)
                prev.text = _clean(prev.text + " " + s.text)
                prev.end = max(prev.end, s.end)
                segments.pop(i)
                did_merge = True
                continue
            if target is nxt:
                _merge_segment_extra(nxt, s, prepend=True)
                nxt.text = _clean(s.text + " " + nxt.text)
                nxt.start = min(nxt.start, s.start)
                segments.pop(i)
                did_merge = True
                continue

            i += 1
        if not did_merge:
            break
    return segments


def _stitch_adjacent_shorts(segments: List[Segment]) -> List[Segment]:
    """Combine adjacent same-speaker segments when both are short and close.

    Catches the case where each segment individually passes MIN_DUR but a
    rapid-fire pair produces a jittery dub. Only stitches when both halves
    live under STITCH_DUR and the gap between them is minimal.
    """
    if len(segments) < 2:
        return segments

    for _ in range(32):
        did = False
        i = 0
        while i + 1 < len(segments):
            a, b = segments[i], segments[i + 1]
            same = a.speaker_id == b.speaker_id
            gap = b.start - a.end
            combined_dur = (b.end - a.start)
            if (
                same
                and gap <= STITCH_GAP
                and a.duration <= STITCH_DUR
                and b.duration <= STITCH_DUR
                and combined_dur <= MAX_DUR
            ):
                _merge_segment_extra(a, b, prepend=False)
                a.text = _clean(a.text + " " + b.text)
                a.end = b.end
                segments.pop(i + 1)
                did = True
                continue
            i += 1
        if not did:
            break
    return segments


def clean_up_segments(segments: List[dict]) -> List[dict]:
    """Public entry: run merge + stitch passes on already-persisted segments.

    Used by the UI's "Clean up segments" action so users can repair jobs
    that were segmented under older, looser rules.
    """
    objs: List[Segment] = []
    for s in segments or []:
        try:
            objs.append(Segment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=_clean(str(s.get("text", ""))),
                speaker_id=str(s.get("speaker_id") or "Speaker 1"),
                id=str(s.get("id") or uuid.uuid4().hex[:8]),
                extra={
                    key: value
                    for key, value in s.items()
                    if key not in {"id", "start", "end", "text", "speaker_id"}
                },
            ))
        except (TypeError, ValueError):
            continue
    objs = [s for s in objs if s.end > s.start and s.text]
    objs = _merge_short(objs)
    objs = _stitch_adjacent_shorts(objs)
    objs = _merge_short(objs)
    return [s.to_dict() for s in objs]


def _apply_scene_cuts(segments: List[Segment], scene_cuts: Iterable[float]) -> List[Segment]:
    """Split segments at scene cuts only if both halves remain viable."""
    cuts = sorted(c for c in scene_cuts if c > 0)
    if not cuts:
        return segments

    out: List[Segment] = []
    for s in segments:
        inner_cuts = [c for c in cuts if s.start + MIN_DUR < c < s.end - MIN_DUR]
        if not inner_cuts:
            out.append(s)
            continue

        remaining = s
        for cut in inner_cuts:
            dur_total = remaining.duration
            if dur_total <= 0:
                break
            words = remaining.extra.get("words")
            if isinstance(words, list) and words:
                # A camera cut is not a word boundary. Character-proportional
                # splitting assigned words seconds away from their real speech.
                candidates = [
                    i for i in range(1, len(words))
                    if float(words[i - 1]["end"]) <= float(words[i]["start"])
                    and abs(float(words[i]["start"]) - cut) <= 0.25
                ]
                if not candidates:
                    continue
                split = min(candidates, key=lambda i: abs(float(words[i]["start"]) - cut))
                left, right = words[:split], words[split:]
                left_text = _clean(" ".join(str(w.get("text", w.get("word", ""))) for w in left))
                right_text = _clean(" ".join(str(w.get("text", w.get("word", ""))) for w in right))
                left_end, right_start = float(left[-1]["end"]), float(right[0]["start"])
                if (len(left_text) < MIN_CHARS or len(right_text) < MIN_CHARS
                        or left_end - remaining.start < MIN_DUR
                        or remaining.end - right_start < MIN_DUR):
                    continue
                out.append(Segment(
                    start=remaining.start, end=left_end, text=left_text,
                    speaker_id=remaining.speaker_id, extra={**remaining.extra, "words": left},
                ))
                remaining = Segment(
                    start=right_start, end=remaining.end, text=right_text,
                    speaker_id=remaining.speaker_id, extra={**remaining.extra, "words": right},
                )
                continue
            ratio = (cut - remaining.start) / dur_total
            tentative_split = int(len(remaining.text) * ratio)
            pos = _best_boundary(remaining.text, tentative_split)
            left_text = remaining.text[:pos].strip()
            right_text = remaining.text[pos:].strip()
            # Viability check — refuse the cut if either half would be a fragment.
            if (
                not left_text
                or not right_text
                or len(left_text) < MIN_CHARS
                or len(right_text) < MIN_CHARS
                or (cut - remaining.start) < MIN_DUR
                or (remaining.end - cut) < MIN_DUR
            ):
                continue
            # Segment text is the joined word texts, so a whitespace-boundary
            # text split maps exactly onto a word-count split of the list.
            words = remaining.extra.get("words")
            left_extra: dict = {}
            right_extra: dict = {}
            if isinstance(words, list) and words:
                n_left = len(left_text.split())
                if n_left and len(words) > n_left:
                    left_extra = {"words": words[:n_left]}
                    right_extra = {"words": words[n_left:]}
            out.append(Segment(
                start=remaining.start, end=cut, text=left_text, speaker_id=remaining.speaker_id,
                extra=left_extra,
            ))
            remaining = Segment(
                start=cut, end=remaining.end, text=right_text, speaker_id=remaining.speaker_id,
                extra=right_extra,
            )
        out.append(remaining)
    return out


def segment_transcript(
    whisper_result: dict,
    duration: float,
    scene_cuts: Optional[Iterable[float]] = None,
) -> List[dict]:
    """Public entry point: whisper result → clean dub segments (as dicts)."""
    words = _words_from_whisper(whisper_result)
    if not words:
        text = _clean((whisper_result or {}).get("text", ""))
        if text:
            return [Segment(start=0.0, end=max(duration, 0.1), text=text).to_dict()]
        return []

    segments = _build_segments_from_words(words)
    segments = _merge_short(segments)
    if scene_cuts:
        segments = _apply_scene_cuts(segments, scene_cuts)
        segments = _merge_short(segments)
    segments = _stitch_adjacent_shorts(segments)
    segments = _merge_short(segments)
    return [s.to_dict() for s in segments]


def assign_speakers_from_diarization(
    segments: List[dict],
    diarization,
) -> List[dict]:
    """Replace speaker_id based on a pyannote diarization result (overlap-weighted)."""
    for s in segments:
        start, end = s["start"], s["end"]
        mid = (start + end) / 2.0
        overlap: dict[str, float] = {}
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            left = max(start, turn.start)
            right = min(end, turn.end)
            if right > left:
                overlap[speaker] = overlap.get(speaker, 0.0) + (right - left)
        if overlap:
            winner = max(overlap.items(), key=lambda kv: kv[1])[0]
        else:
            # fall back to midpoint membership
            winner = None
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                if turn.start <= mid <= turn.end:
                    winner = speaker
                    break
        if winner is not None:
            try:
                idx = int(winner.split("_")[-1]) + 1
                s["speaker_id"] = f"Speaker {idx}"
            except ValueError:
                s["speaker_id"] = winner
    return segments


def assign_speakers_from_turns(
    segments: List[dict],
    turns: List[dict],
) -> List[dict]:
    """Assign speaker_id by overlap against a list of ``{start, end, speaker}``
    turns produced by an ASR backend that diarizes inline (e.g. FunASR's cam++).

    Mirrors :func:`assign_speakers_from_diarization`'s overlap-weighting (winner
    = most-overlapping speaker; midpoint membership as fallback) without a
    pyannote object. ``speaker`` is used verbatim — FunASR already labels its
    speakers ``"Speaker N"``. Falls back to the silence-gap heuristic when no
    usable turns are supplied.
    """
    clean = [
        t for t in (turns or [])
        if t.get("speaker") is not None and t.get("start") is not None and t.get("end") is not None
    ]
    if not clean:
        return assign_speakers_heuristic(segments)
    for s in segments:
        start, end = s["start"], s["end"]
        mid = (start + end) / 2.0
        overlap: dict = {}
        for t in clean:
            left = max(start, t["start"])
            right = min(end, t["end"])
            if right > left:
                overlap[t["speaker"]] = overlap.get(t["speaker"], 0.0) + (right - left)
        if overlap:
            s["speaker_id"] = max(overlap.items(), key=lambda kv: kv[1])[0]
        else:
            for t in clean:
                if t["start"] <= mid <= t["end"]:
                    s["speaker_id"] = t["speaker"]
                    break
    return segments


def assign_speakers_heuristic(
    segments: List[dict], num_speakers: Optional[int] = None
) -> List[dict]:
    """Silence-gap speaker assignment (used when no diarization model runs).

    Base signal: a gap > SPEAKER_GAP seconds between consecutive segments is
    treated as a speaker change. Without a ``num_speakers`` hint this keeps
    the legacy behavior — alternate between exactly two labels. With a hint:

    * ``num_speakers=1`` → every segment gets ``"Speaker 1"``.
    * ``num_speakers>=2`` → labels round-robin across N speakers at each
      gap boundary, so the user's requested count is represented instead of
      being silently capped at 2.

    Limits (be honest with callers): this honors the *count*, not voice
    identity. The rotation order is arbitrary (a returning speaker gets the
    next label in the cycle, not their own), rapid exchanges with no
    > SPEAKER_GAP pause still collapse into one label, and N is an upper
    bound — audio with fewer gap boundaries than N yields fewer labels.
    Real per-speaker attribution needs pyannote (or an inline-diarizing ASR
    backend); callers should warn the user accordingly (see dub_core).
    Invalid hints (non-int, < 1) fall back to the legacy two-speaker cycle.
    """
    try:
        n = int(num_speakers) if num_speakers is not None else 2
    except (TypeError, ValueError):
        n = 2
    if n < 1:
        n = 2
    current = 0  # zero-based rotation index; rendered one-based below
    last_end = 0.0
    for i, s in enumerate(segments):
        if i > 0 and n > 1 and (s["start"] - last_end) > SPEAKER_GAP:
            current = (current + 1) % n
        s["speaker_id"] = f"Speaker {current + 1}"
        last_end = s["end"]
    return segments


# ── Speaker-aware re-split (#486) ────────────────────────────────────────────
#
# Segmentation runs BEFORE diarization and groups words by sentence/duration
# only, so one segment can span two speakers' turns. assign_speakers_* then only
# *relabels* each segment with its majority speaker — the boundary is lost and a
# two-speaker exchange reads as one line. This pass re-splits such a segment at
# the word-level speaker boundary, after diarization.
#
# Hard invariant (the single-speaker no-regression guarantee): a segment whose
# words all map to ONE speaker is returned byte-for-byte unchanged — same dict,
# id, text, start, end — so single-speaker dubs and their timing never move.

def _word_speaker(w: "Word", turns: Sequence[tuple]) -> Optional[str]:
    """Majority-overlap speaker label for a word; midpoint membership as a
    fallback; ``None`` when the word has no diarization coverage at all."""
    acc: dict = {}
    for ts, te, label in turns:
        left = max(w.start, ts)
        right = min(w.end, te)
        if right > left:
            acc[label] = acc.get(label, 0.0) + (right - left)
    if acc:
        return max(acc.items(), key=lambda kv: kv[1])[0]
    mid = (w.start + w.end) / 2.0
    for ts, te, label in turns:
        if ts <= mid <= te:
            return label
    return None


def _fill_and_smooth(labels: List[Optional[str]]) -> List[Optional[str]]:
    """Forward/back-fill gaps (words with no coverage inherit a neighbor) and
    smooth single-word flips, so one mis-attributed word inside a speaker's run
    (diarization noise) doesn't trigger a spurious split."""
    out = list(labels)
    n = len(out)
    last = None
    for i in range(n):
        if out[i] is None:
            out[i] = last
        else:
            last = out[i]
    nxt = None
    for i in range(n - 1, -1, -1):
        if out[i] is None:
            out[i] = nxt
        else:
            nxt = out[i]
    for i in range(1, n - 1):
        if out[i] != out[i - 1] and out[i - 1] == out[i + 1]:
            out[i] = out[i - 1]
    return out


def _resplit_core(
    segments: List[dict], words: Sequence["Word"], turns: Sequence[tuple],
) -> List[dict]:
    """Split each segment that spans >1 speaker at the word-level boundary.

    ``turns`` is a normalised list of ``(start, end, speaker_label)``. Single-
    speaker segments are passed through untouched. Pieces keep the segment's
    outer start/end (preserving any onset-snap) and use word times for interior
    boundaries, so the pieces exactly cover the original span.
    """
    if not turns or not words:
        return segments
    ordered = sorted(words, key=lambda w: (w.start, w.end))
    out: List[dict] = []
    for seg in segments:
        s0, s1 = seg["start"], seg["end"]
        seg_words = [w for w in ordered if min(w.end, s1) - max(w.start, s0) > 1e-6]
        if len(seg_words) < 2:
            out.append(seg)
            continue
        labels = _fill_and_smooth([_word_speaker(w, turns) for w in seg_words])
        if len({l for l in labels if l is not None}) <= 1:
            out.append(seg)  # single speaker (or unknown) → byte-for-byte unchanged
            continue
        runs: List[tuple] = []
        for w, label in zip(seg_words, labels):
            if runs and runs[-1][0] == label:
                runs[-1][1].append(w)
            else:
                runs.append((label, [w]))
        n_runs = len(runs)
        piece_no = 0
        for k, (label, ws) in enumerate(runs):
            text = _clean(" ".join(w.text for w in ws))
            if not text:
                continue
            piece = dict(seg)
            piece["text"] = text
            piece["start"] = s0 if k == 0 else ws[0].start
            piece["end"] = s1 if k == n_runs - 1 else ws[-1].end
            # dict(seg) copied the WHOLE segment's word list into every piece;
            # each piece keeps only its own run's words (karaoke burn-in).
            if "words" in piece:
                piece["words"] = _serialize_words(ws)
            if label:
                piece["speaker_id"] = label
            if piece_no > 0:
                piece["id"] = f"{seg.get('id', 'seg')}-{piece_no}"
                if "text_original" in piece:
                    piece["text_original"] = text
            elif "text_original" in piece:
                piece["text_original"] = text
            out.append(piece)
            piece_no += 1
    return out


def _diar_speaker_label(raw) -> str:
    """``SPEAKER_00`` → ``Speaker 1`` (mirrors assign_speakers_from_diarization)."""
    try:
        return f"Speaker {int(str(raw).split('_')[-1]) + 1}"
    except (ValueError, AttributeError):
        return str(raw)


def resplit_segments_by_diarization(
    segments: List[dict], words: Sequence["Word"], diarization,
) -> List[dict]:
    """Speaker-aware re-split using a pyannote diarization result (#486)."""
    turns = [
        (turn.start, turn.end, _diar_speaker_label(spk))
        for turn, _, spk in diarization.itertracks(yield_label=True)
    ]
    return _resplit_core(segments, words, turns)


def resplit_segments_by_turns(
    segments: List[dict], words: Sequence["Word"], turns: Sequence[dict],
) -> List[dict]:
    """Speaker-aware re-split using inline ASR speaker turns (FunASR cam++).

    ``speaker`` is used verbatim (FunASR already labels ``"Speaker N"``), matching
    :func:`assign_speakers_from_turns`."""
    norm = [
        (t["start"], t["end"], t["speaker"])
        for t in (turns or [])
        if t.get("speaker") is not None
        and t.get("start") is not None
        and t.get("end") is not None
    ]
    return _resplit_core(segments, words, norm)


def deduplicate_chunk_segments(segments: list[dict]) -> list[dict]:
    """Remove repeated ASR context only when matching words share timestamps.

    Preserve different speakers and genuine repeated speech at different times.
    Input order retains chunk provenance even when a later chunk starts earlier.
    """
    import re

    def token(word):
        return re.sub(r'[^\w]', '', str(word.get('text', word.get('word', ''))).casefold())

    def timed_words(segment):
        words = segment.get('words') or []
        return words if words and all(isinstance(w, dict) and isinstance(w.get('start'), (int, float))
                                     and isinstance(w.get('end'), (int, float)) for w in words) else []

    result = []
    for segment in segments:
        words = timed_words(segment)
        matches = []
        if words and segment.get("speaker_id"):
            prior_words = [w for previous in result
                           if previous.get('speaker_id') == segment.get('speaker_id')
                           for w in timed_words(previous)
                           if w['end'] >= words[0]['start'] - .35 and w['start'] <= words[-1]['end'] + .35]
            for index, word in enumerate(words):
                if token(word) and any(token(word) == token(prior)
                                      and abs((word['start'] + word['end']) / 2 - (prior['start'] + prior['end']) / 2) <= .35
                                      for prior in prior_words):
                    matches.append(index)
        # Require a substantial matching prefix; isolated common words cannot
        # authorize deleting speech. No timing-only truncation is performed.
        if len(matches) >= 3 and len(matches) / (matches[-1] + 1) >= .6:
            cutoff = max((w['end'] for w in prior_words), default=0)
            remaining = [w for w in words[matches[-1] + 1:] if w['start'] >= cutoff - .05]
            if not remaining:
                continue
            text = _clean(' '.join(str(w.get('text', w.get('word', ''))) for w in remaining))
            segment = {**segment, 'start': remaining[0]['start'], 'end': remaining[-1]['end'],
                       'text': text, 'text_original': text, 'words': remaining}
        result.append(segment)
    def bounds(row):
        words = timed_words(row)
        if not words:
            return None
        if any(a['start'] > b['start'] for a, b in zip(words, words[1:])):
            # Older chunk stitching can attach an earlier word to a later
            # line. Never invert an interval or move it backwards over speech.
            inside = [w for w in words if row['start'] <= w['start'] <= w['end'] <= row['end']]
            if len(inside) < .6 * len(words):
                return None
            words = inside
        start, end = min(w['start'] for w in words), max(w['end'] for w in words)
        return (start, end) if end > start else None

    # Repair stale camera-cut bounds only when timed words prove the two
    # spoken intervals are disjoint. Genuine overlapping speech stays intact.
    adjust = set()
    ordered = sorted(enumerate(result), key=lambda item: item[1]['start'])
    for position, (left_index, left) in enumerate(ordered):
        for right_index, right in ordered[position + 1:]:
            if right['start'] >= left['end']:
                break
            a, b = bounds(left), bounds(right)
            if a and b and (a[1] <= b[0] or b[1] <= a[0]):
                adjust.update((left_index, right_index))
    result = [{**row, 'start': bounds(row)[0], 'end': bounds(row)[1]}
              if index in adjust else row for index, row in enumerate(result)]
    return result
