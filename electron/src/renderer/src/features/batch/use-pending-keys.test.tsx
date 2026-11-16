import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { usePendingKeys } from './use-pending-keys';

it('ignores repeat runs per key while pending and clears on failure', async () => {
  const { result } = renderHook(() => usePendingKeys());
  let release!: () => void;
  let calls = 0;
  const slow = () => {
    calls++;
    return new Promise<void>((resolve) => (release = resolve));
  };
  let first!: Promise<void>;
  act(() => {
    first = result.current.run('job:es', slow);
    void result.current.run('job:es', slow);
  });
  expect(calls).toBe(1);
  expect(result.current.pending.has('job:es')).toBe(true);
  expect(result.current.pending.has('job:fr')).toBe(false);
  await act(async () => {
    release();
    await first;
  });
  expect(result.current.pending.has('job:es')).toBe(false);
  await act(async () => {
    await result.current.run('job:es', () => Promise.reject(new Error('x'))).catch(() => {});
  });
  expect(result.current.pending.size).toBe(0);
});
