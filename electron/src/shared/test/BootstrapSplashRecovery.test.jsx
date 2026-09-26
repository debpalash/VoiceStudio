/**
 * Regression tests for issue #879 — splash stuck at "preparing" forever when
 * the Tauri IPC layer is dead (corrupted WebView2 cache after a BSOD).
 *
 * fail-before/pass-after: with a hung `invoke()` (never resolves, never
 * rejects — the reported failure mode), the old useBootstrapStage poll loop
 * silently died and the stage never left 'checking'. The #879 watchdog now
 * escapes over plain HTTP or renders the recovery panel.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, renderHook, waitFor } from '@testing-library/react';
import { BootstrapSplash, useBootstrapStage } from '../components/BootstrapSplash';

const invokeMock = vi.fn();
const revealMock = vi.fn();
const persistenceMocks = vi.hoisted(() => ({ flush: vi.fn() }));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock('@tauri-apps/plugin-opener', () => ({
  revealItemInDir: (...args) => revealMock(...args),
}));
vi.mock('../utils/persistenceLifecycle', async (importOriginal) => ({
  ...(await importOriginal()),
  flushApplicationPersistence: (...args) => persistenceMocks.flush(...args),
}));

/** A promise that never settles — the exact #879 IPC failure mode. */
const hangForever = () => new Promise(() => {});

let warnSpy;

beforeEach(() => {
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  invokeMock.mockReset();
  revealMock.mockReset();
  persistenceMocks.flush.mockReset().mockResolvedValue({ failed: 0 });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  warnSpy.mockRestore();
  delete window.__TAURI_INTERNALS__;
});

describe('useBootstrapStage — #879 IPC-dead watchdog', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.__TAURI_INTERNALS__ = {};
    // The hook early-returns 'ready' in dev builds; force the packaged path.
    vi.stubEnv('DEV', false);
  });

  it('hung invoke + healthy backend over HTTP → proceeds to ready (was: stuck forever)', async () => {
    invokeMock.mockImplementation(hangForever);
    const fetchMock = vi.fn(async () => ({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {}); // flush dynamic imports + first (hanging) tick
    expect(result.current.stage).toBe('checking');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(result.current.stage).toBe('ready');
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:3900/health',
      expect.objectContaining({ cache: 'no-store' }),
    );
    // The IPC-fallback breadcrumb for diagnostic bundles / auto bug reports.
    const warned = warnSpy.mock.calls.map((c) => c.join(' ')).join('\n');
    expect(warned).toMatch(/no Tauri IPC signal/i);
    expect(warned).toMatch(/issue #879/);
  });

  it('hung invoke + dead backend → ipc_lost recovery stage after the 45s window, then auto-continues when the backend appears', async () => {
    invokeMock.mockImplementation(hangForever);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(44_000);
    });
    expect(result.current.stage).toBe('checking'); // not yet — still inside the window

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(result.current.stage).toBe('ipc_lost');

    // Backend eventually comes up (e.g. slow first-run install with broken
    // IPC): the splash must still hand over to the app.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true })),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });
    expect(result.current.stage).toBe('ready');
  });

  it('working IPC → normal path unchanged, watchdog disarmed, zero HTTP polling', async () => {
    invokeMock.mockImplementation(async (cmd) =>
      cmd === 'bootstrap_status' ? { stage: 'installing_deps', message: null } : null,
    );
    const fetchMock = vi.fn(async () => ({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useBootstrapStage());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.stage).toBe('installing_deps');

    // Way past both watchdog windows: stage is IPC-driven, health never polled.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(result.current.stage).toBe('installing_deps');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
  });
});

function setUserAgent(value) {
  const original = Object.getOwnPropertyDescriptor(window.navigator, 'userAgent');
  Object.defineProperty(window.navigator, 'userAgent', {
    value,
    configurable: true,
  });
  return () => {
    if (original) Object.defineProperty(window.navigator, 'userAgent', original);
    else delete window.navigator.userAgent;
  };
}

describe('BootstrapSplash — ipc_lost recovery panel', () => {
  it('renders the recovery panel instead of the progress list; no repair button on non-Windows', () => {
    const restore = setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)');
    try {
      render(<BootstrapSplash stage="ipc_lost" message={null} />);
      expect(screen.getByText("The app can't finish starting")).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Open logs/ })).toBeInTheDocument();
      // Windows-only affordance must not leak to other platforms.
      expect(screen.queryByRole('button', { name: /Repair and restart/ })).toBeNull();
      // The infinite-spinner progress list is gone.
      expect(screen.queryByText('Checking environment…')).toBeNull();
    } finally {
      restore();
    }
  });

  it('Windows: repair button invokes clear_webview_cache_and_relaunch after confirm', async () => {
    const restore = setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64)');
    const confirmMock = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmMock);
    invokeMock.mockResolvedValue(undefined);
    try {
      render(<BootstrapSplash stage="ipc_lost" message={null} />);
      fireEvent.click(screen.getByRole('button', { name: /Repair and restart/ }));
      await waitFor(() =>
        expect(invokeMock).toHaveBeenCalledWith('clear_webview_cache_and_relaunch'),
      );
      expect(confirmMock).toHaveBeenCalledTimes(1);
    } finally {
      restore();
    }
  });

  it('Windows: declining the confirm does not invoke the repair command', async () => {
    const restore = setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64)');
    vi.stubGlobal(
      'confirm',
      vi.fn(() => false),
    );
    try {
      render(<BootstrapSplash stage="ipc_lost" message={null} />);
      fireEvent.click(screen.getByRole('button', { name: /Repair and restart/ }));
      await act(async () => {});
      expect(invokeMock).not.toHaveBeenCalledWith('clear_webview_cache_and_relaunch');
    } finally {
      restore();
    }
  });

  it('Windows: persistence failure does not strand the explicit repair action', async () => {
    const restore = setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64)');
    vi.stubGlobal(
      'confirm',
      vi.fn(() => true),
    );
    persistenceMocks.flush.mockRejectedValue(new DOMException('blocked', 'SecurityError'));
    invokeMock.mockResolvedValue(undefined);
    try {
      render(<BootstrapSplash stage="ipc_lost" message={null} />);
      fireEvent.click(screen.getByRole('button', { name: /Repair and restart/ }));

      await waitFor(() =>
        expect(invokeMock).toHaveBeenCalledWith('clear_webview_cache_and_relaunch'),
      );
      expect(warnSpy).toHaveBeenCalledWith(
        '[persistence] cache-repair flush failed',
        expect.any(DOMException),
      );
    } finally {
      restore();
    }
  });

  it('Open logs falls back to an inline log-path hint when IPC is dead', async () => {
    invokeMock.mockRejectedValue(new Error('ipc dead'));
    render(<BootstrapSplash stage="ipc_lost" message={null} />);
    fireEvent.click(screen.getByRole('button', { name: /Open logs/ }));
    expect(await screen.findByText(/Couldn't open the folder automatically/)).toBeInTheDocument();
    expect(revealMock).not.toHaveBeenCalled();
  });
});
