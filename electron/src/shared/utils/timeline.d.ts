export const MIN_SEG_DUR: number;
export const MAX_OVERLAP: number;
export const SNAP_PX: number;

export interface TimelineSegment {
  id: string | number;
  start: number;
  end: number;
  speed?: number;
  original_duration?: number;
}

export function clampSegmentEdit(
  segments: TimelineSegment[],
  index: number,
  mode: 'start' | 'end' | 'move',
  proposed: { start: number; end: number },
  options?: { allowOverlap?: boolean; duration?: number },
): { start: number; end: number };
export function commitMoveResize<T extends TimelineSegment>(
  segment: T,
  timing: { start: number; end: number },
): T;
export function detectOverlaps(segments: TimelineSegment[], epsilon?: number): Set<string>;
export function snapCandidates(options: {
  onsets?: number[];
  prevEnd?: number;
  nextStart?: number;
  playhead?: number;
  pxPerSec: number;
  t: number;
}): number[];
export function snapTime(
  time: number,
  candidates: number[],
  thresholdSeconds: number,
): { time: number; candidate: number | null };
export function nearestOnset(time: number, onsets: number[]): number | null;
