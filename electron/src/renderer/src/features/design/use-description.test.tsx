import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import { useDescription } from './use-description';
vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn() }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});
it('manual edits supersede both queued and already-running descriptions', async () => {
  vi.useFakeTimers();
  const apply = vi.fn();
  let resolve!: (value: unknown) => void;
  vi.mocked(apiJson).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result } = renderHook(() => useDescription(apply));
  act(() => {
    result.current.describe('female');
    result.current.cancel();
  });
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(apiJson).not.toHaveBeenCalled();
  act(() => result.current.describe('male'));
  await act(() => vi.advanceTimersByTimeAsync(500));
  act(() => result.current.cancel());
  await act(async () => resolve({ attrs: { Gender: 'male' }, matched: ['male'], unmatched: [] }));
  expect(apply).not.toHaveBeenCalled();
  expect(result.current.pending).toBe(false);
});
it('newest description wins even if an old request ignores cancellation', async () => {
  vi.useFakeTimers();
  const apply = vi.fn();
  const resolves: ((value: unknown) => void)[] = [];
  vi.mocked(apiJson).mockImplementation(
    () =>
      new Promise((done) => {
        resolves.push(done);
      }),
  );
  const { result } = renderHook(() => useDescription(apply));
  act(() => result.current.describe('old'));
  await act(() => vi.advanceTimersByTimeAsync(500));
  act(() => result.current.describe('new'));
  await act(() => vi.advanceTimersByTimeAsync(500));
  await act(async () =>
    resolves[1]({ attrs: { Age: 'elderly' }, matched: ['elderly'], unmatched: ['raspy'] }),
  );
  await act(async () =>
    resolves[0]({ attrs: { Age: 'child' }, matched: ['child'], unmatched: [] }),
  );
  expect(apply).toHaveBeenCalledExactlyOnceWith({ Age: 'elderly' }, 'new');
  expect(result.current.unmatched).toEqual(['raspy']);
});
