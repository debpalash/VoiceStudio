"""Aggregate download progress across parallel files/chunks (FDL-06).

`huggingface_hub`/Xet download many files (and, within a file, many byte-range
chunks) concurrently. The per-file tqdm events that :mod:`utils.hf_progress`
emits are great for a detail view, but summing them on the frontend to derive
an *overall* speed/remaining/ETA is fragile under parallel fetch. This module
owns the single source of truth for overall progress:

  * seeded by the dry-run preflight totals (bytes that will actually download),
  * fed per-bar updates from the patched tqdm — distinguishing byte bars
    (unit 'B') from the "Fetching N files" count bar, and crediting a file's
    full size when its bar closes (under Xet a byte bar often never increments
    `n`, so completion is the only reliable byte signal),
  * emits ONE throttled ``phase:"aggregate"`` event with bytes_done /
    total_bytes / a windowed instantaneous rate / ETA / files done+total.

No circular import: :mod:`utils.hf_progress` calls :func:`feed` via a sink it is
handed at startup; this module only *imports* hf_progress to emit.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from utils import hf_progress

# Sliding window (seconds) for the instantaneous-rate estimate. Long enough to
# smooth Xet's bursty parallel range-gets, short enough to feel live.
_RATE_WINDOW_S = 8.0
_EMIT_THROTTLE_S = 0.3


def _is_bytes_unit(unit) -> bool:
    """A byte bar reports unit 'B' (often with unit_scale). The 'Fetching N
    files' count bar uses 'it'/'files'/None — everything else is treated as a
    count bar."""
    return isinstance(unit, str) and unit.strip().upper().startswith("B")


class DownloadAggregator:
    """Per-repo byte aggregator. Thread-safe; fed from tqdm + segmented paths."""

    def __init__(
        self,
        repo_id: str,
        *,
        target: str = "local",
        total_bytes: Optional[int] = None,
        files_total: Optional[int] = None,
    ) -> None:
        self.repo_id = repo_id
        self.target = target
        self.total_bytes = total_bytes
        # Byte bars for the files still in flight, keyed by an opaque per-bar
        # key: key -> (downloaded, total). A bar is promoted to
        # ``_completed_bytes`` when it closes and its entry is dropped, so a
        # later bar that reuses the same key cannot overwrite bytes that are
        # already earned (CPython recycles id() values aggressively).
        self._byte_bars: dict[object, tuple[int, Optional[int]]] = {}
        # Bytes credited by byte bars that have already closed, plus the keys
        # that produced them so a repeated close is not counted twice.
        self._completed_bytes = 0
        self._completed_keys: set[object] = set()
        # file-count progress from the "Fetching N files" bar
        self._files_done = 0
        self._files_total = files_total
        # Highest count reported so far, so switching from the byte-bar
        # fallback to the count bar never shows fewer files than before.
        self._files_done_seen = 0
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self._last_emit = 0.0

    # ── feed ──────────────────────────────────────────────────────────────
    def update_byte_bar(self, key: object, downloaded: int, total: Optional[int]) -> None:
        with self._lock:
            if key in self._completed_keys:
                # The bar already closed and its bytes are credited. tqdm's
                # close() calls display(), which reaches this path with the
                # same key; counting it again would double the file.
                return
            prev, prev_total = self._byte_bars.get(key, (0, None))
            # Monotonic per bar: a retried or throttled update must never lower
            # the bytes already reported for this bar.
            self._byte_bars[key] = (max(prev, int(downloaded or 0)), total or prev_total)

    def credit_complete(self, key: object, total: Optional[int]) -> None:
        """A byte bar closed — credit its full size (Xet completion signal).

        The entry is promoted out of ``_byte_bars`` so the key is free for the
        next file without erasing what this one earned. Re-crediting the same
        key is a no-op.
        """
        with self._lock:
            t = int(total or 0)
            prev, prev_total = self._byte_bars.pop(key, (0, None))
            credited = max(prev, t)
            if credited <= 0:
                # Nothing to credit (and no total to remember).
                if prev_total:
                    self._byte_bars[key] = (prev, prev_total)
                return
            if key in self._completed_keys:
                # The same bar closed twice (tqdm can call close() again on
                # teardown). Its bytes are already credited.
                return
            self._completed_keys.add(key)
            self._completed_bytes += credited

    def update_files(self, done: int, total: Optional[int]) -> None:
        with self._lock:
            self._files_done = max(self._files_done, int(done or 0))
            if total:
                self._files_total = int(total)

    def add(self, key: object, delta: int) -> None:
        """Increment a byte bar's downloaded bytes (used by the segmented path)."""
        with self._lock:
            cur, tot = self._byte_bars.get(key, (0, None))
            self._byte_bars[key] = (cur + int(delta or 0), tot)

    # ── derive ────────────────────────────────────────────────────────────
    def _bytes_done_locked(self) -> int:
        total = self._completed_bytes + sum(d for d, _ in self._byte_bars.values())
        # Never report past the preflight total: a retried file can briefly
        # double-count the same bytes, and a >100% bar is worse than a
        # slightly slow one.
        if self.total_bytes:
            return min(total, int(self.total_bytes))
        return total

    def _rate_locked(self, now: float, bytes_done: int) -> float:
        self._samples.append((now, bytes_done))
        cutoff = now - _RATE_WINDOW_S
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if len(self._samples) < 2:
            return 0.0
        t0, b0 = self._samples[0]
        t1, b1 = self._samples[-1]
        dt = t1 - t0
        return (b1 - b0) / dt if dt > 0 else 0.0

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            bytes_done = self._bytes_done_locked()
            rate = self._rate_locked(now, bytes_done)
            total = self.total_bytes
            # files_done: prefer the count bar. Without it, count the files
            # whose bar has closed plus any live bar that reached its total —
            # completed bars leave `_byte_bars`, so they must be counted here
            # or a finished file would never appear.
            files_done = self._files_done
            if not files_done:
                finished_live = sum(
                    1 for d, t in self._byte_bars.values() if t and d >= t
                )
                files_done = len(self._completed_keys) + finished_live
            # The count bar can lag behind the bars that already closed (or
            # report fewer files than finished), so keep the high-water mark:
            # the displayed count must not drop between snapshots.
            if files_done > self._files_done_seen:
                self._files_done_seen = files_done
            files_done = self._files_done_seen
            eta = None
            if rate > 0 and total and total > bytes_done:
                eta = (total - bytes_done) / rate
            return {
                "repo_id": self.repo_id,
                "target": self.target,
                "phase": "aggregate",
                "bytes_done": bytes_done,
                "total_bytes": total,
                "rate": rate,
                "eta_seconds": eta,
                "files_done": files_done,
                "files_total": self._files_total,
            }


