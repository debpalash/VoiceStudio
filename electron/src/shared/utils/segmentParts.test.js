import { describe, it, expect } from 'vitest';
import {
  applyAttribution,
  attributionAt,
  attributionOf,
  clipParts,
  insertionSlot,
  keepParts,
  mergedParts,
  nextSegmentId,
  partsFor,
} from './segmentParts';

// Attribution lives in TEXT-OFFSET space, so the fixtures carry text; the
// timestamps are incidental and deliberately OVERLAP, which is what broke the
// original time-keyed lookup (#1612).
const A = {
  id: 'a',
  text: 'Hello there',
  start: 0,
  end: 3.5,
  speaker_id: 'Anna',
  profile_id: 'voice-anna',
};
const B = {
  id: 'b',
  text: 'but wait',
  start: 2,
  end: 4,
  speaker_id: 'Ben',
  profile_id: 'voice-ben',
};

describe('attributionOf', () => {
  it('collects only the fields the segment actually sets', () => {
    expect(attributionOf(A)).toEqual({ speaker_id: 'Anna', profile_id: 'voice-anna' });
  });

  it('ignores null as firmly as undefined — neither names a speaker', () => {
    expect(attributionOf({ speaker_id: null, profile_id: 'v', gain: 0 })).toEqual({
      profile_id: 'v',
      gain: 0,
    });
  });
});

describe('applyAttribution', () => {
  it('deletes fields the new attribution does not carry', () => {
    const out = applyAttribution({ ...A, direction: 'whispered' }, { speaker_id: 'Ben' });
    expect(out.speaker_id).toBe('Ben');
    // The whole point: a half must not keep the other speaker's voice.
    expect('profile_id' in out).toBe(false);
    expect('direction' in out).toBe(false);
  });

  it('leaves non-attribution fields alone', () => {
    const out = applyAttribution({ ...A, text: 'keep me' }, { speaker_id: 'Ben' });
    expect(out.text).toBe('keep me');
    expect(out.id).toBe('a');
  });
});

describe('partsFor', () => {
  it('synthesises a single spanning part for an un-merged segment', () => {
    expect(partsFor(A)).toEqual([
      { textStart: 0, textEnd: 11, speaker_id: 'Anna', profile_id: 'voice-anna' },
    ]);
  });

  it('prefers a recorded merge_parts', () => {
    const recorded = [{ textStart: 0, textEnd: 1, speaker_id: 'X' }];
    expect(partsFor({ ...A, merge_parts: recorded })).toEqual(recorded);
  });

  it('discards holes in a recorded merge_parts', () => {
    expect(partsFor({ ...A, merge_parts: [null, { textStart: 0, textEnd: 1 }] })).toEqual([
      { textStart: 0, textEnd: 1 },
    ]);
  });

  it('falls back when merge_parts is present but empty', () => {
    expect(partsFor({ ...A, merge_parts: [] })).toHaveLength(1);
  });
});

describe('mergedParts', () => {
  it('records both sides in order', () => {
    expect(mergedParts(A, B).map((p) => p.speaker_id)).toEqual(['Anna', 'Ben']);
  });

  it('flattens instead of nesting when a merged row is merged again', () => {
    const merged = { ...A, text: 'Hello there but wait', merge_parts: mergedParts(A, B) };
    const C = { id: 'c', text: 'and one more', start: 4, end: 6, speaker_id: 'Cara' };
    expect(mergedParts(merged, C).map((p) => p.speaker_id)).toEqual(['Anna', 'Ben', 'Cara']);
  });
});

describe('attributionAt', () => {
  // 'Hello there' is offsets 0-10; 'but wait' starts at 12 after the join.
  const parts = mergedParts(A, B);

  it('picks the part covering the text offset', () => {
    expect(attributionAt(parts, 3).speaker_id).toBe('Anna');
    expect(attributionAt(parts, 14).speaker_id).toBe('Ben');
  });

  it('is unaffected by the two lines overlapping in TIME', () => {
    // A runs 0-3.5s and B runs 2-4s, so both cover t=3. Keyed by time this
    // returned Anna for words that are plainly Ben's — the live-app failure.
    expect(attributionAt(parts, 12).speaker_id).toBe('Ben');
  });

  it('treats a part boundary as belonging to the later part', () => {
    expect(attributionAt(parts, 11).speaker_id).toBe('Ben');
  });

  it('clamps outside the recorded span rather than returning nothing', () => {
    expect(attributionAt(parts, -5).speaker_id).toBe('Anna');
    expect(attributionAt(parts, 999).speaker_id).toBe('Ben');
  });

  it('returns an empty attribution for no parts', () => {
    expect(attributionAt([], 1)).toEqual({});
  });
});

describe('clipParts / keepParts', () => {
  it('clips to the requested window and rebases onto the slice', () => {
    expect(clipParts(mergedParts(A, B), 0, 14)).toEqual([
      { textStart: 0, textEnd: 11, speaker_id: 'Anna', profile_id: 'voice-anna' },
      { textStart: 12, textEnd: 14, speaker_id: 'Ben', profile_id: 'voice-ben' },
    ]);
  });

  it('rebases a right-hand slice to start at zero', () => {
    expect(clipParts(mergedParts(A, B), 12, 20)).toEqual([
      { textStart: 0, textEnd: 8, speaker_id: 'Ben', profile_id: 'voice-ben' },
    ]);
  });

  it('drops zero-width overlaps', () => {
    expect(clipParts(mergedParts(A, B), 11, 11)).toEqual([]);
  });

  it('keepParts discards a record that says nothing the segment does not', () => {
    expect(keepParts([{ textStart: 0, textEnd: 1 }])).toBeUndefined();
    expect(keepParts([])).toBeUndefined();
    expect(keepParts(mergedParts(A, B))).toHaveLength(2);
  });
});

describe('nextSegmentId', () => {
  it('derives a readable id from the anchor', () => {
    expect(nextSegmentId(['a', 'b'], 'a')).toBe('a_new');
  });

  it('keeps counting until it finds a free one', () => {
    expect(nextSegmentId(['a', 'a_new', 'a_new2'], 'a')).toBe('a_new3');
  });

  it('compares as strings so numeric ids collide correctly', () => {
    expect(nextSegmentId([1, 2], 1)).toBe('1_new');
  });
});

describe('insertionSlot', () => {
  it('fills the silent gap before the next line', () => {
    expect(insertionSlot({ end: 2 }, { start: 5 })).toEqual({ start: 2, end: 5 });
  });

  it('falls back to a default slot when the gap is too small to use', () => {
    expect(insertionSlot({ end: 2 }, { start: 2.1 }, { defaultDur: 2 })).toEqual({
      start: 2,
      end: 4,
    });
  });

  it('uses the default slot after the last line', () => {
    expect(insertionSlot({ end: 7 }, undefined, { defaultDur: 1.5 })).toEqual({
      start: 7,
      end: 8.5,
    });
  });
});
