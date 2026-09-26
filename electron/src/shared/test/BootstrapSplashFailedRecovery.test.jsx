/**
 * Regression tests for issue #1156 — the "Setup failed" card never
 * auto-dismissed when the backend later became healthy.
 *
 * fail-before/pass-after: `failed` was a fully terminal stage — the IPC poll
 * loop stopped AND the successful IPC reply disarmed the #879 HTTP watchdog,
 * so neither path could ever observe the backend coming back (supervisor
 * restart, completed dependency repair). Users had to F5/relaunch by hand.
 * The failed stage now runs a plain-HTTP health poll that flips to 'ready'
 * the moment the backend answers.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBootstrapStage } from '../components/BootstrapSplash';
import { startHealthRecoveryPoll } from '../utils/splashWatchdog';

const invokeMock = vi.fn();

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock('@tauri-apps/plugin-opener', () => ({
  revealItemInDir: vi.fn(),
}));

let warnSpy;

beforeEach(() => {
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  invokeMock.mockReset();
  vi.useFakeTimers();
  window.__TAURI_INTERNALS__ = {};
  vi.stubEnv('DEV', false);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  warnSpy.mockRestore();
  delete window.__TAURI_INTERNALS__;
});

describe('useBootstrapStage — #1156 failed-stage auto-recovery', () => {
  it("stage 'failed' + backend later healthy over HTTP → auto-recovers to ready", async () => {
    invokeMock.mockImplementation(async () => ({
      stage: 'failed',
      message: 'Backend died early: exit code 1',
    }));
    let healthy = false;
    const fetchMock = vi.fn(async () => ({ ok: healthy }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {}); // flush dynamic imports + first tick
    expect(result.current.stage).toBe('failed');

    // Backend stays dead: the card must stay up (no false dismissal).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(result.current.stage).toBe('failed');

    // Backend comes back (supervisor restart / repair finished) → dismiss.
    healthy = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.stage).toBe('ready');
  });

  it("stage 'failed' with a permanently dead backend stays failed", async () => {
    invokeMock.mockImplementation(async () => ({
      stage: 'failed',
      message: 'Backend died early: exit code 1',
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false })),
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(result.current.stage).toBe('failed');
  });
});

describe('startHealthRecoveryPoll', () => {
  it('fires onHealthy exactly once when /health starts answering, and cancel() stops it', async () => {
    let healthy = false;
    const onHealthy = vi.fn();
    const fetchFn = vi.fn(async () => ({ ok: healthy }));

    startHealthRecoveryPoll({ healthUrl: 'http://x/health', onHealthy, fetchFn });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(onHealthy).not.toHaveBeenCalled();

    healthy = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(onHealthy).toHaveBeenCalledTimes(1);

    const onHealthy2 = vi.fn();
    const poll = startHealthRecoveryPoll({
      healthUrl: 'http://x/health',
      onHealthy: onHealthy2,
      fetchFn: vi.fn(async () => ({ ok: false })),
    });
    poll.cancel();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(onHealthy2).not.toHaveBeenCalled();
  });
});
