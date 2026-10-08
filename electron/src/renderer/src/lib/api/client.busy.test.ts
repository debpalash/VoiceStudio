import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ stage: 'ready' as string }));

// `apiFetch` reads the stage through this module, so the real one is replaced
// with a controllable snapshot. `isBackendReachable` stays REAL: it is the
// predicate under test, and stubbing it would make these assertions vacuous.
vi.mock('@/hooks/use-backend-status', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/use-backend-status')>();
  return { ...actual, getBackendStatusSnapshot: () => ({ stage: state.stage }) };
});

const { apiFetch, apiJson } = await import('./client');

/** The guard only engages in the native shell, where a real bridge exists. */
function nativeShell(): void {
  vi.stubGlobal('voicestudio', { backend: {} });
}

beforeEach(() => {
  state.stage = 'ready';
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// #2430 — the renderer has its own pre-flight guard, separate from the shared
// client this PR already taught to wait. It rejected every request whose stage
// was not `ready`, so a live-but-busy backend failed the request before it was
// ever issued — including the fetch for the finished audio of a streamed
// generation that had already succeeded.
describe('apiFetch while a live backend is only busy (#2430)', () => {
  it('issues the request instead of rejecting an unresponsive stage', async () => {
    state.stage = 'unresponsive';
    nativeShell();
    const fetchMock = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiJson<{ ok: boolean }>('/audio/take.wav')).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('still rejects a terminal failure without issuing a doomed request', async () => {
    state.stage = 'crashed';
    nativeShell();
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/audio/take.wav')).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('still rejects while the backend has not started listening', async () => {
    state.stage = 'starting';
    nativeShell();
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/audio/take.wav')).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('leaves the browser build alone, with or without a stage', async () => {
    // No native bridge: the guard never engaged here and must not start to.
    state.stage = 'unresponsive';
    vi.stubGlobal('fetch', async () => new Response('{"ok":true}', { status: 200 }));

    await expect(apiJson<{ ok: boolean }>('/system/info')).resolves.toEqual({ ok: true });
  });
});
