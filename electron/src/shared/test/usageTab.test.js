import { describe, it, expect } from 'vitest';
import { fmtDuration, fmtDate } from '../components/settings/UsageTab.jsx';

// Settings → Usage is the local-only alternative to cloud analytics (a PostHog
// integration was rejected in PR #1110). These pin the display helpers; the
// "never leaks content" guarantee is enforced backend-side in
// tests/test_local_stats.py, where the data actually comes from.

describe('fmtDuration', () => {
  it('scales seconds → minutes → hours', () => {
    expect(fmtDuration(0)).toBe('0 s');
    expect(fmtDuration(45)).toBe('45 s');
    expect(fmtDuration(200)).toBe('3 m 20 s');
    expect(fmtDuration(3600)).toBe('1 h 0 m');
    expect(fmtDuration(8040)).toBe('2 h 14 m');
  });

  it('never renders a negative or bogus duration', () => {
    expect(fmtDuration(-10)).toBe('0 s');
    expect(fmtDuration(NaN)).toBe('0 s');
    expect(fmtDuration(undefined)).toBe('0 s');
    expect(fmtDuration(null)).toBe('0 s');
  });
});

describe('fmtDate', () => {
  it('renders a local date for a unix timestamp', () => {
    expect(fmtDate(1783845065)).toBeTruthy();
  });

  it('returns null for a missing timestamp (fresh install)', () => {
    expect(fmtDate(null)).toBeNull();
    expect(fmtDate(undefined)).toBeNull();
    expect(fmtDate(0)).toBeNull();
  });
});
