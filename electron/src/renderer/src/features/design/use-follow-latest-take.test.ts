import { renderHook } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { HistoryItem } from '@/lib/api/types';

const state = vi.hoisted(() => ({
  open: null as unknown,
  latest: null as string | null,
  history: [] as unknown[],
  openTake: vi.fn(),
}));
vi.mock('@/lib/store/takes', () => ({
  openTake: state.openTake,
  useSelectedTake: () => state.open,
}));
vi.mock('@/lib/store/output', () => ({
  useLatestOutput: () => ({ result: state.latest ? { id: state.latest } : null }),
}));
vi.mock('@/hooks/use-history', () => ({ useHistory: () => ({ data: state.history }) }));

import { nextOpenTake, useFollowLatestTake } from './use-follow-latest-take';

const take = (id: string, mode = 'design') => ({ id, mode, language: 'German' }) as HistoryItem;

beforeEach(() => {
  state.open = take('old');
  state.latest = 'earlier-session';
  state.history = [take('old'), take('earlier-session')];
  state.openTake.mockClear();
});

it('moves an open take pane to a newer take of the same kind only', () => {
  expect(nextOpenTake(take('old'), 'new', [take('new')])).toEqual(take('new'));
  expect(nextOpenTake(take('old'), 'new', [take('new', 'clone')])).toBeNull();
  expect(nextOpenTake(null, 'new', [take('new')])).toBeNull();
  expect(nextOpenTake(take('old'), 'old', [take('old')])).toBeNull();
});

it('leaves a take the user opened alone until a new render lands, then shows it', () => {
  const { rerender } = renderHook(() => useFollowLatestTake());
  // An output from before the page mounted never replaces the opened take.
  expect(state.openTake).not.toHaveBeenCalled();
  state.latest = 'new';
  rerender();
  // The history has not caught up yet: wait instead of guessing.
  expect(state.openTake).not.toHaveBeenCalled();
  state.history = [take('new'), ...state.history];
  rerender();
  expect(state.openTake).toHaveBeenCalledWith(take('new'));
});

it('drops the follow when the user opens another take before the history catches up', () => {
  const { rerender } = renderHook(() => useFollowLatestTake());
  state.latest = 'new';
  rerender();
  state.open = take('chosen');
  rerender();
  state.history = [take('new'), take('chosen'), ...state.history];
  rerender();
  expect(state.openTake).not.toHaveBeenCalled();
});

it('keeps a take chosen in the same update as a new render', () => {
  const { rerender } = renderHook(() => useFollowLatestTake());
  state.open = take('chosen');
  state.latest = 'new';
  rerender();
  state.history = [take('new'), take('chosen'), ...state.history];
  rerender();
  expect(state.openTake).not.toHaveBeenCalled();
});
