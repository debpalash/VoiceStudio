import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiFetch } from '../api/client';
import { backendLifecycleStage, _toLifecycle } from '../utils/backendLifecycle';

// #1177 — "Can't reach the local VoiceStudio backend — it may still be starting
// up, or it stopped", reported with no other information to act on. That exact
// string is apiFetch's LAST fallback, reached only when no crash marker exists
// AND the shell's lifecycle stage is 'failed' or 'unknown'. The 'failed' half
// was the bug: `BootstrapStage::Failed { message }` carries the shell's WHOLE
// diagnosis — exit code plus a ~30-line stderr tail, or the precise reason the
// venv bootstrap refused — and `backendLifecycleStage()` returned only the
// stage tag, throwing the message away. Every backend-start failure mode
// therefore collapsed into one generic, evidence-free sentence that was also
// factually wrong: the backend is not starting, and it will not come back.
//
// Fail-before/pass-after: with a diagnosis in hand, apiFetch must surface it.
vi.mock('../utils/backendLifecycle', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/backendLifecycle')>();
  return {
    ...actual,
    backendLifecycleStage: vi.fn().mockResolvedValue({ stage: 'unknown', message: null }),
  };
});

const stageMock = vi.mocked(backendLifecycleStage);
const CASCADE_MS = 400 + 900 + 1600;
const lc = (stage: string, message: string | null = null) =>
  ({ stage, message }) as Awaited<ReturnType<typeof backendLifecycleStage>>;

const DIAGNOSIS =
  'Backend process exited (exit status: 1):\n' +
  'Traceback (most recent call last):\n' +
  '  File "/Users/alice/Library/Application Support/OmniVoice/project/backend/main.py", line 3\n' +
  'ModuleNotFoundError: No module named `torch`';

async function rejection(p: Promise<unknown>, advanceMs: number): Promise<Error> {
  const caught = p.then(
    () => null,
    (e) => e as Error,
  );
  await vi.advanceTimersByTimeAsync(advanceMs);
  const err = await caught;
  if (!err) throw new Error('expected apiFetch to reject');
  return err;
}

describe('apiFetch — a failed backend start surfaces the shell diagnosis (#1177)', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    stageMock.mockReset();
    stageMock.mockResolvedValue(lc('unknown'));
  });

  it('reports the exit code and stderr tail instead of the generic fallback', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('failed', DIAGNOSIS));

    const err = await rejection(apiFetch('/model/status'), CASCADE_MS + 100);
    // The evidence the user needs, which the old message discarded.
    expect(err.message).toContain('exit status: 1');
    expect(err.message).toContain('ModuleNotFoundError');
    expect(err.message).toContain('could not start');
    // And NOT the generic, factually-wrong sentence #1177 reported.
    expect(err.message).not.toContain('it may still be starting up, or it stopped');
  });

  it('scrubs the absolute home path out of the diagnosis', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('failed', DIAGNOSIS));

    const err = await rejection(apiFetch('/model/status'), CASCADE_MS + 100);
    expect(err.message).not.toContain('/Users/alice');
    expect(err.message).toContain('~/Library/Application Support/OmniVoice');
  });

  it('carries the diagnosis on the ApiError detail and raises the notice event', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('failed', DIAGNOSIS));
    const events: string[] = [];
    const onFailed = (e: Event) => events.push((e as CustomEvent).detail?.message);
    window.addEventListener('ov:backend-start-failed', onFailed);

    try {
      const err = (await rejection(apiFetch('/model/status'), CASCADE_MS + 100)) as Error & {
        detail?: { startFailure?: string };
      };
      expect(err.detail?.startFailure).toContain('ModuleNotFoundError');
      // BackendStartFailureNotice renders this — already scrubbed at dispatch.
      expect(events).toHaveLength(1);
      expect(events[0]).toContain('ModuleNotFoundError');
      expect(events[0]).not.toContain('/Users/alice');
    } finally {
      window.removeEventListener('ov:backend-start-failed', onFailed);
    }
  });

  it('keeps the generic fallback when the shell failed WITHOUT a diagnosis', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('failed', null));

    const err = await rejection(apiFetch('/model/status'), CASCADE_MS + 100);
    expect(err.message).toContain("Can't reach the local VoiceStudio backend");
  });

  // #1164 must not regress: outside the desktop shell there IS no shell to
  // fail this way (the stage is always 'unknown'), so browser/dev/Docker keep
  // their deployment-specific message and its own forensics pointer.
  it('leaves the non-Tauri deployment message untouched', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    stageMock.mockResolvedValue(lc('unknown'));

    const err = await rejection(apiFetch('/model/status'), CASCADE_MS + 100);
    expect(err.message).toContain("Can't reach the local VoiceStudio backend");
    expect(err.message).not.toContain('could not start');
  });
});

describe('_toLifecycle', () => {
  it('keeps the diagnosis only for a failed stage', () => {
    expect(_toLifecycle({ stage: 'failed', message: 'boom' })).toEqual({
      stage: 'failed',
      message: 'boom',
    });
    // A stray message on a healthy stage would be stale evidence pinned to a
    // working backend — drop it.
    expect(_toLifecycle({ stage: 'ready', message: 'boom' })).toEqual({
      stage: 'ready',
      message: null,
    });
    expect(_toLifecycle({ stage: 'starting_backend', message: 'boom' })).toEqual({
      stage: 'starting',
      message: null,
    });
  });

  it('normalizes an empty, blank, or non-string message to null', () => {
    expect(_toLifecycle({ stage: 'failed', message: '   ' }).message).toBeNull();
    expect(_toLifecycle({ stage: 'failed', message: '' }).message).toBeNull();
    expect(_toLifecycle({ stage: 'failed', message: 42 }).message).toBeNull();
    expect(_toLifecycle({ stage: 'failed' }).message).toBeNull();
    expect(_toLifecycle(null)).toEqual({ stage: 'unknown', message: null });
  });

  it('trims surrounding whitespace off a real diagnosis', () => {
    expect(_toLifecycle({ stage: 'failed', message: '\n boom \n' }).message).toBe('boom');
  });
});
