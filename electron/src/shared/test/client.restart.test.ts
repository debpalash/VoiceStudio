import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiFetch } from '../api/client';
import { backendLifecycleStage, classifyBootstrapStage } from '../utils/backendLifecycle';

// The recurring "Can't reach the local VoiceStudio backend" class: a REAL
// backend start/restart takes 10–20+ s (venv spawn + torch import), but the
// transport cascade used to give up after ~2.9 s — every request landing in a
// restart window dead-ended with the scary toast. apiFetch must now keep
// retrying exactly as long as the desktop shell says the backend is starting,
// and still fail promptly when the shell says failed / is absent.
vi.mock('../utils/backendLifecycle', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/backendLifecycle')>();
  return {
    ...actual,
    backendLifecycleStage: vi.fn().mockResolvedValue({ stage: 'unknown', message: null }),
  };
});

const stageMock = vi.mocked(backendLifecycleStage);
const CASCADE_MS = 400 + 900 + 1600;

/** The lifecycle probe answers `{ stage, message }` since #1177 — `message`
 * carries the shell's diagnosis and is null for every non-failed stage. */
const lc = (stage: string, message: string | null = null) =>
  ({ stage, message }) as Awaited<ReturnType<typeof backendLifecycleStage>>;

describe('apiFetch — lifecycle-aware restart wait', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    stageMock.mockReset();
    stageMock.mockResolvedValue(lc('unknown'));
  });

  it('keeps retrying while the shell says the backend is starting, then succeeds', async () => {
    vi.useFakeTimers();
    // 5 transport failures (cascade of 3 + 2 lifecycle-waited attempts), then OK.
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue(new Response('ok', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    stageMock.mockResolvedValue(lc('starting'));

    const p = apiFetch('/model/status');
    const assertion = expect(p).resolves.toMatchObject({ status: 200 });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 1500 * 2 + 100);
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(stageMock).toHaveBeenCalled();
  });

  it('fails promptly when the shell says the backend start failed', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('failed'));

    const p = apiFetch('/model/status');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("Can't reach the local VoiceStudio backend"),
    });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    await assertion;
  });

  // #1101 — the hole in the original fix, reported against 0.3.19. The shell's
  // stage is a 2 s POLL: when the backend dies mid-generate the supervisor needs
  // a moment to notice, flip to "starting" and write the crash marker. Asking
  // once at the end of the cascade still saw `ready`, so the request dead-ended
  // on the generic toast anyway. A transport failure contradicts `ready`, so we
  // must keep retrying long enough for the shell to catch up.
  it('does NOT believe a stale "ready" — it waits for the shell to notice the death', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    // The supervisor hasn't ticked yet: still 'ready' for the first few polls,
    // then it notices the death and flips to 'starting'.
    stageMock
      .mockResolvedValueOnce(lc('ready'))
      .mockResolvedValueOnce(lc('ready'))
      .mockResolvedValue(lc('starting'));

    const p = apiFetch('/generate');
    // Never settles into the generic error — it keeps waiting.
    const settled = vi.fn();
    p.then(settled, settled);
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 1000 + 1000 + 1500);
    expect(settled).not.toHaveBeenCalled();

    // Once the backend comes back, the request succeeds — the toast never fired.
    fetchMock.mockResolvedValue(new Response('ok', { status: 200 }));
    await vi.advanceTimersByTimeAsync(1500 * 2);
    await expect(p).resolves.toMatchObject({ status: 200 });
  });

  it('still gives up when a "ready" backend stays unreachable past the reconcile window', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('ready')); // shell insists it's fine; it never recovers

    const p = apiFetch('/model/status');
    const assertion = expect(p).rejects.toMatchObject({ status: 0 });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 12_000 + 2000);
    await assertion;
  });

  it('keeps the old prompt failure outside the Tauri shell (stage unknown)', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    stageMock.mockResolvedValue(lc('unknown'));

    const p = apiFetch('/model/status');
    const assertion = expect(p).rejects.toMatchObject({ status: 0 });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    await assertion;
    // Exactly the short cascade — no lifecycle-extended retries. Count only
    // the transport attempts against the requested path: the give-up branch
    // additionally probes the crash-forensics endpoint in browser mode
    // (#1164), which is diagnostics, not a retry.
    const transportCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).endsWith('/model/status'),
    );
    expect(transportCalls).toHaveLength(4);
  });
});

describe('classifyBootstrapStage', () => {
  it('maps shell stages to the coarse lifecycle answer', () => {
    expect(classifyBootstrapStage('ready')).toBe('ready');
    expect(classifyBootstrapStage('failed')).toBe('failed');
    for (const s of [
      'checking',
      'awaiting_setup',
      'downloading_uv',
      'creating_venv',
      'installing_deps',
      'starting_backend',
    ]) {
      expect(classifyBootstrapStage(s)).toBe('starting');
    }
    expect(classifyBootstrapStage('')).toBe('unknown');
    expect(classifyBootstrapStage(null)).toBe('unknown');
    expect(classifyBootstrapStage(undefined)).toBe('unknown');
  });
});

// #1113 — reported on v0.3.21, the release that was supposed to end this class.
// The user got "it may still be starting up, or it stopped" while the shell knew
// the backend was RUNNING and no crash was ever recorded. Both halves of that
// sentence were false: it hadn't stopped, and it wasn't starting. It was alive
// and wedged. Saying "restart the app" for a stuck job is the wrong advice.
describe('apiFetch — an alive-but-unresponsive backend says so (#1113)', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    stageMock.mockReset();
    stageMock.mockResolvedValue(lc('unknown'));
  });

  it('names the wedged-job case instead of claiming it stopped', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('ready')); // the shell KNOWS the process is alive

    const p = apiFetch('/generate');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining('running but stopped responding'),
    });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 12_000 + 2000);
    await assertion;
  });

  it('still says "starting up or stopped" when the shell has no idea (no shell)', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('unknown'));

    const p = apiFetch('/model/status');
    const assertion = expect(p).rejects.toMatchObject({
      message: expect.stringContaining("Can't reach the local VoiceStudio backend"),
    });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    await assertion;
  });
});
