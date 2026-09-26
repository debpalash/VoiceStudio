// ─────────────────────────────────────────────────────────────────
//  Provider wrapper for PAGE / PANEL visual specs.
//
//  Pure leaf components (Badge, Button…) render with no context, so the
//  base harness renders them bare. Pages and settings panels, by contrast,
//  reach for app infrastructure — the Zustand store (`useAppStore`),
//  react-i18next, `@tanstack/react-query`, and direct `api/*` fetches. To
//  snapshot them deterministically with NO Python backend, a spec OPTS IN
//  by declaring a `providers` block (see ./specs.jsx); the harness then
//  seeds that infrastructure here before rendering.
//
//  Nothing in this file runs for a pure leaf spec — the opt-in keeps the
//  existing leaf baselines byte-for-byte unaffected.
//
//  A spec's `providers` block (every field optional):
//    {
//      // Seed Zustand state. Object, or (ctx) => object — ctx = { theme }.
//      store: { locale: 'en', theme: 'gruvbox' },
//      // Pre-fill the react-query cache so useQuery() hooks resolve to data
//      // instead of a loading spinner. (queryClient, ctx) => void.
//      query: (qc) => qc.setQueryData(queryKeys.systemInfo, { … }),
//      // Canned responses for components that call api/* directly (no react
//      // query). Return a body object for a match, or undefined to fall
//      // through (→ a rejected fetch, surfacing un-mocked calls loudly).
//      fetch: (url, opts) => ({ … }) | undefined,
//    }
// ─────────────────────────────────────────────────────────────────

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';

import i18n from '../../i18n';
import { useAppStore } from '../../store';

// A QueryClient tuned for snapshots: no retries, no background refetching,
// nothing that could change a pixel after the first paint. Polling hooks
// (useSysinfo et al. set refetchInterval) are neutralised by killing the
// timers so a seeded value never gets overwritten by a (failing) refetch.
export function makeSnapshotQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
        staleTime: Infinity,
        refetchInterval: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        refetchOnMount: false,
      },
    },
  });
}

// Install a deterministic window.fetch for specs that hit api/* directly
// (StoragePanel's GET on mount, etc.). The handler maps a URL → a plain
// object, which we wrap in a real 200 JSON Response so apiFetch/apiJson
// behave exactly as against a live backend. An unmatched URL rejects with
// a clear error rather than hanging — an un-mocked call should be obvious,
// not silent. Returns an uninstaller that restores the real fetch.
export function installFetchStub(handler) {
  const real = window.fetch;
  window.fetch = (input, opts) => {
    const url = typeof input === 'string' ? input : input?.url || String(input);
    let body;
    try {
      body = handler(url, opts);
    } catch (e) {
      return Promise.reject(e);
    }
    if (body === undefined) {
      return Promise.reject(new Error(`[visual harness] unmocked fetch: ${url}`));
    }
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  };
  return () => {
    window.fetch = real;
  };
}

// Stub the Tauri IPC bridge for specs that render desktop-shell panels (the
// Storage destructive panels invoke `reset_scan` / `uninstall_scan` on mount).
// `@tauri-apps/api/core` reads `window.__TAURI_INTERNALS__.invoke`, so seeding
// that is enough for a dynamic `import('@tauri-apps/api/core')` to resolve to
// our handler — and the panels' own `inTauri()` check (which looks for the same
// global) then believes it is in the shell, so it renders instead of bailing.
export function installTauriStub(handler) {
  const real = window.__TAURI_INTERNALS__;
  window.__TAURI_INTERNALS__ = {
    ...real,
    invoke: (cmd, args) => Promise.resolve(handler(cmd, args)),
  };
  return () => {
    window.__TAURI_INTERNALS__ = real;
  };
}

// Prepare all infrastructure a provider-spec asked for, BEFORE first render,
// and return a function that wraps the spec's element in the live providers.
// Idempotent per page load (the harness renders once).
export function applyProviders(providers, ctx) {
  const resolved = providers || {};

  // 1. i18n — force English so a headless navigator locale can't drift the
  //    snapshot. The English bundle is bundled synchronously, so t() is
  //    correct on the very first render even though changeLanguage is async.
  void i18n.changeLanguage('en');

  // 2. Zustand — merge the representative slice into the live store. Object
  //    or (ctx) => object, so a spec can align the store theme with the
  //    rendered data-theme variant.
  if (resolved.store) {
    const partial = typeof resolved.store === 'function' ? resolved.store(ctx) : resolved.store;
    if (partial) useAppStore.setState(partial);
  }

  // 3. react-query — seed the cache so useQuery() returns data immediately.
  const queryClient = makeSnapshotQueryClient();
  if (resolved.query) resolved.query(queryClient, ctx);

  // 4. Direct api/* fetches — stub the global fetch.
  if (resolved.fetch) installFetchStub(resolved.fetch);

  // 5. Tauri IPC — stub window.__TAURI_INTERNALS__.invoke.
  if (resolved.invoke) installTauriStub(resolved.invoke);

  return function Wrap({ children }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
      </QueryClientProvider>
    );
  };
}
