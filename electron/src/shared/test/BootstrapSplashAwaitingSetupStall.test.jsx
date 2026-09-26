/**
 * #1376 — the first-run setup screen must not time out while it waits for you.
 *
 * The splash flips any stage that sits still past a budget to `failed`, so a
 * genuinely wedged bootstrap surfaces Retry and logs instead of an info-less
 * spinner. That is right for every stage the MACHINE owns.
 *
 * `awaiting_setup` is not one of them. Rust parks there on purpose — "nothing
 * downloads or installs in this stage, complete_setup is the only way out of
 * it" (bootstrap.rs) — and waits for a human to choose install mode, storage
 * locations, region and mirrors. That screen is built for deliberation, so
 * taking longer than a machine stage is the NORMAL case, not a fault.
 *
 * With the default 120 s budget applied to it, reading the setup screen for
 * two minutes replaced it with "Setup failed — the backend never reported
 * ready" and stopped the IPC poll. Retry re-enters the bootstrap, which parks
 * at awaiting_setup again, and fails again on the same clock — an unescapable
 * loop on the very first screen a new user ever sees.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBootstrapStage } from '../components/BootstrapSplash';

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
  // The hook early-returns 'ready' in dev builds; force the packaged path.
  vi.stubEnv('DEV', false);
  // Backend genuinely is not up during first-run setup — nothing to fall back
  // to over HTTP, which is also what keeps the #879 watchdog out of the way.
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
});

describe('useBootstrapStage — awaiting_setup is human-gated (#1376)', () => {
  it('stays on the setup screen no matter how long the user takes', async () => {
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage: 'awaiting_setup' } : undefined,
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});
    expect(result.current.stage).toBe('awaiting_setup');

    // Well past the old 120 s budget — a user reading the install plan.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    });

    expect(result.current.stage).toBe('awaiting_setup');
    expect(result.current.message ?? '').not.toMatch(/stuck/i);
  });

  it('still leaves for the install once complete_setup advances the stage', async () => {
    // The exemption must not strand the user the other way: when the plan is
    // submitted and Rust moves on, the normal progress UI has to take over.
    let stage = 'awaiting_setup';
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage } : undefined,
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    });
    expect(result.current.stage).toBe('awaiting_setup');

    stage = 'installing_deps';
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.stage).toBe('installing_deps');
  });

  it('lets Rust finish its backend startup budget before declaring a wedge', async () => {
    // The other half of the contract. Exempting awaiting_setup must not
    // disarm the stall detector for stages nothing but the app can advance —
    // that would trade this bug for the info-less infinite spinner (#879).
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage: 'starting_backend' } : undefined,
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {});
    expect(result.current.stage).toBe('starting_backend');

    // The shell waits five minutes for slow torch/CUDA imports. The splash
    // must not replace that live launch with its old two-minute false failure.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * 60 * 1000);
    });

    expect(result.current.stage).toBe('starting_backend');
    expect(result.current.message ?? '').not.toMatch(/stuck/i);

    // Pin both sides of the six-minute fallback boundary.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * 60 * 1000 - 1_000);
    });

    expect(result.current.stage).toBe('starting_backend');
    expect(result.current.message ?? '').not.toMatch(/stuck/i);

    // A frontend-only deadlock remains bounded if the shell never advances.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(result.current.stage).toBe('failed');
    expect(result.current.message).toMatch(/stuck/i);
  });
});
