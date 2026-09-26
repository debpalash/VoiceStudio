/**
 * The dictation widget window is a hidden host, and it must never be able to
 * decide it is the main window.
 *
 * The reported symptom was a dark, empty, square-cornered rectangle that
 * appeared on every dictation trigger and survived until the app was killed.
 * It measured exactly 300x64 — the widget window's declared size — and the
 * pill it should have contained is a capsule (`border-radius: 100px`), so what
 * was on screen was the WINDOW painting its own background with no pill in it.
 *
 * One cause explains all three properties. `detectIsWidget()` asked
 * `getCurrentWindow().label`, which throws while Tauri's internals are still
 * being injected; the catch then fell back to a URL query that Tauri 2 cannot
 * set, so the answer was "I am the main window". From there:
 *
 *   - `data-window="widget"` was never set  → opaque chrome background
 *   - `<App/>` rendered instead of the pill → empty
 *   - the idle-hide reconcile lives inside CaptureWidget, which never mounted
 *     → nothing in the process could hide it again
 *
 * The fix is an initialization_script (lib.rs) that stamps
 * `window.__OV_WINDOW__` before any page script runs. An init script cannot
 * race a readiness check, because there is nothing left to be ready for.
 *
 * These tests pin the frontend half: the marker wins over the API, and it is
 * still believed when the Tauri API is unavailable — the exact condition that
 * used to produce the stranded rectangle.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../App.jsx', () => ({ default: () => null }));
vi.mock('../components/CaptureWidget.jsx', () => ({ default: () => null }));

const mountRoot = () => {
  const root = document.createElement('div');
  root.id = 'root';
  document.body.appendChild(root);
};

beforeEach(async () => {
  vi.resetModules();
  const longformPersistence = await import('../utils/longformPersistence');
  longformPersistence.configureLongformDurableStoreForTests({
    read: async () => null,
    write: async () => {},
    clear: async () => {},
  });
  mountRoot();
  delete document.documentElement.dataset.window;
  delete window.__OV_WINDOW__;
});

afterEach(() => {
  document.getElementById('root')?.remove();
  delete document.documentElement.dataset.window;
  delete window.__OV_WINDOW__;
  vi.doUnmock('@tauri-apps/api/window');
  vi.restoreAllMocks();
});

describe('the widget window identifies itself from the injected marker', () => {
  it('trusts the marker even when the Tauri window API is unavailable', async () => {
    // THE REGRESSION. Before the init script this threw, the catch guessed
    // "main", and the window became an unhideable opaque rectangle.
    vi.doMock('@tauri-apps/api/window', () => {
      throw new Error('__TAURI_INTERNALS__ not injected yet');
    });
    window.__OV_WINDOW__ = 'widget';

    const { bootstrapApp } = await import('../main-app.jsx');
    await bootstrapApp();

    expect(document.documentElement.dataset.window).toBe('widget');
  });

  it('does not mark the main window, whose marker is absent', async () => {
    vi.doMock('@tauri-apps/api/window', () => ({
      getCurrentWindow: () => ({ label: 'main' }),
    }));

    const { bootstrapApp } = await import('../main-app.jsx');
    await bootstrapApp();

    expect(document.documentElement.dataset.window).toBeUndefined();
  });

  it('prefers the marker over a window label that disagrees', async () => {
    // The marker is injected by the shell that created the window, so it is
    // the more authoritative of the two — and unlike the label it cannot be
    // read before it exists.
    vi.doMock('@tauri-apps/api/window', () => ({
      getCurrentWindow: () => ({ label: 'main' }),
    }));
    window.__OV_WINDOW__ = 'widget';

    const { bootstrapApp } = await import('../main-app.jsx');
    await bootstrapApp();

    expect(document.documentElement.dataset.window).toBe('widget');
  });
});
