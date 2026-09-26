/**
 * Centralised API base URL resolver.
 *
 * Single source of truth for "where is the VoiceStudio backend reachable from
 * the currently-rendering frontend?". Three runtime contexts need different
 * answers:
 *
 *   1. Explicit override (Docker users / CI / power users):
 *        VITE_OMNIVOICE_API="http://10.0.0.5:3900"
 *      Always wins. Set in `.env.local` or the docker-compose env.
 *
 *   2. Tauri webview (the shipped desktop app):
 *      Backend always listens on IPv4 127.0.0.1:3900 on the same machine, so we
 *      target the numeric `http://127.0.0.1:3900` — NOT `localhost`, which on
 *      Windows can resolve to ::1 (IPv6) first and miss the IPv4-only backend.
 *      (api/client.ts already does this; this util is kept in lockstep.)
 *      Tauri's webview origin (`tauri://localhost`) is unaffected.
 *
 *   3. Plain browser (Docker LAN, port-forward, dev server on a NAS):
 *      The browser was served from some host — likely a LAN IP. We must
 *      target THAT host's :3900, not the browser machine's localhost.
 *      This closes issue #80 (Docker LAN frontend hits the wrong host).
 *
 * Plan: 01-03-PLAN.md (Phase 1 Wave 3)
 * Issue: #80
 */

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
    __TAURI__?: unknown;
    /** Runtime API base injected into index.html by the backend from
     *  OMNIVOICE_PUBLIC_API_BASE (Docker / reverse-proxy deployments). */
    __OMNIVOICE_API_BASE__?: string;
  }
}

/** Backend port — kept here as a single constant so we never grep-replace
 *  hard-coded `3900` across the codebase again. */
export const BACKEND_PORT = 3900;

/** True when the current execution context is a Tauri webview. */
export function isTauriContext(): boolean {
  return typeof window !== 'undefined' && !!(window.__TAURI_INTERNALS__ || window.__TAURI__);
}

/**
 * Resolve the backend API base URL for the current runtime context.
 *
 * Returns a URL with NO trailing slash so callers can safely concatenate
 * `/preview/upload` etc.
 */
/** Test-only override for the env-resolved API base. vitest 4.x does not
 *  propagate `vi.stubEnv` to dynamically imported modules' `import.meta.env`,
 *  so we expose this small hook for tests. Production code never sets it. */
let _testEnvOverride: string | undefined = undefined;
export function _setEnvOverrideForTesting(value: string | undefined): void {
  _testEnvOverride = value;
}

function _readEnvOverride(): string | undefined {
  if (_testEnvOverride !== undefined) return _testEnvOverride;
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  return env?.VITE_OMNIVOICE_API;
}

export function getApiBase(): string {
  // 0. Runtime override injected by the backend (Docker/proxy) wins over
  //    everything — it's the only knob a prebuilt image can turn at run time.
  if (typeof window !== 'undefined') {
    const runtime = window.__OMNIVOICE_API_BASE__;
    if (typeof runtime === 'string' && runtime) {
      return stripTrailingSlash(runtime);
    }
  }

  // 1. Explicit build-time override.
  const override = _readEnvOverride();
  if (override) {
    return stripTrailingSlash(override);
  }

  // 2. Tauri webview → loopback. Use the literal IPv4 127.0.0.1, NOT "localhost".
  //    The backend binds IPv4 127.0.0.1 only (backend/main.py), but on Windows
  //    "localhost" frequently resolves to ::1 (IPv6) first — so
  //    http://localhost:3900 hits an address nothing is listening on and the
  //    request fails with "Can't reach the local backend". The main API client
  //    (api/client.ts) already resolves Tauri → 127.0.0.1; this util lagged on
  //    "localhost", so its one consumer — utils/media.js's preview/blob upload
  //    (the audiobook/video preview path, #653) — still broke on Windows. Align
  //    the two resolvers. The numeric address skips name resolution and is
  //    correct on macOS/Linux too (the backend is always on this machine).
  if (isTauriContext()) {
    return `http://127.0.0.1:${BACKEND_PORT}`;
  }

  // 3. Plain browser → follow the page's own origin/host.
  if (typeof window !== 'undefined' && window.location) {
    const { protocol, hostname } = window.location;
    if (hostname) {
      return `${protocol}//${hostname}:${BACKEND_PORT}`;
    }
  }

  // 4. SSR / vitest jsdom without window — safe fallback.
  return `http://localhost:${BACKEND_PORT}`;
}

function stripTrailingSlash(url: string): string {
  return url.endsWith('/') ? url.slice(0, -1) : url;
}

/** Module-level cached base URL — resolved once at import time. Most callers
 *  want this; only call `getApiBase()` directly if you need to re-evaluate
 *  after env or window changes (rare, mostly tests). */
export const API_BASE: string = getApiBase();

export default API_BASE;
