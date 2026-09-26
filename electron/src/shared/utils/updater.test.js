import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { checkForUpdate, installUpdate } from './updater';

const updaterMocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  relaunch: vi.fn(),
  flush: vi.fn(),
}));

// Stub the Tauri core so checkForUpdate's "proceed" path resolves cleanly in
// jsdom without a real Tauri runtime. The guarded (early-return) cases never
// reach these imports.
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => updaterMocks.invoke(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: (...args) => updaterMocks.listen(...args),
}));
vi.mock('@tauri-apps/plugin-process', () => ({
  relaunch: (...args) => updaterMocks.relaunch(...args),
}));
vi.mock('./persistenceLifecycle', async (importOriginal) => ({
  ...(await importOriginal()),
  flushApplicationPersistence: (...args) => updaterMocks.flush(...args),
}));

function makeStore(status) {
  return {
    updateStatus: status,
    setUpdateChecking: vi.fn(),
    setUpdateAvailable: vi.fn(),
    setUpdateIdle: vi.fn(),
  };
}

describe('checkForUpdate periodic re-check guard', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
    updaterMocks.invoke
      .mockReset()
      .mockImplementation(async (cmd) => (cmd === 'get_update_channel' ? 'stable' : null));
    updaterMocks.listen.mockReset().mockResolvedValue(() => {});
    updaterMocks.relaunch.mockReset().mockResolvedValue(undefined);
    updaterMocks.flush.mockReset().mockResolvedValue({ failed: 0 });
  });
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  // Regression for #214: a 6h re-check while one of these states is showing
  // must NOT call setUpdateChecking() — that clears updateError and hides the
  // badge, silently wiping the "failed · retry" prompt (the 'error' case) or
  // resetting an in-flight job ('downloading' / 'ready').
  it.each(['error', 'downloading', 'ready'])(
    'no-ops while status is %s (leaves the badge intact)',
    async (status) => {
      const store = makeStore(status);
      await checkForUpdate(store);
      expect(store.setUpdateChecking).not.toHaveBeenCalled();
    },
  );

  it('proceeds from idle (boot / periodic check actually runs)', async () => {
    const store = makeStore('idle');
    await checkForUpdate(store);
    expect(store.setUpdateChecking).toHaveBeenCalledTimes(1);
    expect(store.setUpdateIdle).toHaveBeenCalled(); // mocked check_update → no update
  });
});

describe('installUpdate relaunch persistence', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
    updaterMocks.invoke
      .mockReset()
      .mockImplementation(async (cmd) => (cmd === 'get_update_channel' ? 'stable' : null));
    updaterMocks.listen.mockReset().mockResolvedValue(() => {});
    updaterMocks.relaunch.mockReset().mockResolvedValue(undefined);
    updaterMocks.flush.mockReset();
  });

  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it('keeps an installed update ready and relaunches when persistence rejects', async () => {
    updaterMocks.flush.mockRejectedValue(new DOMException('blocked', 'SecurityError'));
    const store = {
      setUpdateProgress: vi.fn(),
      setUpdateReady: vi.fn(),
      setUpdateError: vi.fn(),
    };

    await installUpdate(store);

    expect(store.setUpdateReady).toHaveBeenCalledOnce();
    expect(store.setUpdateError).not.toHaveBeenCalled();
    expect(updaterMocks.relaunch).toHaveBeenCalledOnce();
  });
});

describe('listReleases / fetchAppVersion', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
  });
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it('listReleases returns [] when not in Tauri', async () => {
    delete window.__TAURI_INTERNALS__;
    const { listReleases } = await import('./updater');
    expect(await listReleases('stable')).toEqual([]);
  });

  it('fetchAppVersion returns null when not in Tauri', async () => {
    delete window.__TAURI_INTERNALS__;
    const { fetchAppVersion } = await import('./updater');
    expect(await fetchAppVersion()).toBeNull();
  });
});
