/** Seconds as the segment Start/End fields display them. */
export const formatSegmentTime = (seconds: number) => seconds.toFixed(2);

/**
 * Read a Start/End field on commit. Returns null when nothing should be
 * committed: the field still shows the current time (it displays hundredths,
 * so committing it untouched would round the segment), or it is blank or not
 * a number.
 */
export function parseSegmentTimeField(value: string, current: number): number | null {
  const text = value.trim();
  if (!text || text === formatSegmentTime(current)) return null;
  const seconds = Number(text);
  return Number.isFinite(seconds) ? seconds : null;
}
