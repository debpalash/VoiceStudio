import { describe, expect, it } from 'vitest';
import { engineTrimsReference, referenceUsageNote } from './reference-usage';

const omnivoice = { max_ref_seconds: 20, ref_strategy: 'best_window' } as const;
const voxcpm = { max_ref_seconds: 30, ref_strategy: 'head' } as const;
const unverified = { max_ref_seconds: null, ref_strategy: null };

describe('referenceUsageNote', () => {
  it('stays quiet inside the 5–15 s recommendation', () => {
    expect(referenceUsageNote(omnivoice, 12)).toBeNull();
    expect(referenceUsageNote(omnivoice, null)).toBeNull();
  });

  it('names the passage an OmniVoice-style engine picks from a long clip', () => {
    expect(referenceUsageNote(omnivoice, 25)).toEqual({
      kind: 'best_window',
      seconds: 20,
    });
  });

  it('names the head an engine keeps', () => {
    expect(referenceUsageNote(voxcpm, 45)).toEqual({ kind: 'head', seconds: 30 });
  });

  it('makes no engine claim when the clip fits or the engine is unverified', () => {
    expect(referenceUsageNote(omnivoice, 18)).toEqual({ kind: 'long', seconds: 15 });
    expect(referenceUsageNote(unverified, 60)).toEqual({ kind: 'long', seconds: 15 });
    expect(referenceUsageNote(null, 60)).toEqual({ kind: 'long', seconds: 15 });
  });
});

describe('engineTrimsReference', () => {
  it('is true only past a verified engine limit', () => {
    expect(engineTrimsReference(omnivoice, 20)).toBe(false);
    expect(engineTrimsReference(omnivoice, 20.5)).toBe(true);
    expect(engineTrimsReference(voxcpm, 31)).toBe(true);
    expect(engineTrimsReference(unverified, 70)).toBe(false);
    expect(engineTrimsReference(omnivoice, null)).toBe(false);
  });
});
