/**
 * Unit tests for the IPC-independent splash watchdog (issue #879).
 *
 * The class under test is the escape hatch for a dead Tauri IPC layer
 * (corrupted WebView2 cache after a BSOD): no bootstrap events, `invoke()`
 * hanging forever, backend perfectly healthy over plain HTTP.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  startSplashWatchdog,
  IPC_SILENCE_MS,
  RECOVERY_AFTER_MS,
  HEALTH_POLL_MS,
} from '../utils/splashWatchdog';

const HEALTH_URL = 'http://127.0.0.1:3900/health';

let warnSpy;

beforeEach(() => {
  vi.useFakeTimers();
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  warnSpy.mockRestore();
});

function make(fetchImpl, overrides = {}) {
  const onReadyViaHttp = vi.fn();
  const onStuck = vi.fn();
  const fetchFn = vi.fn(fetchImpl);
  const wd = startSplashWatchdog({
    healthUrl: HEALTH_URL,
    onReadyViaHttp,
    onStuck,
    fetchFn,
    ...overrides,
  });
  return { wd, onReadyViaHttp, onStuck, fetchFn };
}

describe('startSplashWatchdog (#879)', () => {
  it('no IPC signal + healthy backend → proceeds via HTTP with a console.warn breadcrumb', async () => {
    const { onReadyViaHttp, onStuck, fetchFn } = make(async () => ({ ok: true }));

    // Before the silence window: nothing happens.
    await vi.advanceTimersByTimeAsync(IPC_SILENCE_MS - 1);
    expect(fetchFn).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith(HEALTH_URL, expect.anything());
    expect(onReadyViaHttp).toHaveBeenCalledTimes(1);
    expect(onStuck).not.toHaveBeenCalled();

    // Breadcrumbs for auto bug reports: the fallback + the detected
    // "IPC silent but HTTP works" combination.
    const warned = warnSpy.mock.calls.map((c) => c.join(' ')).join('\n');
    expect(warned).toMatch(/No Tauri IPC signal within 10s/);
    expect(warned).toMatch(/healthy over plain HTTP but no Tauri IPC signal/);
    expect(warned).toMatch(/issue #879/);

    // Fully disarmed afterwards: no more polls, no stuck panel.
    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS * 2);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(onStuck).not.toHaveBeenCalled();
    expect(onReadyViaHttp).toHaveBeenCalledTimes(1);
  });

  it('no IPC + dead backend → onStuck at the recovery window, then still proceeds when the backend comes up', async () => {
    let healthy = false;
    const { onReadyViaHttp, onStuck, fetchFn } = make(async () => {
      if (!healthy) throw new TypeError('Failed to fetch');
      return { ok: true };
    });

    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS - 1);
    expect(onStuck).not.toHaveBeenCalled();
    expect(fetchFn).toHaveBeenCalled(); // polling started at the silence mark

    await vi.advanceTimersByTimeAsync(1);
    expect(onStuck).toHaveBeenCalledTimes(1);
    expect(onReadyViaHttp).not.toHaveBeenCalled();

    // Recovery panel showing is NOT terminal: a slow first-run install with
    // broken IPC must still reach the app once the backend answers.
    healthy = true;
    await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    expect(onReadyViaHttp).toHaveBeenCalledTimes(1);
    expect(onStuck).toHaveBeenCalledTimes(1); // never re-fired
  });

  it('non-2xx /health responses count as unhealthy', async () => {
    const { onReadyViaHttp, onStuck } = make(async () => ({ ok: false, status: 503 }));
    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS);
    expect(onReadyViaHttp).not.toHaveBeenCalled();
    expect(onStuck).toHaveBeenCalledTimes(1);
  });

  it('markIpcAlive before the silence window → never polls, never warns, never fires', async () => {
    const { wd, onReadyViaHttp, onStuck, fetchFn } = make(async () => ({ ok: true }));

    await vi.advanceTimersByTimeAsync(3_000);
    wd.markIpcAlive();
    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS * 3);

    expect(fetchFn).not.toHaveBeenCalled();
    expect(onReadyViaHttp).not.toHaveBeenCalled();
    expect(onStuck).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('a late IPC signal after HTTP polling started still disarms everything', async () => {
    const { wd, onReadyViaHttp, onStuck, fetchFn } = make(async () => {
      throw new TypeError('Failed to fetch');
    });

    await vi.advanceTimersByTimeAsync(IPC_SILENCE_MS + HEALTH_POLL_MS);
    const callsSoFar = fetchFn.mock.calls.length;
    expect(callsSoFar).toBeGreaterThan(0);

    wd.markIpcAlive(); // IPC thawed — normal path owns the transition now
    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS * 2);

    expect(fetchFn).toHaveBeenCalledTimes(callsSoFar);
    expect(onStuck).not.toHaveBeenCalled();
    expect(onReadyViaHttp).not.toHaveBeenCalled();
  });

  it('cancel() stops timers and polling (unmount path)', async () => {
    const { wd, onReadyViaHttp, onStuck, fetchFn } = make(async () => ({ ok: true }));
    wd.cancel();
    await vi.advanceTimersByTimeAsync(RECOVERY_AFTER_MS * 2);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(onReadyViaHttp).not.toHaveBeenCalled();
    expect(onStuck).not.toHaveBeenCalled();
  });
});

// #1112: an Intel-Mac install can never be retried into working (PyTorch ships
// no macOS x86_64 wheels), so the splash must not offer a Retry that re-fails
// identically — the "clicking the buttons does nothing" dead end.
describe('isUnrecoverableFailure (#1112)', () => {
  it('flags the Intel-Mac failure as unrecoverable', async () => {
    const { isUnrecoverableFailure } = await import('../components/BootstrapSplash.jsx');
    expect(
      isUnrecoverableFailure(
        "Intel Macs can't run the local AI backend — PyTorch no longer ships…",
      ),
    ).toBe(true);
  });

  it('leaves ordinary failures retryable', async () => {
    const { isUnrecoverableFailure } = await import('../components/BootstrapSplash.jsx');
    expect(isUnrecoverableFailure('uv sync failed: connection timed out')).toBe(false);
    expect(isUnrecoverableFailure('Backend process exited (never started)')).toBe(false);
    expect(isUnrecoverableFailure('')).toBe(false);
  });
});
