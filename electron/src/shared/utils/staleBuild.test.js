import { describe, it, expect } from 'vitest';

import { isOutdated, parseVersionTriple } from './staleBuild';

describe('parseVersionTriple', () => {
  it.each([
    ['0.5.0', [0, 5, 0]],
    ['v0.5.0', [0, 5, 0]],
    ['0.5.1-3', [0, 5, 1]], // preview build stamp
    ['1.2.3-rc.1', [1, 2, 3]],
    [' v0.4.2 ', [0, 4, 2]],
  ])('parses %s', (raw, expected) => {
    expect(parseVersionTriple(raw)).toEqual(expected);
  });

  it.each([
    ['unknown'],
    [''],
    [null],
    [undefined],
    ['0.5'],
    ['abc'],
    ['v.1.2.3'],
    // Anchored: trailing data that isn't a semver -/+ suffix is rejected,
    // never silently read as the leading triple.
    ['1.2.3.4'],
    ['1.2.3garbage'],
  ])('rejects %s', (raw) => {
    expect(parseVersionTriple(raw)).toBeNull();
  });
});

describe('isOutdated', () => {
  it.each([
    // The historical case: 0.3.7-era builds filing against a 0.5.0 world.
    ['0.3.7', '0.5.0', true],
    ['0.3.7', 'v0.5.0', true],
    ['0.5.0', '0.5.0', false],
    ['0.5.0', '0.5.1', true],
    // Numeric, not lexicographic: .10 > .9
    ['0.5.9', '0.5.10', true],
    ['0.10.0', '0.9.9', false],
    ['1.0.0', '0.9.9', false],
    // Preview builds stamp latest+1-N — never "outdated" vs the stable tag.
    ['0.5.1-2', '0.5.0', false],
  ])('%s vs latest %s → %s', (current, latest, expected) => {
    expect(isOutdated(current, latest)).toBe(expected);
  });

  it.each([
    ['unknown', '0.5.0'],
    ['', '0.5.0'],
    ['0.5.0', ''],
    ['0.5.0', null],
    [null, null],
  ])('never flags unparseable pair (%s, %s)', (current, latest) => {
    expect(isOutdated(current, latest)).toBe(false);
  });
});
