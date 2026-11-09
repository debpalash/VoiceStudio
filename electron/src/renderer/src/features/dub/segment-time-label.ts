import { formatTimestamp } from '@shared/utils/timeFormat';

/** Readable start time for a transcript row: "1:00.0", never "60.0s". */
export function segmentTimeLabel(seconds: number): string {
  return Number.isFinite(seconds) ? formatTimestamp(seconds, { decimals: 1, hours: true }) : '?';
}
