import { expect, it } from 'vitest';
import { segmentTimeLabel } from './segment-time-label';

it('formats start times as clock time', () => {
  expect(segmentTimeLabel(0)).toBe('0:00.0');
  expect(segmentTimeLabel(59.96)).toBe('1:00.0');
  expect(segmentTimeLabel(60)).toBe('1:00.0');
  expect(segmentTimeLabel(3725.5)).toBe('1:02:05.5');
});

it('shows a placeholder for an unknown start', () => {
  expect(segmentTimeLabel(Number.NaN)).toBe('?');
});
