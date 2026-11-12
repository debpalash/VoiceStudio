import { expect, it } from 'vitest';
import {
  SUPERVISE_ACTIVE_MS,
  SUPERVISE_CALM_AFTER,
  SUPERVISE_CALM_MS,
  superviseDelayMs,
} from './supervise-cadence';

it('probes at the active period until the backend has been healthy for a while', () => {
  expect(superviseDelayMs(0)).toBe(SUPERVISE_ACTIVE_MS);
  expect(superviseDelayMs(SUPERVISE_CALM_AFTER - 1)).toBe(SUPERVISE_ACTIVE_MS);
});

it('relaxes after a long healthy streak and returns to the active period on a miss', () => {
  expect(superviseDelayMs(SUPERVISE_CALM_AFTER)).toBe(SUPERVISE_CALM_MS);
  expect(SUPERVISE_CALM_MS).toBeGreaterThan(SUPERVISE_ACTIVE_MS);
  expect(superviseDelayMs(0)).toBe(SUPERVISE_ACTIVE_MS);
});
