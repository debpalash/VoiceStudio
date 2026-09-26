/**
 * Shared, unit-tolerant timestamp normalization + relative-time formatting.
 *
 * Root cause of the "20617d ago" bug class: the backend stores timestamps as
 * Unix SECONDS (`time.time()` → REAL columns: generation_history.created_at,
 * dub_history, exports, longform jobs, projects…), while frontend-local
 * records carry MILLISECONDS (`Date.now()` — story projects) or ISO strings
 * (transcriptions). Any formatter that assumes one unit renders the other as
 * ~1970 ("20617d ago") or an epoch date ("Jan 1, 1970"). Every timestamp
 * render must funnel through toMillis() so no individual view can regress.
 *
 * Do NOT change the backend's stored format — existing user DBs hold seconds.
 */

// Numeric timestamps below this are seconds, at/above it milliseconds.
// 1e12 ms = Sep 2001; 1e12 s = year 33658 — unambiguous for real data.
const MS_THRESHOLD = 1e12;

const SHORT_DATE_OPTS = { month: 'short', day: 'numeric' };

/**
 * Normalize any timestamp shape to epoch milliseconds, or null when missing/
 * unparseable. Accepts: Unix seconds (float), epoch ms, numeric strings of
 * either, ISO/date strings, Date instances. 0 / null / undefined / '' are
 * treated as "missing" (0 is this codebase's missing-timestamp sentinel —
 * never a real 1970 record).
 */
export function toMillis(ts) {
  if (ts == null || ts === 0 || ts === '') return null;
  if (ts instanceof Date) {
    const t = ts.getTime();
    return Number.isNaN(t) ? null : t;
  }
  if (typeof ts === 'string') {
    const trimmed = ts.trim();
    if (!trimmed) return null;
    // Numeric strings ("1751600000", "1751600000000.5") are Unix stamps,
    // not date strings — Date.parse would reject or misread them.
    const asNum = Number(trimmed);
    if (Number.isFinite(asNum)) return toMillis(asNum);
    const parsed = Date.parse(trimmed);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) return null;
  return Math.round(ts < MS_THRESHOLD ? ts * 1000 : ts);
}

// Future stamps within this window are clock skew, not data corruption.
const CLOCK_SKEW_MS = 60_000;

/**
 * Relative-time label for record timestamps (any unit — see toMillis).
 *  - missing/unparseable → "—" (never "20617d ago" / epoch dates)
 *  - future within 1 min (clock skew) → "just now"
 *  - <60s → "Ns ago"; <60m → "Nm ago"; <24h → "Nh ago"; <7d → "Nd ago"
 *  - older (or far-future, i.e. bad clock) → short absolute date
 */
export function timeAgo(ts) {
  const ms = toMillis(ts);
  if (ms == null) return '—';
  const diff = Date.now() - ms;
  if (diff < -CLOCK_SKEW_MS) return shortDate(ms);
  if (diff < 0) return 'just now';
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return shortDate(ms);
}

/** Absolute host-locale datetime for tooltips; '' when missing/unparseable. */
export function absoluteTime(ts) {
  const ms = toMillis(ts);
  return ms == null ? '' : new Date(ms).toLocaleString();
}

function shortDate(ms) {
  const d = new Date(ms);
  const opts =
    d.getFullYear() === new Date().getFullYear()
      ? SHORT_DATE_OPTS
      : { ...SHORT_DATE_OPTS, year: 'numeric' };
  return d.toLocaleDateString([], opts);
}
