"""Book-level progress, throughput and ETA for a long-form render (#3417).

A chapter used to be the only unit the render reported, so a 5,000-word
chapter on a slow accelerator meant an hour of silence: the UI could not tell
"slow" from "hung". The renderer now reports every finished chunk; this module
turns those reports into one book-wide snapshot.

Work is measured in script characters. Each chapter weighs its raw script
length; the renderer reports progress *within* a chapter as a fraction of its
own (normalized) text, which is scaled onto that weight, so normalization and
lexicon rewrites never skew the book total.

Throughput comes from a rolling window over the most recent *freshly
synthesized* chunks only. Cached chunks and cached chapters advance the
progress bar but never the rate: replaying a WAV from disk says nothing about
how fast the engine speaks, and counting it made the old chapter-based ETA
wildly optimistic on a resumed render. The ETA is the remaining audio divided
by the measured realtime factor (audio seconds per wall second), where the
remaining audio is estimated from the remaining text at the window's measured
audio-per-character rate — which reduces to remaining characters times wall
seconds per character.

Pure and torch-free, with an injectable clock, so the math is unit-tested.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence

#: Fresh chunks the rolling rate averages over. Small enough to follow a
#: change in speed (thermal throttling, another job sharing the GPU), large
#: enough that one long sentence doesn't swing the estimate.
RATE_WINDOW = 12


class RenderProgress:
    """Tracks a render's progress across chapters and estimates time left."""

    def __init__(
        self,
        chapter_weights: Sequence[int],
        *,
        window: int = RATE_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._weights = [max(0, int(w)) for w in chapter_weights]
        self._total = sum(self._weights)
        self._clock = clock
        self._started = clock()
        self._finished: set[int] = set()
        self._partial: dict[int, float] = {}  # chapter -> fraction rendered
        self._samples: deque[tuple[float, float, float]] = deque(maxlen=max(1, window))
        self._mark: float | None = None  # when the last chunk finished
        self.chapter: int | None = None

    # ── feeding ─────────────────────────────────────────────────────────

    def start_chapter(self, index: int, *, now: float | None = None) -> None:
        """A chapter is about to render; its first chunk is timed from here."""
        self.chapter = index
        self._mark = self._clock() if now is None else now

    def chunk(
        self,
        index: int,
        done: float,
        total: float,
        *,
        audio_s: float = 0.0,
        cached: bool = False,
        now: float | None = None,
    ) -> None:
        """``done`` of ``total`` units of chapter ``index`` are rendered.

        ``audio_s`` is the audio this step produced. Only a fresh step with a
        measurable duration feeds the rate.
        """
        if index in self._finished:
            return  # a late report from an abandoned chapter's thread
        now = self._clock() if now is None else now
        frac = min(1.0, max(0.0, done / total)) if total > 0 else 1.0
        prev = self._partial.get(index, 0.0)
        self._partial[index] = max(prev, frac)
        if not cached and self._mark is not None:
            wall = now - self._mark
            units = (self._partial[index] - prev) * self._weights_at(index)
            if wall > 0 and units > 0:
                self._samples.append((units, wall, max(0.0, float(audio_s))))
        self._mark = now

    def finish_chapter(self, index: int, *, now: float | None = None) -> None:
        """Chapter ``index`` is over — rendered, served from cache, or failed."""
        self._finished.add(index)
        self._partial.pop(index, None)
        self._mark = self._clock() if now is None else now

    # ── reading ─────────────────────────────────────────────────────────

    def _weights_at(self, index: int) -> int:
        return self._weights[index] if 0 <= index < len(self._weights) else 0

    def fraction(self) -> float:
        """Share of the whole book that is rendered, 0.0–1.0."""
        n = len(self._weights)
        if self._total <= 0:
            return (len(self._finished) / n) if n else 1.0
        done = sum(self._weights_at(i) for i in self._finished)
        done += sum(f * self._weights_at(i) for i, f in self._partial.items()
                    if i not in self._finished)
        return min(1.0, done / self._total)

    def remaining_units(self) -> float:
        return max(0.0, self._total * (1.0 - self.fraction()))

    def realtime_factor(self) -> float | None:
        """Audio seconds produced per wall second over the window."""
        wall = sum(s[1] for s in self._samples)
        audio = sum(s[2] for s in self._samples)
        return audio / wall if wall > 0 and audio > 0 else None

    def chunk_seconds(self) -> float | None:
        """Mean wall time of a fresh chunk over the window (stall baseline)."""
        if not self._samples:
            return None
        return sum(s[1] for s in self._samples) / len(self._samples)

    def eta_seconds(self) -> float | None:
        """Estimated wall seconds left, or ``None`` until a fresh chunk is timed."""
        # remaining audio (remaining chars x audio/char) over the realtime
        # factor (audio/wall) — algebraically remaining chars x wall/char, which
        # also holds for an engine whose audio length we could not measure.
        units = sum(s[0] for s in self._samples)
        wall = sum(s[1] for s in self._samples)
        if units <= 0 or wall <= 0:
            return None
        return self.remaining_units() * (wall / units)

    def snapshot(self, *, now: float | None = None) -> dict:
        """The additive fields of a ``progress`` SSE event."""
        now = self._clock() if now is None else now

        def _r(v: float | None, nd: int = 1) -> float | None:
            # A positive measurement never reads as 0: a fast chunk (under
            # 0.05 s) used to report chunk_s 0.0, which says "no time at all".
            if v is None:
                return None
            r = round(v, nd)
            return 10.0 ** -nd if v > 0 and r == 0 else r

        return {
            "chapter": self.chapter,
            "fraction": round(self.fraction(), 4),
            "eta_s": _r(self.eta_seconds()),
            "rtf": _r(self.realtime_factor(), 3),
            "chunk_s": _r(self.chunk_seconds()),
            "elapsed_s": _r(now - self._started),
        }


def chapter_weight(chapter) -> int:
    """A chapter's share of the book: the characters it will speak."""
    return sum(len(getattr(s, "text", "") or "") for s in getattr(chapter, "spans", ()))


__all__ = ["RATE_WINDOW", "RenderProgress", "chapter_weight"]
