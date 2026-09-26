/**
 * Tests for utils/apiBase.ts (Phase 1 Wave 3, issue #80).
 *
 * Covers the four-branch resolver:
 *   1. VITE_OMNIVOICE_API override always wins.
 *   2. Tauri webview context → 127.0.0.1:3900 (IPv4; not "localhost" — Windows IPv6).
 *   3. Plain browser context → window.location.hostname:3900.
 *   4. SSR / no-window fallback → localhost:3900.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

describe('apiBase.getApiBase', () => {
  // Snapshot what we mutate so each test starts clean.
  let originalTauriInternals: unknown;
  let originalTauri: unknown;
  let originalLocation: Location;

  beforeEach(() => {
    originalTauriInternals = (window as Window).__TAURI_INTERNALS__;
    originalTauri = (window as Window).__TAURI__;
    originalLocation = window.location;
    // jsdom defaults VITE_OMNIVOICE_API to undefined — no need to stub.
    vi.resetModules();
  });

  afterEach(() => {
    delete (window as Window).__OMNIVOICE_API_BASE__;
    if (originalTauriInternals === undefined) {
      delete (window as Window).__TAURI_INTERNALS__;
    } else {
      (window as Window).__TAURI_INTERNALS__ = originalTauriInternals;
    }
    if (originalTauri === undefined) {
      delete (window as Window).__TAURI__;
    } else {
      (window as Window).__TAURI__ = originalTauri;
    }
    // Restore window.location via the descriptor — jsdom protects against
    // direct reassignment, so we have to override via defineProperty in each
    // test then restore here.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    });
  });

  it('uses VITE_OMNIVOICE_API override when set', async () => {
    // vi.stubEnv works for inline `import.meta.env` reads in the test file,
    // but does not propagate to dynamically imported modules' import.meta in
    // vitest 4.x (Vite's per-module env snapshot). Instead, we patch the
    // module's resolver function via its (intentionally exposed) override
    // hook so the test exercises the same code path.
    vi.resetModules();
    const mod = await import('./apiBase');
    mod._setEnvOverrideForTesting('http://10.0.0.5:3900');
    expect(mod.getApiBase()).toBe('http://10.0.0.5:3900');
    mod._setEnvOverrideForTesting(undefined);
  });

  it('strips trailing slash from override', async () => {
    vi.resetModules();
    const mod = await import('./apiBase');
    mod._setEnvOverrideForTesting('http://10.0.0.5:3900/');
    expect(mod.getApiBase()).toBe('http://10.0.0.5:3900');
    mod._setEnvOverrideForTesting(undefined);
  });

  it('runtime window.__OMNIVOICE_API_BASE__ wins over Tauri + env (Docker prebuilt-image override)', async () => {
    (window as Window).__TAURI_INTERNALS__ = {};
    (window as Window).__OMNIVOICE_API_BASE__ = 'https://api.example.com/';
    vi.resetModules();
    const mod = await import('./apiBase');
    mod._setEnvOverrideForTesting('http://10.0.0.5:3900');
    expect(mod.getApiBase()).toBe('https://api.example.com'); // trailing slash stripped
    mod._setEnvOverrideForTesting(undefined);
  });

  it('returns 127.0.0.1:3900 (NOT localhost) in Tauri context — Windows IPv6 fix', async () => {
    // The backend binds IPv4 127.0.0.1 only; "localhost" can resolve to ::1 on
    // Windows and miss it, causing "Can't reach the local backend" on the media/
    // preview path. Must be the numeric IPv4 address (matches api/client.ts).
    (window as Window).__TAURI_INTERNALS__ = {};
    const { getApiBase } = await import('./apiBase');
    expect(getApiBase()).toBe('http://127.0.0.1:3900');
  });

  it('returns window.location.hostname:3900 in plain browser context (LAN IP)', async () => {
    delete (window as Window).__TAURI_INTERNALS__;
    delete (window as Window).__TAURI__;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        protocol: 'http:',
        hostname: '192.168.1.50',
        host: '192.168.1.50:5173',
        href: 'http://192.168.1.50:5173/',
      },
    });
    const { getApiBase } = await import('./apiBase');
    expect(getApiBase()).toBe('http://192.168.1.50:3900');
  });

  it('respects page protocol (https) when serving over LAN TLS', async () => {
    delete (window as Window).__TAURI_INTERNALS__;
    delete (window as Window).__TAURI__;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        protocol: 'https:',
        hostname: 'studio.local',
        host: 'studio.local',
        href: 'https://studio.local/',
      },
    });
    const { getApiBase } = await import('./apiBase');
    expect(getApiBase()).toBe('https://studio.local:3900');
  });

  it('BACKEND_PORT constant is exported and equals 3900', async () => {
    const { BACKEND_PORT } = await import('./apiBase');
    expect(BACKEND_PORT).toBe(3900);
  });
});
