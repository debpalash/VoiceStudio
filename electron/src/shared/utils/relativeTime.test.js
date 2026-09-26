import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { toMillis, timeAgo, absoluteTime } from './relativeTime';

// Regression suite for the "20617d ago" bug class: backend rows store Unix
// SECONDS (time.time()); formatters that assumed epoch MILLISECONDS rendered
// every record as ~1970 ("20617d ago" ≈ 56.5 years) or an epoch date.

// Frozen "now": 2026-07-04T12:00:00Z.
const NOW_MS = Date.UTC(2026, 6, 4, 12, 0, 0);
const NOW_S = NOW_MS / 1000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW_MS);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('toMillis', () => {
  it('converts Unix seconds (backend time.time() floats) to ms', () => {
    expect(toMillis(NOW_S - 7200)).toBe(NOW_MS - 7200 * 1000);
    expect(toMillis(1751600000.123)).toBe(1751600000123);
  });

  it('passes epoch milliseconds through unchanged', () => {
    expect(toMillis(NOW_MS)).toBe(NOW_MS);
    expect(toMillis(1751600000123)).toBe(1751600000123);
  });

  it('parses ISO strings', () => {
    expect(toMillis('2026-07-04T11:00:00.000Z')).toBe(NOW_MS - 3600 * 1000);
  });

  it('treats numeric strings as Unix stamps, not dates', () => {
    expect(toMillis(String(NOW_S))).toBe(NOW_MS);
    expect(toMillis(String(NOW_MS))).toBe(NOW_MS);
  });

  it('accepts Date instances', () => {
    expect(toMillis(new Date(NOW_MS))).toBe(NOW_MS);
    expect(toMillis(new Date('garbage'))).toBeNull();
  });

  it('returns null for missing/degenerate values', () => {
    for (const v of [null, undefined, 0, '', '   ', NaN, Infinity, -5, 'not a date']) {
      expect(toMillis(v)).toBeNull();
    }
  });
});

describe('timeAgo', () => {
  it('EPOCH REGRESSION: a seconds timestamp from today never renders as thousands of days ago', () => {
    // Pre-fix, 2h-ago-in-seconds fed to a ms diff → "20617d ago" (1970).
    const label = timeAgo(NOW_S - 7200);
    expect(label).toBe('2h ago');
    expect(label).not.toMatch(/\d{3,}d ago/);
  });

  it('renders identical output for seconds and milliseconds inputs', () => {
    expect(timeAgo(NOW_S - 90)).toBe('1m ago');
    expect(timeAgo(NOW_MS - 90 * 1000)).toBe('1m ago');
    expect(timeAgo(NOW_S - 3 * 86400)).toBe('3d ago');
    expect(timeAgo(NOW_MS - 3 * 86400 * 1000)).toBe('3d ago');
  });

  it('parses ISO strings', () => {
    expect(timeAgo('2026-07-04T11:59:30.000Z')).toBe('30s ago');
  });

  it('renders a dash for null/0/undefined — never an epoch age', () => {
    expect(timeAgo(null)).toBe('—');
    expect(timeAgo(0)).toBe('—');
    expect(timeAgo(undefined)).toBe('—');
    expect(timeAgo('')).toBe('—');
  });

  it('treats future stamps within clock skew (<1 min ahead) as "just now"', () => {
    expect(timeAgo(NOW_S + 30)).toBe('just now');
    expect(timeAgo(NOW_MS + 59 * 1000)).toBe('just now');
  });

  it('falls back to an absolute date beyond 7 days (and for far-future stamps)', () => {
    expect(timeAgo(NOW_S - 30 * 86400)).not.toMatch(/ago/);
    expect(timeAgo(NOW_S + 3600)).not.toMatch(/ago/);
  });
});

describe('absoluteTime', () => {
  it('formats any unit and is empty for missing values (no "Jan 1, 1970" tooltips)', () => {
    expect(absoluteTime(NOW_S)).toBe(new Date(NOW_MS).toLocaleString());
    expect(absoluteTime(NOW_MS)).toBe(new Date(NOW_MS).toLocaleString());
    expect(absoluteTime(null)).toBe('');
    expect(absoluteTime(0)).toBe('');
  });
});
