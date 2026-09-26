import { describe, it, expect } from 'vitest';
import { reorder } from './storyReorder';

const list = [{ id: 1 }, { id: 2 }, { id: 3 }];

describe('reorder', () => {
  it('moves an item to before the target id', () => {
    expect(reorder(list, 3, 1).map((x) => x.id)).toEqual([3, 1, 2]);
  });
  it('is a no-op when from === to', () => {
    expect(reorder(list, 2, 2).map((x) => x.id)).toEqual([1, 2, 3]);
  });
  it('returns the same order when an id is missing', () => {
    expect(reorder(list, 9, 1).map((x) => x.id)).toEqual([1, 2, 3]);
  });
});
