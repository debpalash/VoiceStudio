import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseScriptToSpans, roundHalfToEven } from '../utils/longformParser';

// Load the SAME golden corpus the Python suite asserts against. A divergence
// between the two ports fails one of the two suites against this shared truth.
// vitest runs with cwd = frontend/, so the repo-root fixture is one level up.
// (import.meta.url isn't a file: URL under the vitest transform, so use cwd.)
const CASES = JSON.parse(
  readFileSync(resolve(process.cwd(), '../tests/fixtures/longform_parser_cases.json'), 'utf-8'),
);

describe('parseScriptToSpans — cross-impl golden corpus', () => {
  it('has the same ≥40-case corpus as Python', () => {
    expect(CASES.length).toBeGreaterThanOrEqual(40);
  });

  it.each(CASES.map((c) => [c.name, c]))('%s', (_name, c) => {
    const got = parseScriptToSpans(c.input, {
      defaultVoice: c.default_voice,
      defaultSpeed: c.default_speed ?? null,
    });
    expect(got).toEqual(c.expected);
  });
});

describe('parseScriptToSpans — guards & rounding', () => {
  it('returns [] for empty / undefined input', () => {
    expect(parseScriptToSpans('')).toEqual([]);
    expect(parseScriptToSpans(undefined)).toEqual([]);
  });

  it('rounds half-to-even like Python (not Math.round half-up)', () => {
    expect(roundHalfToEven(0.5)).toBe(0);
    expect(roundHalfToEven(1.5)).toBe(2);
    expect(roundHalfToEven(2.5)).toBe(2);
    expect(roundHalfToEven(3.5)).toBe(4);
    expect(roundHalfToEven(0.4)).toBe(0);
    expect(roundHalfToEven(0.6)).toBe(1);
  });

  it('is linear on pathological input (ReDoS guard)', () => {
    for (const blob of [
      '[slow]'.repeat(5000),
      '[pause'.repeat(5000),
      '[voice:'.repeat(5000),
      '# \n'.repeat(5000),
      '[a]'.repeat(5000),
    ]) {
      const t0 = Date.now();
      parseScriptToSpans(blob, { defaultVoice: 'v' });
      expect(Date.now() - t0).toBeLessThan(1000);
    }
  });
});
