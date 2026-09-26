/**
 * #1791 — a backend that is still narrating its startup is not stuck.
 *
 * The reporter's project lived on a mapped network drive, where the cold
 * `import torch` took far longer than the shell's five-minute readiness
 * budget. The shell killed the still-importing backend and respawned it, the
 * respawn raced the same clock, and the app reported "the backend never
 * reported ready" — while launching the very same backend by hand reached
 * ready in under a minute once the file cache was warm.
 *
 * Rust now keeps waiting for as long as `/startup/progress` answers (see
 * `keep_waiting_for_backend` in bootstrap.rs). This is the splash's half of
 * that contract: the stall watchdog keys on `bootstrap_status`, which sits on
 * `starting_backend` for the entire slow start, so on its own it would still
 * declare a false failure at six minutes. The proof of life arrives on the
 * `bootstrap-log` stream instead, and that has to count as activity.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBootstrapStage, noteBootstrapLogActivity } from '../components/BootstrapSplash';

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
  // The backend has not bound its port yet, so nothing answers /health — the
  // same condition that keeps the #879 IPC watchdog out of the way.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('ECONNREFUSED');
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  warnSpy.mockRestore();
  delete window.__TAURI_INTERNALS__;
  // Module-scoped signal — clear it so a later test's silent backend really is
  // silent.
  noteBootstrapLogActivity(0);
});

describe('useBootstrapStage — a narrating backend is not stuck (#1791)', () => {
  it('keeps waiting past the stall budget while startup steps still arrive', async () => {
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage: 'starting_backend' } : undefined,
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});
    expect(result.current.stage).toBe('starting_backend');

    // Twenty minutes of a genuinely slow cold start, with the backend
    // reporting a new step every four minutes — well inside the six-minute
    // budget each time, but far past it in total.
    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4 * 60 * 1000);
      });
      expect(result.current.stage).toBe('starting_backend');
      expect(result.current.message ?? '').not.toMatch(/stuck/i);
      act(() => {
        noteBootstrapLogActivity();
      });
    }

    expect(result.current.stage).toBe('starting_backend');
    expect(result.current.message ?? '').not.toMatch(/stuck/i);
  });

  it('still calls a genuinely silent backend stuck', async () => {
    // The other half: output is the evidence, and with none the watchdog has
    // to keep breaking the info-less spinner it exists for (#879).
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage: 'starting_backend' } : undefined,
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6 * 60 * 1000 + 2_000);
    });

    expect(result.current.stage).toBe('failed');
    expect(result.current.message).toMatch(/stuck/i);
  });
});
