import { expect, it } from 'vitest';
import { openingViewport } from './workflow-fit';

it('fits a wide workflow into view without zooming past natural size', () => {
  const view = openingViewport({ x: 40, y: 80, width: 2400, height: 300 }, 1000, 700);
  expect(view.zoom).toBeLessThan(1);
  // Every step is inside the canvas horizontally.
  expect(view.x + 40 * view.zoom).toBeGreaterThanOrEqual(0);
  expect(view.x + (40 + 2400) * view.zoom).toBeLessThanOrEqual(1000);
});

it('starts a short workflow near the top instead of the vertical middle', () => {
  const view = openingViewport({ x: 40, y: 80, width: 600, height: 150 }, 1100, 1400);
  expect(view.zoom).toBe(1);
  expect(view.y + 80 * view.zoom).toBe(40);
});

it('keeps a tall workflow fully in view', () => {
  const view = openingViewport({ x: 0, y: 0, width: 400, height: 3000 }, 1000, 700);
  expect(view.y).toBeGreaterThanOrEqual(0);
  expect(view.y + 3000 * view.zoom).toBeLessThanOrEqual(700);
});
