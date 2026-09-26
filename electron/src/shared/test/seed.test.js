import { describe, it, expect } from 'vitest';
import { pickDesignSeed, MAX_SEED } from '../utils/seed';

// #526: design synth must reuse the pinned seed when "keep this seed" is on,
// and roll a fresh in-range random one otherwise.
describe('pickDesignSeed (#526)', () => {
  it('returns the pinned seed when keepSeed is on and a valid seed is pinned', () => {
    expect(pickDesignSeed(true, 12345, () => 0.5)).toBe(12345);
    expect(pickDesignSeed(true, 0, () => 0.5)).toBe(0); // 0 is a valid seed
  });

  it('rolls a fresh seed when keepSeed is off', () => {
    expect(pickDesignSeed(false, 12345, () => 0)).toBe(0);
    expect(pickDesignSeed(false, 12345, () => 0.999999)).toBe(Math.floor(0.999999 * MAX_SEED));
  });

  it('rolls a fresh seed when keepSeed is on but nothing valid is pinned', () => {
    expect(pickDesignSeed(true, null, () => 0)).toBe(0);
    expect(pickDesignSeed(true, undefined, () => 0)).toBe(0);
    expect(pickDesignSeed(true, 1.5, () => 0)).toBe(0); // non-integer is not a valid seed
  });

  it('keeps generated seeds in the 31-bit range', () => {
    expect(pickDesignSeed(false, null, () => 0)).toBeGreaterThanOrEqual(0);
    expect(pickDesignSeed(false, null, () => 0.9999999999)).toBeLessThanOrEqual(MAX_SEED);
  });
});
