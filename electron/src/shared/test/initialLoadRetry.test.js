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
