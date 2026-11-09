/** Normal gap between background update checks. */
export const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1_000;
/** First retry after a failed check; it doubles up to the normal interval. */
export const UPDATE_CHECK_RETRY_MS = 60 * 60 * 1_000;
export const STARTUP_UPDATE_CHECK_DELAY_MS = 10_000;

export interface UpdateCheckTimers {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const systemTimers: UpdateCheckTimers = {
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** 6 h after a success; 1 h, 2 h, 4 h, then 6 h after consecutive failures. */
export function nextUpdateCheckDelay(consecutiveFailures: number): number {
  if (consecutiveFailures <= 0) return UPDATE_CHECK_INTERVAL_MS;
  return Math.min(
    UPDATE_CHECK_INTERVAL_MS,
    UPDATE_CHECK_RETRY_MS * 2 ** Math.min(consecutiveFailures - 1, 8),
  );
}

/**
 * Runs `check` shortly after launch, then again after each one finishes. A
 * failed or rejected check comes back sooner, so an outage does not leave the
 * app without an update check for six hours. Returns a stop function.
 */
export function scheduleUpdateChecks(
  check: () => Promise<boolean>,
  {
    startupDelayMs = STARTUP_UPDATE_CHECK_DELAY_MS,
    timers = systemTimers,
  }: { startupDelayMs?: number; timers?: UpdateCheckTimers } = {},
): () => void {
  let failures = 0;
  let stopped = false;
  let handle: unknown;
  const run = async () => {
    let succeeded = false;
    try {
      succeeded = await check();
    } catch {
      // The caller records the failure; here it only shortens the next wait.
    }
    if (stopped) return;
    failures = succeeded ? 0 : failures + 1;
    handle = timers.setTimeout(() => void run(), nextUpdateCheckDelay(failures));
  };
  handle = timers.setTimeout(() => void run(), startupDelayMs);
  return () => {
    stopped = true;
    timers.clearTimeout(handle);
  };
}
