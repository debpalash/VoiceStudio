import { describe, it, expect } from 'vitest';
import {
  nextCastColor,
  effectiveProfile,
  effectiveSpeed,
  castMember,
  CAST_COLORS,
} from './storyCast';

describe('nextCastColor', () => {
  it('returns the first unused palette color', () => {
    expect(nextCastColor([{ color: CAST_COLORS[0] }])).toBe(CAST_COLORS[1]);
  });
  it('returns the first color for an empty cast', () => {
    expect(nextCastColor([])).toBe(CAST_COLORS[0]);
  });
});

describe('effectiveProfile', () => {
  const cast = [
    { id: 'narrator', profileId: 'narr' },
    { id: 'fox', profileId: 'foxv' },
    { id: 'owl', profileId: null },
  ];
  it('prefers a per-line override', () => {
    expect(effectiveProfile({ character: 'fox', profileId: 'override' }, cast)).toBe('override');
  });
  it('falls back to the cast voice', () => {
    expect(effectiveProfile({ character: 'fox', profileId: null }, cast)).toBe('foxv');
  });
  it('returns null when neither is set', () => {
    expect(effectiveProfile({ character: 'owl', profileId: null }, cast)).toBeNull();
  });
});

describe('effectiveSpeed', () => {
  it('prefers a per-line speed override over the global', () => {
    expect(effectiveSpeed({ speed: 1.25 }, 0.7)).toBe(1.25);
  });
  it('applies the global speed when the track has none (#508)', () => {
    // Regression: preview + stem export must follow the global, not 1.0.
    expect(effectiveSpeed({ speed: null }, 0.7)).toBe(0.7);
    expect(effectiveSpeed({}, 0.7)).toBe(0.7);
  });
  it('treats a global of 1 as at-rest → null (engine default)', () => {
    expect(effectiveSpeed({ speed: null }, 1)).toBeNull();
  });
  it('returns null when neither override nor a non-default global is set', () => {
    expect(effectiveSpeed({ speed: null }, null)).toBeNull();
    expect(effectiveSpeed({}, undefined)).toBeNull();
  });
});

describe('castMember', () => {
  it('finds by id, else first', () => {
    const cast = [{ id: 'a' }, { id: 'b' }];
    expect(castMember(cast, 'b').id).toBe('b');
    expect(castMember(cast, 'zzz').id).toBe('a');
    expect(castMember([], 'x')).toBeNull();
  });
});
