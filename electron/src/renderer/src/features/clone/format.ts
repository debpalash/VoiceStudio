const DIVISIONS: { amount: number; unit: Intl.RelativeTimeFormatUnit }[] = [
  { amount: 60, unit: 'second' },
  { amount: 60, unit: 'minute' },
  { amount: 24, unit: 'hour' },
  { amount: 7, unit: 'day' },
  { amount: 4.34524, unit: 'week' },
  { amount: 12, unit: 'month' },
  { amount: Number.POSITIVE_INFINITY, unit: 'year' },
];

/** "3 minutes ago" style, via Intl so it follows the UI locale. */
/** Accepts the backend's REAL epoch seconds (voice_profiles / history rows) as
 *  well as ISO strings, so a row-shape change can't take the sidebar down. */
export function formatRelative(
  stamp: number | string | null | undefined,
  locale: string,
  now = Date.now(),
): string {
  if (stamp === null || stamp === undefined || stamp === '') return '';
  const then =
    typeof stamp === 'number'
      ? stamp * (stamp < 1e12 ? 1000 : 1)
      : Date.parse(stamp.endsWith('Z') || /[+-]\d\d:\d\d$/.test(stamp) ? stamp : `${stamp}Z`);
  if (Number.isNaN(then)) return '';
  let delta = (then - now) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  for (const { amount, unit } of DIVISIONS) {
    const rounded = Math.round(delta);
    if (Math.abs(delta) < amount && Math.abs(rounded) < amount) return rtf.format(rounded, unit);
    delta /= amount;
  }
  return '';
}

/**
 * Drop leading `[laughter]`-style control tokens from a take's text: they are
 * synthesis instructions, not content, and they eat the two visible lines.
 */
export function displayTitle(text: string): string {
  const stripped = text.replace(/^(\s*\[[^\]]{1,30}\]\s*)+/, '').trim();
  return stripped || text;
}

export function formatSeconds(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}
