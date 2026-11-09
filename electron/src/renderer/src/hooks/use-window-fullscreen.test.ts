import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useWindowFullscreen } from './use-window-fullscreen';

afterEach(() => {
  Reflect.deleteProperty(window, 'voicestudio');
});

it('follows native fullscreen changes and stops listening on unmount', async () => {
  let emit: (value: boolean) => void = () => {};
  const off = vi.fn();
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: {
      app: {
        isFullScreen: () => Promise.resolve(false),
        onFullScreen: (callback: (value: boolean) => void) => {
          emit = callback;
          return off;
        },
      },
    },
  });
  const { result, unmount } = renderHook(() => useWindowFullscreen());
  expect(result.current).toBe(false);
  await act(async () => emit(true));
  expect(result.current).toBe(true);
  unmount();
  expect(off).toHaveBeenCalled();
});

it('stays false without a bridge', () => {
  expect(renderHook(() => useWindowFullscreen()).result.current).toBe(false);
});