# ── registry of active per-repo aggregators ────────────────────────────────
_aggregators: dict[tuple[str, str], DownloadAggregator] = {}
_registry_lock = threading.Lock()
_sink_installed = False


def start(
    repo_id: str,
    *,
    target: str = "local",
    total_bytes: Optional[int] = None,
    files_total: Optional[int] = None,
) -> DownloadAggregator:
    """Begin (or reset) aggregation for a repo. Called by the preflight."""
    agg = DownloadAggregator(
        repo_id, target=target, total_bytes=total_bytes, files_total=files_total
    )
    with _registry_lock:
        _aggregators[(target, repo_id)] = agg
    return agg


def complete(repo_id: str, *, target: str = "local") -> None:
    """Flush a finished download to 100% (FDL-06). Under Xet the per-file byte
    bars never increment `n` or close through our tqdm, so byte-level progress
    is unobservable mid-download; this credits the full preflight total on
    success so the overall bar lands exactly on done. Emits one final
    un-throttled aggregate event."""
    agg = _get(repo_id, target)
    if agg is None:
        return
    with agg._lock:
        if agg.total_bytes:
            # REPLACE all byte bars with one full-total entry so the sum is
            # exactly total. Never add on top: the segmented path already
            # accumulated the real bytes, so adding total again would double it.
            agg._byte_bars = {"__complete__": (int(agg.total_bytes), int(agg.total_bytes))}
            agg._completed_bytes = 0
            agg._completed_keys.clear()
        if agg._files_total:
            agg._files_done = agg._files_total
            agg._files_done_seen = max(agg._files_done_seen, agg._files_total)
        # Clear the rate window: crediting the full size in one step would
        # otherwise compute an absurd instantaneous rate (Δbytes over ~0s).
        agg._samples.clear()
    try:
        snap = agg.snapshot()
        snap["rate"] = 0.0
        snap["eta_seconds"] = 0
        hf_progress.emit(snap)
    except Exception:
        pass


def finish(repo_id: str, *, target: str = "local") -> None:
    with _registry_lock:
        _aggregators.pop((target, repo_id), None)


def _get(repo_id: str, target: str = "local") -> Optional[DownloadAggregator]:
    with _registry_lock:
        return _aggregators.get((target, repo_id))


def feed(repo_id, key, unit, downloaded, total, complete, *, target: Optional[str] = None) -> None:
    """Sink target for a per-bar tqdm update (from utils.hf_progress).

    Distinguishes byte bars (unit 'B') from the file-count bar and routes
    accordingly. A download with no preflight (start() never called) is ignored
    here — the per-file events still flow for the detail view.
    """
    agg = _get(repo_id, target or hf_progress.current_target.get())
    if agg is None:
        return
    if _is_bytes_unit(unit):
        if complete:
            agg.credit_complete(key, total)
        else:
            agg.update_byte_bar(key, downloaded, total)
    else:
        # "Fetching N files" count bar: downloaded=files done, total=files total
        agg.update_files(downloaded, total)
    _maybe_emit(agg)


def add_bytes(
    repo_id: str, key: object, delta: int, *, target: Optional[str] = None
) -> None:
    """Direct byte increment for the opt-in segmented downloader (FDL-08/09)."""
    agg = _get(repo_id, target or hf_progress.current_target.get())
    if agg is None:
        return
    agg.add(key, delta)
    _maybe_emit(agg)


def _maybe_emit(agg: DownloadAggregator) -> None:
    now = time.monotonic()
    if (now - agg._last_emit) < _EMIT_THROTTLE_S:
        return
    agg._last_emit = now
    try:
        hf_progress.emit(agg.snapshot(now))
    except Exception:
        pass


def install() -> None:
    """Wire the per-bar tqdm byte sink so tqdm updates feed the aggregator."""
    global _sink_installed
    if _sink_installed:
        return
    try:
        hf_progress.set_byte_sink(feed)
        _sink_installed = True
    except Exception:
        pass
