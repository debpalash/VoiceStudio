import { describe, expect, it } from 'vitest';
import { licensedEngine } from './engine-license';

// A license-gated engine must always offer its Accept flow until the backend
// reports it accepted; the gate keys off the structured flags, never off the
// wording of the unavailable reason.
describe('licensedEngine', () => {
  it.each(['supertonic3', 'pockettts'])('offers the license for %s until accepted', (id) => {
    expect(licensedEngine(id, true, false)).toBe(id);
    expect(licensedEngine(id, true, undefined)).toBe(id);
  });

  it('offers nothing once accepted, when not required, or for other engines', () => {
    expect(licensedEngine('supertonic3', true, true)).toBeNull();
    expect(licensedEngine('pockettts', undefined, false)).toBeNull();
    expect(licensedEngine('kokoro', true, false)).toBeNull();
  });
});
