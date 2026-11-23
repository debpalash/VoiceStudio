"""
NLP-aware subtitle segmentation — Phase 1.2 (ROADMAP.md).

Takes the raw ASR output (Whisper / WhisperX / mlx-whisper) and re-chunks it
so every resulting segment respects the Netflix subtitle style guide:

    • ≤ 42 characters per line
    • ≤ 2 lines per subtitle (but we prefer single-line)
    • ≤ 17 characters per second (CPS)
    • no orphan fragments (< 1.2 s or < 8 characters)

Splits happen at, in priority order:
    1. Sentence terminators  . ? ! 。 ？ ！
    2. Clause separators     , ; : —  ，；：
    3. Conjunctions          "and", "but", "or", "so", "however", …
    4. Last resort: greedy word packing at the 42-char boundary.

When word-level timings are available (WhisperX provides `words: [{text,
start, end}]` on each segment), splits are placed at exact word boundaries.
Otherwise we proportionally interpolate by character offset — good enough.

Short adjacent segments are merged *after* splitting so we don't emit
"Yes." / "No." one-char subtitles.

Pure function — no model, no network. Deterministic, fast, easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# ── Tunable thresholds ────────────────────────────────────────────────────────

MAX_CHARS_PER_LINE = 42   # Netflix single-line cap
MAX_LINES         = 2     # Netflix hard cap (we still prefer 1)
MAX_CPS           = 17    # Netflix reading-speed ceiling
MIN_DURATION_S    = 1.2   # Below this → merge with neighbour
MIN_CHARS         = 8     # Same for very short text
MAX_CHARS_TOTAL   = MAX_CHARS_PER_LINE * MAX_LINES  # 84

# Sentence terminators (+CJK). Keep the terminator ON the left piece.
_SENT_TERM_RE = re.compile(r"([\.!\?。！？]+)(\s+)")

# Clause separators. Keep on the left.
_CLAUSE_SPLIT_RE = re.compile(r"([,;:—，；：])\s+")

# Conjunctions. Split BEFORE the conjunction word.
_CONJUNCTIONS = {
    "and", "but", "or", "so", "however", "because",
    "although", "while", "therefore", "meanwhile",
    "y", "pero", "o",                       # es
    "et", "mais", "ou", "donc",             # fr
    "und", "aber", "oder", "weil",          # de
}
_CONJ_RE = re.compile(
    r"\s+(" + "|".join(sorted(_CONJUNCTIONS, key=len, reverse=True)) + r")\s+",
    flags=re.IGNORECASE,
)


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class Word:
    text:  str
    start: float
    end:   float


@dataclass
class Seg:
    start: float
    end:   float
    text:  str
    words: List[Word] = field(default_factory=list)
    # Any extra metadata (speaker_id, language, etc.) flows through untouched.
    extras: dict = field(default_factory=dict)


# ── Public API ───────────────────────────────────────────────────────────────


def segment_for_subtitles(segments: list[dict]) -> list[dict]:
    """
    Top-level entry: list[dict] in, list[dict] out. Preserves every key the
    caller passed in other than `text`, `start`, `end`, and `words`, which this
    function owns.
    """
    if not segments:
        return []

    normalized = [_to_seg(s) for s in segments]
    splits: list[Seg] = []
    for s in normalized:
        splits.extend(_split_one(s))
    merged = _merge_tiny_neighbours(splits)
    return [_from_seg(s) for s in merged]


# ── Adapters ──────────────────────────────────────────────────────────────────

def _to_seg(d: dict) -> Seg:
    words = []
    for w in (d.get("words") or []):
        if "start" in w and "end" in w and "text" in w:
            words.append(Word(text=str(w["text"]), start=float(w["start"]), end=float(w["end"])))
        elif "word" in w and "start" in w and "end" in w:
            words.append(Word(text=str(w["word"]), start=float(w["start"]), end=float(w["end"])))
    # Keep extras: anything not owned by the segmenter.
    extras = {k: v for k, v in d.items() if k not in ("start", "end", "text", "words")}
    return Seg(
        start=float(d.get("start", 0.0)),
        end=float(d.get("end", 0.0)),
        text=(d.get("text") or "").strip(),
        words=words,
        extras=extras,
    )


def _from_seg(s: Seg) -> dict:
    out = {**s.extras, "start": s.start, "end": s.end, "text": s.text.strip()}
    if s.words:
        out["words"] = [{"text": w.text, "start": w.start, "end": w.end} for w in s.words]
    return out


# ── Splitter ──────────────────────────────────────────────────────────────────

def _split_one(s: Seg) -> List[Seg]:
    """Recursively split `s` until every piece fits MAX_CHARS_TOTAL + MAX_CPS."""
    text = s.text
    dur = max(1e-3, s.end - s.start)
    cps = len(text) / dur

    if len(text) <= MAX_CHARS_TOTAL and cps <= MAX_CPS:
        return [s]

    cut = _pick_cut(text)
    if cut is None or cut <= 0 or cut >= len(text):
        # Can't find a natural cut — fall back to a hard word-boundary split
        # closest to half-length.
        cut = _hard_word_cut(text)
        if cut is None:
            return [s]  # one-word rumble; accept the violation

    left_text = text[:cut].rstrip()
    right_text = text[cut:].lstrip()
    if not left_text or not right_text:
        return [s]

    split_t = _time_at_char(s, cut)
    left = Seg(start=s.start, end=split_t, text=left_text,
               words=[w for w in s.words if w.end <= split_t + 1e-4],
               extras=dict(s.extras))
    right = Seg(start=split_t, end=s.end, text=right_text,
                words=[w for w in s.words if w.start >= split_t - 1e-4],
                extras=dict(s.extras))

    # Recurse — splits might still be too long.
    return _split_one(left) + _split_one(right)


def _pick_cut(text: str) -> Optional[int]:
    """
    Return the character index to split at, or None if no natural cut fits.
    Priority: sentence > clause > conjunction. Prefer a cut near the middle
    of the text so neither side is tiny.
    """
    n = len(text)
    target = n / 2
    cands: list[tuple[int, int]] = []  # (priority, char_index_of_cut)

    for m in _SENT_TERM_RE.finditer(text):
        # Cut after the whitespace run so the terminator stays on the left piece.
        cands.append((1, m.end()))
    for m in _CLAUSE_SPLIT_RE.finditer(text):
        cands.append((2, m.end()))
    for m in _CONJ_RE.finditer(text):
        # Cut BEFORE the conjunction (after the preceding whitespace).
        cands.append((3, m.start() + 1))

    # If nothing fits OR all candidates are at the very start/end, bail.
    cands = [(p, c) for (p, c) in cands if 0 < c < n]
    if not cands:
        return None

    # Prefer highest-priority candidate closest to the mid-point that doesn't
    # leave either side above MAX_CHARS_TOTAL — if impossible, any mid-ish cut.
    cands.sort(key=lambda pc: (pc[0], abs(pc[1] - target)))
    for _pri, c in cands:
        if _fits(text[:c], text[c:]):
            return c
    # Nothing produces a legal split alone, but we still want forward progress
    # — return the best midpoint cut so recursion can chip away.
    return cands[0][1]


def _fits(left: str, right: str) -> bool:
    return len(left.strip()) <= MAX_CHARS_TOTAL and len(right.strip()) <= MAX_CHARS_TOTAL


def _hard_word_cut(text: str) -> Optional[int]:
    """Space-boundary cut nearest the midpoint. None if no spaces."""
    target = len(text) // 2
    best = None
    best_dist = None
    for i, ch in enumerate(text):
        if ch.isspace():
            d = abs(i - target)
            if best_dist is None or d < best_dist:
                best, best_dist = i, d
    return best


def _time_at_char(s: Seg, char_idx: int) -> float:
    """Find the timestamp corresponding to `char_idx`. Uses word timings if present.

    When `char_idx` falls in the whitespace GAP between two words, we return
    the previous word's end-time (the natural pause) rather than the next
    word's end — so splits land on real breaths, not mid-word.
    """
    if s.words:
        cursor = 0
        text = s.text
        last_end = s.start
        for w in s.words:
            # Skip whitespace between words.
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            # Cut falls before this word starts → it's in the gap. Use the
            # previous word's end (the pause between them).
            if char_idx <= cursor:
                return last_end
            wlen = len(w.text)
            if cursor + wlen >= char_idx:
                return w.end  # mid-word cut — round up to end of covering word
            cursor += wlen
            last_end = w.end
        return s.end
    # No word timings → proportional interpolation on chars.
    frac = char_idx / max(1, len(s.text))
    return s.start + frac * (s.end - s.start)


# ── Merger ────────────────────────────────────────────────────────────────────

def _merge_tiny_neighbours(segs: List[Seg]) -> List[Seg]:
    """
    Fold segments shorter than MIN_DURATION_S / MIN_CHARS into their
    nearest in-sentence neighbour, as long as the merge doesn't violate
    MAX_CHARS_TOTAL or MAX_CPS.
    """
    if not segs:
        return segs
    out: List[Seg] = []
    for s in segs:
        if out and _should_merge(out[-1], s):
            out[-1] = _merge(out[-1], s)
        else:
            out.append(s)
    return out


def _should_merge(a: Seg, b: Seg) -> bool:
    ad = a.end - a.start
    bd = b.end - b.start
    a_tiny = ad < MIN_DURATION_S or len(a.text) < MIN_CHARS
    b_tiny = bd < MIN_DURATION_S or len(b.text) < MIN_CHARS
    if not (a_tiny or b_tiny):
        return False
    # Don't merge across a hard sentence boundary — keep the reading beat.
    if _SENT_TERM_RE.search(a.text + " "):
        return False
    combined = f"{a.text} {b.text}".strip()
    combined_dur = max(1e-3, b.end - a.start)
    if len(combined) > MAX_CHARS_TOTAL:
        return False
    if len(combined) / combined_dur > MAX_CPS:
        return False
    # Don't bridge speaker changes when we know them.
    if a.extras.get("speaker_id") and b.extras.get("speaker_id") \
       and a.extras["speaker_id"] != b.extras["speaker_id"]:
        return False
    return True


def _merge(a: Seg, b: Seg) -> Seg:
    return Seg(
        start=a.start, end=b.end,
        text=f"{a.text} {b.text}".strip(),
        words=a.words + b.words,
        extras={**a.extras, **b.extras},
    )


# ── Layout helper (UI / SRT rendering convenience) ────────────────────────────


def format_subtitle_lines(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """
    Greedy word-wrap text into ≤ MAX_LINES lines of ≤ max_chars each. Returns
    the list of lines. If text is inherently too long, the last line overflows
    rather than truncating.
    """
    words = text.split()
    lines: list[str] = []
    line: list[str] = []
    length = 0
    for word in words:
        if line and len(lines) < MAX_LINES - 1 and length + 1 + len(word) > max_chars:
            lines.append(" ".join(line))
            line = []
            length = 0
        length += len(word) + bool(line)
        line.append(word)
    if line:
        lines.append(" ".join(line))
    return lines
