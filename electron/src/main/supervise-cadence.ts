/** The supervisor's probe period while something is wrong or just changed. */
export const SUPERVISE_ACTIVE_MS = 2000;
/** Probe period once the backend has answered healthily for a while. */
export const SUPERVISE_CALM_MS = 6000;
/** Healthy probes in a row (about a minute at the active period) before relaxing. */
export const SUPERVISE_CALM_AFTER = 30;

/**
 * Delay before the next health probe. A steady, healthy backend is probed less
 * often (idle CPU, #3924); the first miss resets the streak to zero, so every
 * retry and the whole miss-counting window run at the active period again.
 */
export function superviseDelayMs(healthyStreak: number): number {
  return healthyStreak >= SUPERVISE_CALM_AFTER ? SUPERVISE_CALM_MS : SUPERVISE_ACTIVE_MS;
}
