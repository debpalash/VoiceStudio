import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { startVisibleInterval, useVisibleInterval } from './visible-interval';

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility('visible');
});
afterEach(() => {
  vi.useRealTimers();
});

it('ticks while visible, stops while hidden, and catches up on show', () => {
  const cb = vi.fn();
  const dispose = startVisibleInterval(cb, 1000);
  vi.advanceTimersByTime(3000);
  expect(cb).toHaveBeenCalledTimes(3);
  setVisibility('hidden');
  expect(vi.getTimerCount()).toBe(0);
  vi.advanceTimersByTime(60_000);
  expect(cb).toHaveBeenCalledTimes(3);
  setVisibility('visible');
  expect(cb).toHaveBeenCalledTimes(4);
  vi.advanceTimersByTime(1000);
  expect(cb).toHaveBeenCalledTimes(5);
  dispose();
  expect(vi.getTimerCount()).toBe(0);
});

it('does not start a timer when mounted hidden', () => {
  setVisibility('hidden');
  const dispose = startVisibleInterval(vi.fn(), 1000);
  expect(vi.getTimerCount()).toBe(0);
  dispose();
});

it('hook honours active and cleans up', () => {
  const cb = vi.fn();
  const { rerender, unmount } = renderHook(({ on }) => useVisibleInterval(cb, 500, on), {
    initialProps: { on: false },
  });
  expect(vi.getTimerCount()).toBe(0);
  rerender({ on: true });
  act(() => void vi.advanceTimersByTime(1000));
  expect(cb).toHaveBeenCalledTimes(2);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it('stays stopped when the catch-up callback disposes of it', () => {
  let dispose = () => {};
  const cb = vi.fn(() => dispose());
  setVisibility('hidden');
  dispose = startVisibleInterval(cb, 1000);
  setVisibility('visible');
  expect(cb).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});
