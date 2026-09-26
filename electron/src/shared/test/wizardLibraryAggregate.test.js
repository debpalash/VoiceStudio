import { describe, it, expect } from 'vitest';
import { aggregate, progressFromAgg, fmtBytes, fmtRate } from '../components/WizardLibrary.jsx';

describe('progressFromAgg — backend authoritative aggregate event', () => {
  it('computes pct + remaining + rate + ETA from real totals (not the buggy per-file sum)', () => {
    // 8% of 2.4 GB must show ~2.2 GB left — NOT "0.0 MB left" (#657 follow-up bug).
    const TOTAL = 2.4 * 1024 * 1024 * 1024; // 2.4 GiB, matching the "2.4 GB" label
    const agg = {
      bytesDone: 0.08 * TOTAL,
      totalBytes: TOTAL,
      rate: 5.2 * 1024 * 1024, // 5.2 MB/s
      etaSeconds: 405,
    };
    const r = progressFromAgg(agg);
    expect(r.pct).toBe(8);
    expect(r.remaining).toBeCloseTo(0.92 * TOTAL, 0);
    expect(fmtBytes(r.remaining)).toBe('2.2 GB'); // not 0.0 MB
    expect(fmtRate(r.rate)).toBe('5.2 MB/s'); // not 1 KB/s
    expect(r.etaSec).toBe(405);
  });

  it('returns null until totals are known (so the row falls back / shows downloading…)', () => {
    expect(progressFromAgg(null)).toBeNull();
    expect(progressFromAgg({ bytesDone: 100, totalBytes: 0 })).toBeNull();
    expect(progressFromAgg({ bytesDone: 100 })).toBeNull();
  });

  it('caps pct at 100 and treats no-remaining as null', () => {
    const r = progressFromAgg({
      bytesDone: 2_576_980_377,
      totalBytes: 2_576_980_377,
      rate: 0,
      etaSeconds: null,
    });
    expect(r.pct).toBe(100);
    expect(r.remaining).toBeNull();
    expect(r.etaSec).toBeNull();
  });
});

describe('aggregate — download telemetry from SSE file events', () => {
  it('sums bytes, computes pct, remaining, rate and ETA', () => {
    const files = {
      'a.bin': { downloaded: 500, total: 1000, rate: 100 },
      'b.bin': { downloaded: 250, total: 1000, rate: 150 },
    };
    const { pct, remaining, rate, etaSec } = aggregate(files);
    expect(pct).toBe(38); // 750 / 2000
    expect(remaining).toBe(1250); // 2000 - 750
    expect(rate).toBe(250); // both still downloading
    expect(etaSec).toBeCloseTo(5, 5); // 1250 / 250
  });

  it('drops rate from already-complete files (no negative/idle ETA)', () => {
    const files = {
      done: { downloaded: 1000, total: 1000, rate: 999 }, // complete → rate ignored
      live: { downloaded: 0, total: 1000, rate: 200 },
    };
    const { rate, remaining, etaSec } = aggregate(files);
    expect(rate).toBe(200);
    expect(remaining).toBe(1000);
    expect(etaSec).toBeCloseTo(5, 5);
  });

  it('returns nulls before any totals arrive (degrades to "downloading…")', () => {
    expect(aggregate({}).pct).toBeNull();
    expect(aggregate({}).remaining).toBeNull();
    expect(aggregate(undefined).pct).toBeNull();
  });
});

describe('fmtBytes / fmtRate', () => {
  it('formats remaining size in MB/GB', () => {
    expect(fmtBytes(700 * 1024 * 1024)).toBe('700 MB');
    expect(fmtBytes(1.5 * 1024 * 1024 * 1024)).toBe('1.5 GB');
    expect(fmtBytes(0)).toBe('');
    expect(fmtBytes(null)).toBe('');
  });

  it('formats rate in MB/s or KB/s, blank when idle', () => {
    expect(fmtRate(5.2 * 1024 * 1024)).toBe('5.2 MB/s');
    expect(fmtRate(512 * 1024)).toBe('512 KB/s');
    expect(fmtRate(0)).toBe('');
  });
});
