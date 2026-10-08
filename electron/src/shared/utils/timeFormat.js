// Clock-style timestamp formatting.
//
// Round the *total* to the display precision first, then split it into
// minutes/seconds. Splitting first and rounding the seconds remainder
// afterwards renders 59.96 as "0:60.0" (and Math.round(59.6 % 60) as "60s").

/**
 * Format seconds as `m:ss.d` (or `h:mm:ss.d` with `hours: true`).
 * `decimals` is the number of fractional digits (0 omits the point).
 * Non-finite or negative input renders as zero.
 */
export function formatTimestamp(seconds, { decimals = 1, hours = false } = {}) {
  const scale = 10 ** decimals;
  const units = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * scale) : 0;
  const whole = Math.floor(units / scale);
  const fraction = units % scale;
  const sec = String(whole % 60).padStart(2, '0');
  const tail = decimals > 0 ? `.${String(fraction).padStart(decimals, '0')}` : '';
  const totalMinutes = Math.floor(whole / 60);
  if (hours && totalMinutes >= 60) {
    const mm = String(totalMinutes % 60).padStart(2, '0');
    return `${Math.floor(totalMinutes / 60)}:${mm}:${sec}${tail}`;
  }
  return `${totalMinutes}:${sec}${tail}`;
}

/**
 * Split whole-second rounding into `{ minutes, seconds }` with seconds in
 * 0..59, rounding the total first so seconds never reach 60.
 */
export function splitRoundedMinutes(seconds) {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
  return { minutes: Math.floor(total / 60), seconds: total % 60 };
}
