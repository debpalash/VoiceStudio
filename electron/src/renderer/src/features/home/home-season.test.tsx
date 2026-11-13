import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PixelSnow, currentHomeSeason, homeSeason } from './home-season';

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('marks winter from December to February and Christmas week inside it', () => {
  expect(homeSeason(new Date(2026, 11, 19))).toBe('winter');
  expect(homeSeason(new Date(2026, 11, 20))).toBe('christmas');
  expect(homeSeason(new Date(2026, 11, 26))).toBe('christmas');
  expect(homeSeason(new Date(2026, 11, 27))).toBe('winter');
  expect(homeSeason(new Date(2027, 0, 15))).toBe('winter');
  expect(homeSeason(new Date(2027, 1, 28))).toBe('winter');
  expect(homeSeason(new Date(2027, 2, 1))).toBeNull();
  expect(homeSeason(new Date(2026, 10, 13))).toBeNull();
});

it('draws snow, then stops animating when unmounted', () => {
  const fillRect = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    fillRect,
    fillStyle: '',
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(180);
  const cancel = vi.spyOn(window, 'cancelAnimationFrame');
  const { unmount } = render(<PixelSnow />);
  expect(fillRect).toHaveBeenCalled();
  unmount();
  expect(cancel).toHaveBeenCalled();
});

it('repaints the snow in the new tint when the theme switches', async () => {
  const fillRect = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    fillRect,
    fillStyle: '',
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(180);
  render(<PixelSnow />);
  const before = fillRect.mock.calls.length;
  document.documentElement.classList.toggle('dark');
  await new Promise((resolve) => setTimeout(resolve, 0));
  document.documentElement.classList.toggle('dark');
  expect(fillRect.mock.calls.length).toBeGreaterThan(before);
});

it('stops and restarts the snow when the reduced-motion setting changes', () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    fillStyle: '',
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D);
  let listener: ((event: MediaQueryListEvent) => void) | undefined;
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener: (_: string, next: (event: MediaQueryListEvent) => void) => {
      listener = next;
    },
    removeEventListener: vi.fn(),
  }));
  const request = vi.spyOn(window, 'requestAnimationFrame');
  const cancel = vi.spyOn(window, 'cancelAnimationFrame');
  render(<PixelSnow />);
  expect(request).toHaveBeenCalledTimes(1);
  listener?.({ matches: true } as MediaQueryListEvent);
  expect(cancel).toHaveBeenCalled();
  listener?.({ matches: false } as MediaQueryListEvent);
  expect(request).toHaveBeenCalledTimes(2);
});

it('keeps Home working where the canvas has no 2D context', () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  expect(() => render(<PixelSnow />)).not.toThrow();
});

it('lets a local preview choose the season', () => {
  localStorage.setItem('voicestudio.home.season', 'christmas');
  expect(currentHomeSeason()).toBe('christmas');
  localStorage.setItem('voicestudio.home.season', 'summer');
  expect(currentHomeSeason()).toBe(homeSeason());
});
