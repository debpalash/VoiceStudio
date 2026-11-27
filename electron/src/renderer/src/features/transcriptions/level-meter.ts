export const METER_BARS = 9;
const FLOOR = 0.008;
const CEILING = 0.25;

/** Maps one RMS reading (0..1 samples) onto a perceptual 0..1 bar height. */
export function rmsToLevel(rms: number): number {
  if (!Number.isFinite(rms) || rms <= FLOOR) return 0;
  const clamped = Math.min(rms, CEILING);
  // Log scale between floor and ceiling: quiet speech still moves the bars.
  return Math.log(clamped / FLOOR) / Math.log(CEILING / FLOOR);
}

export function frameRms(frame: ArrayLike<number>): number {
  if (!frame.length) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

/**
 * One meter tick. The newest bar jumps to the input (fast attack) or falls
 * from the previous newest bar (slow release), so pauses settle to nothing
 * instead of freezing or flickering.
 */
export function nextBars(bars: readonly number[], level: number): number[] {
  const last = bars[bars.length - 1] ?? 0;
  const head = level >= last ? level : Math.max(level, last * 0.7);
  return [...bars.slice(1), head < 0.02 ? 0 : head];
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const seconds = String(total % 60).padStart(2, '0');
  return `${Math.floor(total / 60)}:${seconds}`;
}
