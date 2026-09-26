import { describe, expect, it } from 'vitest';
import { visibleMergeAvailability } from '../utils/segmentParts';

describe('DubSegmentTable merge availability', () => {
  it('disables merges when a filter leaves only the middle row visible', () => {
    const segments = [{ id: 'hidden-prev' }, { id: 'visible' }, { id: 'hidden-next' }];

    expect(visibleMergeAvailability(segments, [segments[1]], segments[1])).toEqual({
      canMerge: false,
      canMergePrev: false,
    });
  });

  it('keeps actions for visible neighbors that are adjacent in source order', () => {
    const segments = [{ id: 'prev' }, { id: 'visible' }, { id: 'next' }];

    expect(visibleMergeAvailability(segments, segments, segments[1])).toEqual({
      canMerge: true,
      canMergePrev: true,
    });
  });

  it('does not expose merge-next for the final source row', () => {
    const segments = [{ id: 'prev' }, { id: 'last' }];

    expect(visibleMergeAvailability(segments, segments, segments[1])).toEqual({
      canMerge: false,
      canMergePrev: true,
    });
    expect(visibleMergeAvailability(segments, segments, segments[0]).canMerge).toBe(true);
  });
});
