/**
 * #1802/#1805 — a give-up must not invent a crash it has no evidence for.
 *
 * Two reporters on Apple Silicon hit "it most likely crashed or was killed
 * mid-request" while generating. Neither bug report carried a crash marker,
 * because none had been recorded — the shell learns the backend died from a
 * ~2 s poll, and `apiFetch` asked for the marker exactly ONCE, at the instant
 * the transport gave up. That ask races the poll and loses both ways round:
 *
 *   - the backend really died → no marker yet → the user gets the vague
 *     sentence instead of the exit code and "View crash details";
 *   - the backend never died → no marker ever → the user is told it crashed
 *     anyway, and sent to Retry and Clean & Retry (which rebuilds the Python
 *     environment) to fix a process that was merely wedged.
 *
 * `streamDropError` already waits that poll out (#1119). This pins the same
 * behaviour for the request path, and that the fallback copy stops asserting
 * a cause.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiError } from '../api/client';
import {
  recordBackendContact,
  unreachableBackendMessage,
  _resetBackendContactForTests,
} from '../utils/backendContact';

const CASCADE_MS = 400 + 900 + 1600;

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

const MARKER = {
  ts: 1_700_000_000,
  exit_code: null,
  signal: 9,
  exit_desc: 'signal: 9 (SIGKILL)',
  backend_version: '0.5.2',
  uptime_s: 42,
  last_stderr: 'MPS backend out of memory',
  acknowledged: false,
};

beforeEach(() => {
  invokeMock.mockReset();
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

describe('apiFetch give-up vs. the shell death poll (#1802/#1805)', () => {
  it('reports the real crash when the marker lands after the first ask', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    // The shell needs two poll intervals to notice the child exit and write
    // the marker — exactly the race that produced the unevidenced sentence.
    let asks = 0;
    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === 'get_last_backend_crash') {
        asks += 1;
        return asks >= 3 ? MARKER : null;
      }
      // The shell has already given up on this backend, so the retry loop
      // above ends at the cascade and the give-up path runs immediately.
      if (cmd === 'bootstrap_status') return { stage: 'failed' };
      return null;
    });

    const { apiFetch } = await import('../api/client');
    const settled = apiFetch('/generate').catch((e: ApiError) => e);
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 6_000);
    const err = (await settled) as ApiError;

    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/backend crashed/i);
    expect(err.message).toContain('signal 9');
    expect(err.message).not.toMatch(/most likely crashed/);
  });

  it('stops asserting a crash when the wait ends with no marker at all', async () => {
    // Nothing recorded a death, so the one thing we actually know is that the
    // backend went quiet — the copy has to stop naming a cause.
    _resetBackendContactForTests();
    recordBackendContact(10_000);
    const msg = unreachableBackendMessage('desktop', 14_000);
    expect(msg).not.toMatch(/most likely crashed|was killed/i);
    expect(msg).toMatch(/no crash was recorded/i);
  });
});

describe('canceling diagnostic waits', () => {
  it.each([
    [100, 'failed'],
    [CASCADE_MS + 100, 'failed'],
    [CASCADE_MS + 100, 'starting'],
    [CASCADE_MS + 100, 'ready'],
  ])('honors abort at %i ms in %s', async (abortAt, stage) => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    invokeMock.mockImplementation(async (cmd: string) =>
      cmd === 'bootstrap_status' ? { stage } : null,
    );
    const { apiFetch } = await import('../api/client');
    const controller = new AbortController();
    let settled = false;
    const result = apiFetch('/generate', { signal: controller.signal }).catch((error) => {
      settled = true;
      return error;
    });
    await vi.advanceTimersByTimeAsync(Number(abortAt));
    controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(true);
    expect(await result).toMatchObject({ name: 'AbortError' });
  });
});
