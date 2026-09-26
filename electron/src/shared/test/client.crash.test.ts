import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiFetch } from '../api/client';
import { getUnacknowledgedBackendCrash } from '../utils/backendCrash';

// #941: when the transport failure coincides with a recorded backend crash,
// the vague "Can't reach the local VoiceStudio backend" must become the honest
// story — exit code + how long ago — and the crash-notice event must fire so
// the UI can offer "View crash details".
vi.mock('../utils/backendCrash', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/backendCrash')>();
  return {
    ...actual,
    getUnacknowledgedBackendCrash: vi.fn().mockResolvedValue(null),
  };
});

const crashMock = vi.mocked(getUnacknowledgedBackendCrash);

function markerSecondsAgo(s: number) {
  return {
    ts: Math.floor(Date.now() / 1000) - s,
    exit_code: 3221226505,
    signal: null,
    exit_desc: 'exit code: 3221226505',
    backend_version: '0.3.10',
    uptime_s: 42,
    last_stderr: 'OSError: [WinError 1455] The paging file is too small',
    acknowledged: false,
  };
}

describe('apiFetch — crash-marker honesty (#941)', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    crashMock.mockClear();
    crashMock.mockResolvedValue(null);
  });

  it('replaces the vague unreachable error with the honest crash story', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    crashMock.mockResolvedValue(markerSecondsAgo(15));
    const events: unknown[] = [];
    const onCrash = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener('ov:backend-crashed', onCrash);

    const p = apiFetch('/generate');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      // fake timers advance Date.now() during the retry backoff, so assert
      // the shape (exit code + a seconds-scale age), not an exact second.
      message: expect.stringMatching(/crashed \(exit code 3221226505\) \d+ s ago/),
    });
    await vi.advanceTimersByTimeAsync(400 + 900 + 1600 + 100);
    await assertion;

    // The crash-notice affordance is driven by this event.
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ exit_code: 3221226505 });
    window.removeEventListener('ov:backend-crashed', onCrash);
  });

  it('keeps the generic message when no unacknowledged crash exists', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    crashMock.mockResolvedValue(null);

    const p = apiFetch('/generate');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("Can't reach the local VoiceStudio backend"),
    });
    await vi.advanceTimersByTimeAsync(400 + 900 + 1600 + 100);
    await assertion;
  });

  it('never turns an HTTP error into a crash story (backend responded)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('nope', { status: 500, statusText: 'Server Error' })),
    );
    crashMock.mockResolvedValue(markerSecondsAgo(5));
    await expect(apiFetch('/x')).rejects.toMatchObject({ status: 500 });
    expect(crashMock).not.toHaveBeenCalled();
  });
});
