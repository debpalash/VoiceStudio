import { describe, it, expect, vi } from 'vitest';
import { loadLatest, retryInitialLoad } from '../utils/initialLoadRetry';

// #1158 class: the initial data loads (profiles/history/…) ran exactly once
// after the backend became reachable. A transient failure on that single call
// (backend restarting, WS reconnect window, a 502 from the LAN gate) left the
// voices panel empty with no retry — which reads to users as "all my voices
// are gone". The load itself already keeps the previous list on later (WS)
// reloads; only the INITIAL load must retry until first success.

describe('retryInitialLoad (#1158 class)', () => {
  it('retries a failing loader until it succeeds', async () => {
    let calls = 0;
    const loader = vi.fn(async () => {
      calls += 1;
      if (calls < 3) throw new Error('transient');
    });
    await retryInitialLoad(loader, { baseDelayMs: 1 });
    expect(calls).toBe(3);
  });

  it('does not retry after first success', async () => {
    const loader = vi.fn(async () => {});
    await retryInitialLoad(loader, { baseDelayMs: 1 });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('stops retrying when cancelled', async () => {
    const loader = vi.fn(async () => {
      throw new Error('down');
    });
    const opts = { baseDelayMs: 1 };
    const p = retryInitialLoad(loader, opts);
    opts.cancelled = true;
    await p;
    // At most one attempt plus whatever was already in flight; the point is
    // it terminates instead of looping forever after cancellation.
    expect(loader.mock.calls.length).toBeLessThan(5);
  });

  // #1158 wiring contract: the initial load must pass loaders that REJECT on
  // failure. useAppData's loadProfiles-style loaders swallow errors by design
  // (WS reloads keep the previous list); if the initial-load call site ever
  // stops using { rethrow: true }, the retry helper resolves on attempt one
  // and the fix is silently inert again (skeptic finding F1).
  it('integration: useAppData wires rethrowing loaders into the retry', async () => {
    const { readFileSync } = await import('node:fs');
    // jsdom replaces the global URL, so build a REAL node: URL instance and
    // hand that to fileURLToPath. Taking `.pathname` instead looks equivalent
    // but is not on Windows: a file:///C:/… URL's pathname is "/C:/…", which
    // readFileSync then resolves against the current drive as "C:\C:\…" and
    // the whole test file fails with ENOENT on every Windows checkout.
    const nodeUrl = await import('node:url');
    const NodeURL = nodeUrl.URL;
    const hookPath = nodeUrl.fileURLToPath(new NodeURL('../hooks/useAppData.js', import.meta.url));
    const src = readFileSync(hookPath, 'utf8');
    const initialBlock = src.slice(src.indexOf('retryInitialLoad('));
    for (const loader of [
      'loadProfiles',
      'loadHistory',
      'loadDubHistory',
      'loadProjects',
      'loadExportHistory',
    ]) {
      expect(initialBlock).toContain(`${loader}({ rethrow: true })`);
    }
    expect(src).toContain('rethrow,');
  });
});

describe('loadLatest', () => {
  it('does not let a stale initial response overwrite a WebSocket reload', async () => {
    let resolveInitial;
    let resolveReload;
    const initial = new Promise((resolve) => {
      resolveInitial = resolve;
    });
    const reload = new Promise((resolve) => {
      resolveReload = resolve;
    });
    const generations = {};
    const apply = vi.fn();

    const initialLoad = loadLatest({
      generations,
      key: 'profiles',
      fetch: () => initial,
      apply,
      label: 'profiles',
    });
    const websocketReload = loadLatest({
      generations,
      key: 'profiles',
      fetch: () => reload,
      apply,
      label: 'profiles',
    });

    resolveReload(['new']);
    await websocketReload;
    resolveInitial(['stale']);
    await initialLoad;

    expect(apply).toHaveBeenCalledOnce();
    expect(apply).toHaveBeenCalledWith(['new']);
  });

  it('retries when a stale initial success is discarded after a newer reload fails', async () => {
    let resolveInitial;
    let rejectReload;
    const initial = new Promise((resolve) => {
      resolveInitial = resolve;
    });
    const reload = new Promise((_resolve, reject) => {
      rejectReload = reject;
    });
    const generations = {};
    const apply = vi.fn();
    const fetch = vi
      .fn()
      .mockReturnValueOnce(initial)
      .mockReturnValueOnce(reload)
      .mockResolvedValueOnce(['recovered']);

    const initialLoad = retryInitialLoad(
      () =>
        loadLatest({
          generations,
          key: 'profiles',
          fetch,
          apply,
          label: 'profiles',
          rethrow: true,
        }),
      { baseDelayMs: 1 },
    );
    const websocketReload = loadLatest({
      generations,
      key: 'profiles',
      fetch,
      apply,
      label: 'profiles',
    });

    rejectReload(new Error('reload failed'));
    await websocketReload;
    resolveInitial(['stale']);
    await initialLoad;

    expect(fetch).toHaveBeenCalledTimes(3);
    expect(apply).toHaveBeenCalledOnce();
    expect(apply).toHaveBeenCalledWith(['recovered']);
  });
});
