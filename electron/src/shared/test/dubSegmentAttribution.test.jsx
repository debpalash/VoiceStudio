import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAppStore } from '../store';

// #1612 — per-line subtitle management in the dub table.
//
// The reporter's scenario: a couple of words belonging to character A were
// transcribed onto character B's line. The only way to move them is merge the
// two rows and split them again at the right word — and that round-trip used
// to hand BOTH halves the FIRST row's speaker and voice, so B's remaining
// words came out dubbed in A's voice ("some words might end up with the wrong
// character's speech"). These tests pin the round-trip, plus the two
// capabilities the same report asked for: merging upwards, and inserting a
// line that never existed in the transcript.

vi.mock('../api/client', () => ({
  apiPost: vi.fn(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
  API: '',
}));

import useSegmentEditing from '../hooks/useSegmentEditing';

const ANNA = { speaker_id: 'Anna', profile_id: 'voice-anna' };
const BEN = { speaker_id: 'Ben', profile_id: 'voice-ben' };

// "Hello there " is 12 chars of a 20-char merged line spanning 0-4s, so a
// split at the word boundary lands at 2.4s — inside Ben's original span.
const TWO_SPEAKERS = [
  { id: '1', text: 'Hello there', text_original: 'Hello there', start: 0, end: 2, ...ANNA },
  { id: '2', text: 'but wait', text_original: 'but wait', start: 2, end: 4, ...BEN },
];

function setSegments(segs) {
  act(() => useAppStore.getState().setDubSegments(segs));
}

function segments() {
  return useAppStore.getState().dubSegments;
}

beforeEach(() => {
  act(() => useAppStore.getState().setDubSegments([]));
});

describe('merge → split keeps each speaker their own words (#1612)', () => {
  it('gives the second half the speaker who actually says it', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    const merged = segments();
    expect(merged).toHaveLength(1);
    expect(merged[0].text).toBe('Hello there but wait');

    act(() => result.current.segmentSplit(merged[0].id, 12));
    const [left, right] = segments();

    expect(left.text).toBe('Hello there');
    expect(left.speaker_id).toBe('Anna');
    expect(left.profile_id).toBe('voice-anna');

    // The regression: this used to be Anna's voice reading Ben's line.
    expect(right.text).toBe('but wait');
    expect(right.speaker_id).toBe('Ben');
    expect(right.profile_id).toBe('voice-ben');
  });

  it('moves words across the boundary without moving the speaker', () => {
    // "but" belongs to Anna; splitting after it leaves Ben with "wait".
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 16));
    const [left, right] = segments();

    expect(left.text).toBe('Hello there but');
    expect(left.speaker_id).toBe('Anna');
    expect(right.text).toBe('wait');
    expect(right.speaker_id).toBe('Ben');
  });

  it('drops a half-speaker voice rather than inheriting the other one', () => {
    setSegments([
      { id: '1', text: 'Hello there', start: 0, end: 2, ...ANNA },
      { id: '2', text: 'but wait', start: 2, end: 4 }, // no speaker set
    ]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 12));
    const [, right] = segments();

    expect(right.speaker_id).toBeUndefined();
    expect(right.profile_id).toBeUndefined();
  });

  it('leaves an ordinary split untouched — both halves keep the parent', () => {
    setSegments([{ id: '1', text: 'one two', start: 0, end: 2, ...ANNA }]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentSplit('1', 4));
    const [left, right] = segments();

    expect(left.speaker_id).toBe('Anna');
    expect(right.speaker_id).toBe('Anna');
    expect(left.merge_parts).toBeUndefined();
    expect(right.merge_parts).toBeUndefined();
  });

  it('carries every attribution field through an ordinary split', () => {
    // Attribution is now applied wholesale, deleting fields the covering part
    // does not name — so a plain split must still reproduce ALL of them, or
    // splitting a line would quietly discard its direction, gain or language.
    setSegments([
      {
        id: '1',
        text: 'one two',
        start: 0,
        end: 2,
        ...ANNA,
        direction: 'whispered',
        gain: 1.4,
        target_lang: 'it',
      },
    ]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentSplit('1', 4));
    for (const half of segments()) {
      expect(half.speaker_id).toBe('Anna');
      expect(half.profile_id).toBe('voice-anna');
      expect(half.direction).toBe('whispered');
      expect(half.gain).toBe(1.4);
      expect(half.target_lang).toBe('it');
    }
  });

  it("restores the far side's direction and gain, not just its voice", () => {
    setSegments([
      { id: '1', text: 'Hello there', start: 0, end: 2, ...ANNA, direction: 'calm' },
      { id: '2', text: 'but wait', start: 2, end: 4, ...BEN, direction: 'urgent', gain: 0.6 },
    ]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 12));
    const [left, right] = segments();

    expect(left.direction).toBe('calm');
    expect(left.gain).toBeUndefined();
    expect(right.direction).toBe('urgent');
    expect(right.gain).toBe(0.6);
  });

  it('still splits by speaker when the lines OVERLAP in time', () => {
    // Caught by driving the real app: retiming a line so it runs past its
    // neighbour makes two merged parts cover the same instant. A time-based
    // lookup then returns whichever it checked first and gave Ben's words to
    // Anna. Attribution keys off text offsets, which cannot overlap.
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    // Anna's line now ends at 3.5s, well inside Ben's 2–4s slot.
    act(() => result.current.segmentMoveResize('1', { start: 0, end: 3.5 }));
    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 12));
    const [left, right] = segments();

    expect(left.speaker_id).toBe('Anna');
    expect(right.speaker_id).toBe('Ben');
    expect(right.profile_id).toBe('voice-ben');
  });

  it('survives a second merge/split round-trip', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 12));
    act(() => result.current.segmentMerge(segments()[0].id));
    act(() => result.current.segmentSplit(segments()[0].id, 12));
    const [left, right] = segments();

    expect(left.speaker_id).toBe('Anna');
    expect(right.speaker_id).toBe('Ben');
  });

  it('keeps trimmed offsets correct when the right half is split again', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentSplit(segments()[0].id, 5));
    const rightId = segments()[1].id;
    act(() => result.current.segmentSplit(rightId, 6));

    expect(segments()[2].text).toBe('but wait');
    expect(segments()[2].speaker_id).toBe('Ben');
  });

  it.each([
    ['text', 'rewritten line'],
    ['speaker_id', 'Cara'],
    ['profile_id', 'voice-cara'],
  ])('invalidates stored attribution when %s is edited', (field, value) => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    expect(segments()[0].merge_parts).toHaveLength(2);
    act(() => result.current.segmentEditField(segments()[0].id, field, value));

    expect(segments()[0].merge_parts).toBeUndefined();
  });

  it('restores original text with its per-speaker attribution', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1'));
    act(() => result.current.segmentEditField(segments()[0].id, 'text', 'rewritten'));
    act(() => result.current.segmentRestoreOriginal(segments()[0].id));
    act(() => result.current.segmentSplit(segments()[0].id, 12));

    expect(segments()[0].speaker_id).toBe('Anna');
    expect(segments()[1].speaker_id).toBe('Ben');
    expect(segments()[1].profile_id).toBe('voice-ben');
  });
});

