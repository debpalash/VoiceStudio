/**
 * Long-form render status: progress, time left and stall detection (#3417).
 *
 * The backend reports every finished chunk as a `progress` event carrying the
 * book-wide fraction, an ETA from the rolling rate of freshly synthesized
 * chunks, the realtime factor and the mean chunk time. This module turns the
 * last of those into what the tracker shows. Older backends send no
 * `progress` events; the chapter-count estimate is kept for them.
 */
import type { AudiobookRenderChapter } from './longform-session';

export interface RenderProgressSnapshot {
  /** Share of the book rendered, 0..1. */
  fraction: number;
  /** Backend estimate of seconds left when the event arrived. */
  etaS: number | null;
  /** Audio seconds per wall second. */
  rtf: number | null;
  /** Mean wall seconds per freshly synthesized chunk. */
  chunkS: number | null;
  /** `Date.now()` when the event arrived. */
  receivedAt: number;
}

/** Silence tolerated before the first chunk is timed (covers a cold model load). */
export const STALL_COLD_S = 600;
/** Never call a render stalled sooner than this. */
export const STALL_FLOOR_S = 120;
/** A stall is this many typical chunks without any progress. */
export const STALL_FACTOR = 6;

const num = (value: unknown): number | null => {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

export function parseProgressEvent(
  event: Record<string, unknown>,
  now: number,
): RenderProgressSnapshot | null {
  const fraction = num(event.fraction);
  if (fraction == null) return null;
  const etaS = num(event.eta_s);
  const rtf = num(event.rtf);
  const chunkS = num(event.chunk_s);
  return {
    fraction: Math.min(1, Math.max(0, fraction)),
    etaS: etaS != null && etaS >= 0 ? etaS : null,
    rtf: rtf != null && rtf > 0 ? rtf : null,
    chunkS: chunkS != null && chunkS > 0 ? chunkS : null,
    receivedAt: now,
  };
}

export function stallThresholdS(chunkS: number | null): number {
  return chunkS == null ? STALL_COLD_S : Math.max(STALL_FLOOR_S, STALL_FACTOR * chunkS);
}

export interface RenderStatusInput {
  stage: string;
  chapters: AudiobookRenderChapter[];
  progress: RenderProgressSnapshot | null;
  startedAt: number | null;
  lastActivityAt: number | null;
  now: number;
}

export interface RenderStatus {
  percent: number;
  elapsedS: number;
  /** Seconds left, or null while there is nothing to estimate from. */
  etaS: number | null;
  rtf: number | null;
  /** Seconds without progress once that counts as stalled, else null. */
  stalledForS: number | null;
}

const FINISHED = new Set(['done', 'cached', 'failed']);

export function renderStatus({
  stage,
  chapters,
  progress,
  startedAt,
  lastActivityAt,
  now,
}: RenderStatusInput): RenderStatus {
  const elapsedS = startedAt == null ? 0 : Math.max(0, (now - startedAt) / 1000);
  const total = chapters.length;
  const finished = chapters.filter((chapter) => FINISHED.has(chapter.status)).length;
  let percent: number;
  let etaS: number | null = null;
  if (progress) {
    percent = progress.fraction * 100;
    if (progress.etaS != null)
      etaS = Math.max(0, progress.etaS - (now - progress.receivedAt) / 1000);
  } else {
    // Older backend: chapter count only. Chapters served from cache took no
    // time, so they must not make the remaining ones look instant.
    percent = total ? (finished / total) * 100 : 0;
    const timed = chapters.filter((chapter) => chapter.status === 'done').length;
    const left = total - finished;
    if (timed > 0 && left > 0) etaS = (elapsedS / timed) * left;
  }
  let stalledForS: number | null = null;
  // An older backend sends no chunk events, so silence there is not a stall.
  if (stage === 'rendering' && progress != null && lastActivityAt != null) {
    const idle = Math.max(0, (now - lastActivityAt) / 1000);
    if (idle > stallThresholdS(progress?.chunkS ?? null)) stalledForS = idle;
  }
  return {
    percent: Math.min(100, Math.max(0, percent)),
    elapsedS,
    etaS,
    rtf: progress?.rtf ?? null,
    stalledForS,
  };
}

/** `m:ss`, or `h:mm:ss` from an hour up — a book can run all night. */
export function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = String(safe % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}
