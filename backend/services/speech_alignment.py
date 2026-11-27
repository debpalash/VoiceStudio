"""Character timings for synthesized speech (ElevenLabs ``with-timestamps``).

``character_alignment`` maps the caller's text onto a take's timeline. With
word timings from speech recognition, recognized words are matched to the
text's words (case and punctuation ignored); words the recognizer heard
differently ("3" spoken as "three") get times interpolated between their
matched neighbours. Without word timings, time is spread over the speech span
in proportion to each word's length. Characters inside a word share its span
evenly; spaces and punctuation between words cover the gap between them.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

_WORD_RE = re.compile(r"\S+")
_NORMALIZE_RE = re.compile(r"[^\w]+", re.UNICODE)


def _norm(token: str) -> str:
    return _NORMALIZE_RE.sub("", token).casefold()


def _spread(start: float, end: float, n: int) -> list[tuple[float, float]]:
    if n <= 0:
        return []
    step = max(end - start, 0.0) / n
    return [(start + i * step, start + (i + 1) * step) for i in range(n)]


def _word_spans(tokens: list[re.Match], words: list[dict], speech: tuple[float, float]):
    """One ``(start, end)`` per text token."""
    n = len(tokens)
    spans: list[Optional[tuple[float, float]]] = [None] * n
    if words:
        heard = [_norm(str(w.get("word", w.get("text", "")))) for w in words]
        wanted = [_norm(t.group()) for t in tokens]
        matcher = difflib.SequenceMatcher(a=wanted, b=heard, autojunk=False)
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                w = words[block.b + k]
                spans[block.a + k] = (float(w["start"]), float(w["end"]))
    # Unmatched runs: share the time between the matched neighbours (or the
    # speech span's edges) by character count.
    i = 0
    while i < n:
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j < n and spans[j] is None:
            j += 1
        left = spans[i - 1][1] if i > 0 else speech[0]
        right = spans[j][0] if j < n else speech[1]
        right = max(right, left)
        weights = [max(len(tokens[k].group()), 1) for k in range(i, j)]
        total = sum(weights)
        cursor = left
        for k, weight in zip(range(i, j), weights):
            width = (right - left) * weight / total
            spans[k] = (cursor, cursor + width)
            cursor += width
        i = j
    # Keep the timeline monotonic even if the recognizer's words overlap.
    last = speech[0]
    out = []
    for start, end in spans:  # type: ignore[misc]
        start = max(start, last)
        end = max(end, start)
        out.append((start, end))
        last = end
    return out


def character_alignment(
    text: str,
    duration: float,
    *,
    words: Optional[list[dict]] = None,
    speech: Optional[tuple[float, float]] = None,
) -> dict:
    """ElevenLabs' ``alignment`` object for ``text`` over ``duration`` seconds.

    ``words`` are recognized words with ``start``/``end`` seconds; ``speech``
    is the ``(start, end)`` of audible speech (defaults to the whole take).
    """
    speech = speech or (0.0, duration)
    tokens = list(_WORD_RE.finditer(text))
    starts = [0.0] * len(text)
    ends = [0.0] * len(text)
    spans = _word_spans(tokens, words or [], speech)
    previous_end = speech[0]
    previous_char = 0
    for token, (start, end) in zip(tokens, spans):
        # Characters between the previous word and this one cover the gap.
        for pos, (s, e) in zip(range(previous_char, token.start()),
                               _spread(previous_end, start, token.start() - previous_char)):
            starts[pos], ends[pos] = s, e
        for pos, (s, e) in zip(range(token.start(), token.end()),
                               _spread(start, end, token.end() - token.start())):
            starts[pos], ends[pos] = s, e
        previous_end, previous_char = end, token.end()
    tail_end = max(speech[1], previous_end)
    for pos, (s, e) in zip(range(previous_char, len(text)),
                           _spread(previous_end, tail_end, len(text) - previous_char)):
        starts[pos], ends[pos] = s, e
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(v, 3) for v in starts],
        "character_end_times_seconds": [round(v, 3) for v in ends],
    }


def speech_bounds(samples, sample_rate: int, *, threshold_db: float = -40.0) -> tuple[float, float]:
    """``(start, end)`` seconds of the audible part of a mono float take."""
    import numpy as np

    arr = np.abs(np.asarray(samples, dtype=np.float32).reshape(-1))
    duration = arr.size / sample_rate if sample_rate else 0.0
    if arr.size == 0:
        return 0.0, 0.0
    loud = np.flatnonzero(arr >= 10 ** (threshold_db / 20))
    if loud.size == 0:
        return 0.0, duration
    return loud[0] / sample_rate, (loud[-1] + 1) / sample_rate
