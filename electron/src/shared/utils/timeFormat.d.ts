export function formatTimestamp(
  seconds: number,
  options?: { decimals?: number; hours?: boolean },
): string;
export function splitRoundedMinutes(seconds: number): { minutes: number; seconds: number };
