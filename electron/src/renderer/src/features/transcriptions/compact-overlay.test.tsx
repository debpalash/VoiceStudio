import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CompactOverlay } from './compact-overlay';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
});
afterEach(() => {
  vi.useRealTimers();
});

function setup(live = true) {
  let emit: (level: number) => void = () => {};
  const unsubscribe = vi.fn();
  const onLevel = vi.fn((listener: (level: number) => void) => {
    emit = listener;
    return unsubscribe;
  });
  const view = render(<CompactOverlay onLevel={onLevel} startedAt={Date.now()} live={live} />);
  return { view, emit: (level: number) => emit(level), onLevel, unsubscribe };
}

const heights = () =>
  screen.getAllByTestId('compact-bar').map((bar) => Number.parseInt(bar.style.height, 10));

it('shows only bars and a timer, with no transcript or controls', () => {
  setup();
  expect(screen.getAllByTestId('compact-bar').length).toBeGreaterThan(0);
  expect(screen.getByRole('timer').textContent).toBe('0:00');
  expect(screen.queryByRole('button')).toBeNull();
});

it('drives bars from real input levels and settles during a pause', () => {
  const { emit } = setup();
  act(() => emit(0.9));
  act(() => void vi.advanceTimersByTime(60));
  expect(Math.max(...heights())).toBeGreaterThan(80);

  // Silence: the meter decays back to its resting height while the timer keeps going.
  act(() => emit(0));
  act(() => void vi.advanceTimersByTime(60 * 30));
  expect(Math.max(...heights())).toBe(12);
  expect(screen.getByRole('timer').textContent).toBe('0:01');
});

it('freezes the timer and stops listening once recording ends', () => {
  const { view, unsubscribe } = setup();
  act(() => void vi.advanceTimersByTime(3000));
  expect(screen.getByRole('timer').textContent).toBe('0:03');
  view.rerender(
    <CompactOverlay
      onLevel={vi.fn(() => unsubscribe)}
      startedAt={Date.now() - 3000}
      live={false}
    />,
  );
  act(() => void vi.advanceTimersByTime(5000));
  expect(screen.getByRole('timer').textContent).toBe('0:03');
  expect(unsubscribe).toHaveBeenCalled();
});

it('uses the theme colour for the bars', () => {
  setup();
  expect(screen.getAllByTestId('compact-bar')[0].className).toContain('bg-primary');
});
