import { describe, expect, test, vi } from 'vitest';
import {
  nextUpdateCheckDelay,
  scheduleUpdateChecks,
  STARTUP_UPDATE_CHECK_DELAY_MS,
  UPDATE_CHECK_INTERVAL_MS,
  type UpdateCheckTimers,
} from './update-schedule';

const HOUR = 60 * 60 * 1_000;

function fakeTimers() {
  const pending: Array<{ callback: () => void; ms: number; id: number }> = [];
  let next = 0;
  const timers: UpdateCheckTimers = {
    setTimeout: (callback, ms) => {
      pending.push({ callback, ms, id: (next += 1) });
      return next;
    },
    clearTimeout: (id) => {
      const index = pending.findIndex((timer) => timer.id === id);
      if (index >= 0) pending.splice(index, 1);
    },
  };
  /** Fires the one pending timer and returns its delay. */
  const fire = async () => {
    expect(pending).toHaveLength(1);
    const timer = pending.shift()!;
    timer.callback();
    await vi.waitFor(() => expect(pending.length).toBeGreaterThan(0));
    return timer.ms;
  };
  return { timers, pending, fire };
}

describe('update check schedule', () => {
  test('backs off 1 h, 2 h, 4 h, then 6 h after failures and resets after a success', async () => {
    const results = [false, false, false, false, false, true, false, true];
    const check = vi.fn(async () => results.shift() ?? true);
    const { timers, pending, fire } = fakeTimers();
    scheduleUpdateChecks(check, { timers });
    expect(pending[0].ms).toBe(STARTUP_UPDATE_CHECK_DELAY_MS);

    const delays: number[] = [];
    for (let run = 0; run < 8; run += 1) {
      await fire();
      delays.push(pending[0].ms);
    }
    expect(delays).toEqual([
      HOUR,
      2 * HOUR,
      4 * HOUR,
      6 * HOUR,
      6 * HOUR,
      6 * HOUR,
      HOUR,
      6 * HOUR,
    ]);
    expect(check).toHaveBeenCalledTimes(8);
  });

  test('a rejected check counts as a failure', async () => {
    const { timers, pending, fire } = fakeTimers();
    scheduleUpdateChecks(vi.fn().mockRejectedValue(new Error('boom')), { timers });
    await fire();
    expect(pending[0].ms).toBe(HOUR);
  });

  test('stops cleanly, including while a check is running', async () => {
    let finish!: (value: boolean) => void;
    const { timers, pending } = fakeTimers();
    const stop = scheduleUpdateChecks(() => new Promise<boolean>((resolve) => (finish = resolve)), {
      timers,
    });
    pending.shift()!.callback();
    stop();
    finish(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(pending).toHaveLength(0);

    const idle = fakeTimers();
    scheduleUpdateChecks(
      vi.fn(async () => true),
      { timers: idle.timers },
    )();
    expect(idle.pending).toHaveLength(0);
  });

  test('never waits longer than the normal interval', () => {
    expect(nextUpdateCheckDelay(0)).toBe(UPDATE_CHECK_INTERVAL_MS);
    expect(nextUpdateCheckDelay(1_000)).toBe(UPDATE_CHECK_INTERVAL_MS);
  });
});
