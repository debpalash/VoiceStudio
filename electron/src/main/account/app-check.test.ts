import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppCheck } from './app-check';

const tick = async (ms: number) => {
  await vi.advanceTimersByTimeAsync(ms);
};

function setup(platform = 'linux') {
  const state = { active: true };
  const appCheck = vi.fn<(b: unknown) => Promise<number | null>>(async () => 600);
  const check = new AppCheck({
    api: { appCheck },
    version: '1.2.3',
    platform,
    isActive: () => state.active,
  });
  return { state, appCheck, check };
}

describe('AppCheck', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('sends the body shortly after start and reschedules from the answer', async () => {
    const { appCheck, check } = setup('darwin');
    check.reconcile();
    expect(appCheck).not.toHaveBeenCalled();
    await tick(5000);
    expect(appCheck).toHaveBeenCalledTimes(1);
    expect(appCheck).toHaveBeenCalledWith({ app: 'oss', app_version: '1.2.3', platform: 'macos' });
    await tick(599_000);
    expect(appCheck).toHaveBeenCalledTimes(1);
    await tick(1000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    check.stop();
  });

  it('maps platforms and skips unknown ones', async () => {
    const win = setup('win32');
    win.check.reconcile();
    await tick(5000);
    expect(win.appCheck).toHaveBeenCalledWith(expect.objectContaining({ platform: 'windows' }));
    win.check.stop();
    const other = setup('freebsd');
    other.check.reconcile();
    await tick(10_000);
    expect(other.appCheck).not.toHaveBeenCalled();
  });

  it('clamps the interval to 60..21600 seconds', async () => {
    const { appCheck, check } = setup();
    appCheck.mockResolvedValueOnce(5);
    check.reconcile();
    await tick(5000);
    await tick(59_000);
    expect(appCheck).toHaveBeenCalledTimes(1);
    appCheck.mockResolvedValueOnce(999_999);
    await tick(1000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    await tick(21_599_000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    await tick(1000);
    expect(appCheck).toHaveBeenCalledTimes(3);
    check.stop();
  });

  it('stops on sign-out or when switched off, and restarts on sign-in', async () => {
    const { state, appCheck, check } = setup();
    check.reconcile();
    await tick(5000);
    state.active = false;
    check.reconcile();
    await tick(3_600_000);
    expect(appCheck).toHaveBeenCalledTimes(1);
    state.active = true;
    check.reconcile();
    await tick(5000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    check.stop();
  });

  it('does not reschedule when switched off during a request', async () => {
    const { state, appCheck, check } = setup();
    appCheck.mockImplementationOnce(async () => {
      state.active = false;
      return 60;
    });
    check.reconcile();
    await tick(5000);
    await tick(3_600_000);
    expect(appCheck).toHaveBeenCalledTimes(1);
  });

  it('backs off on errors: last interval, and 6 hours after a 404', async () => {
    const { appCheck, check } = setup();
    appCheck.mockResolvedValueOnce(120);
    check.reconcile();
    await tick(5000);
    appCheck.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 503 }));
    await tick(120_000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    appCheck.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 404 }));
    await tick(120_000);
    expect(appCheck).toHaveBeenCalledTimes(3);
    await tick(21_599_000);
    expect(appCheck).toHaveBeenCalledTimes(3);
    await tick(1000);
    expect(appCheck).toHaveBeenCalledTimes(4);
    check.stop();
  });

  it('uses 300 seconds by default and after a network error', async () => {
    const { appCheck, check } = setup();
    appCheck.mockRejectedValueOnce(new Error('offline'));
    check.reconcile();
    await tick(5000);
    await tick(299_000);
    expect(appCheck).toHaveBeenCalledTimes(1);
    await tick(1000);
    expect(appCheck).toHaveBeenCalledTimes(2);
    check.stop();
  });

  it('stop clears the timer for good', async () => {
    const { appCheck, check } = setup();
    check.reconcile();
    check.stop();
    check.reconcile();
    await tick(3_600_000);
    expect(appCheck).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