describe('merge direction (#1612)', () => {
  it('merges upwards into the previous line', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('2', 'prev'));

    expect(segments()).toHaveLength(1);
    expect(segments()[0].text).toBe('Hello there but wait');
    // Merging upward anchors on the EARLIER row, so the result is Anna's.
    expect(segments()[0].speaker_id).toBe('Anna');
  });

  it('is a no-op on the first row', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('1', 'prev'));
    expect(segments()).toHaveLength(2);
  });

  it('is a no-op on the last row merging down', () => {
    setSegments(TWO_SPEAKERS);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentMerge('2', 'next'));
    expect(segments()).toHaveLength(2);
  });
});

describe('insert a new line (#1612)', () => {
  it('lands in the silent gap and continues the same voice', () => {
    setSegments([
      { id: '1', text: 'Hello', start: 0, end: 2, ...ANNA, target_lang: 'it' },
      { id: '2', text: 'later', start: 6, end: 8, ...BEN },
    ]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentInsert('1'));
    const segs = segments();

    expect(segs).toHaveLength(3);
    expect(segs[1].text).toBe('');
    expect(segs[1].start).toBe(2);
    expect(segs[1].end).toBe(6);
    expect(segs[1].speaker_id).toBe('Anna');
    expect(segs[1].profile_id).toBe('voice-anna');
    expect(segs[1].target_lang).toBe('it');
  });

  it("does not inherit the previous line's directorial note", () => {
    setSegments([{ id: '1', text: 'Hello', start: 0, end: 2, ...ANNA, direction: 'whispered' }]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentInsert('1'));
    expect(segments()[1].direction).toBeUndefined();
  });

  it('gives the new row an id nothing else is using', () => {
    setSegments([
      { id: '1', text: 'a', start: 0, end: 2 },
      { id: '1_new', text: 'b', start: 4, end: 6 },
    ]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentInsert('1'));
    const ids = segments().map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('appends after the last line when there is nothing to sit before', () => {
    setSegments([{ id: '1', text: 'only', start: 0, end: 2 }]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentInsert('1'));
    const segs = segments();
    expect(segs).toHaveLength(2);
    expect(segs[1].start).toBe(2);
    expect(segs[1].end).toBeGreaterThan(2);
  });

  it('is undoable like any other edit', () => {
    setSegments([{ id: '1', text: 'only', start: 0, end: 2 }]);
    const { result } = renderHook(() => useSegmentEditing());

    act(() => result.current.segmentInsert('1'));
    expect(segments()).toHaveLength(2);
    act(() => result.current.undo());
    expect(segments()).toHaveLength(1);
  });
});
