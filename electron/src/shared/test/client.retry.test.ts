import i18n from 'i18next';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiFetch, ApiError } from '../api/client';

// #567/#570/#571: when the backend dies mid-session, the auto-restart
// supervisor revives it within a few seconds. apiFetch must ride out that
// brief window by retrying a *transport* failure (a thrown fetch) a bounded
// few times — but never retry an HTTP error (the backend responded) or a
// deliberate abort, and still surface the actionable error if it stays down.
describe('apiFetch transport-retry', () => {
  it('localizes Argos runtime errors without losing recovery metadata', async () => {
    const detail = { code: 'argos_runtime_unavailable', message: 'Raw native diagnostic' };
    vi.spyOn(i18n, 't').mockReturnValue('Localized recovery guidance');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 400 })),
    );
    const err = await apiFetch('/dub/translate').catch((error) => error);
    expect(err.message).toContain('Localized recovery guidance');
    expect(err.detail).toEqual(detail);
  });
  it.each([false, true])('localizes HTTP failure topics (nested: %s)', async (nested) => {
    const translate = vi.spyOn(i18n, 't').mockReturnValue('Localized recovery');
    const failure = { docs_topic: 'GPU_ARCH_UNSUPPORTED', detail: 'English fallback' };
    const payload = nested ? { detail: failure } : failure;
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(payload), { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);
    const error = await apiFetch('/generate').catch((e) => e);
    expect(error.message).toContain('Localized recovery');
    expect(error.detail).toEqual(payload.detail);
    expect(translate).toHaveBeenCalledWith('tts_errors.gpu_arch_unsupported');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('retries a transient transport failure, then succeeds', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('ok', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const p = apiFetch('/health');
    await vi.advanceTimersByTimeAsync(500); // first backoff is 400ms
    const res = await p;

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does NOT retry an HTTP error response', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('nope', { status: 500, statusText: 'Server Error' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/x')).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('can disable transport retries for one-shot capability requests', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/one-shot', { retryTransport: false })).rejects.toMatchObject({
      status: 0,
    });
    const transportCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith('/one-shot'),
    );
    expect(transportCalls).toHaveLength(1);
  });

  it('does NOT call fetch once the signal is already aborted', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const ac = new AbortController();
    ac.abort();

    await expect(apiFetch('/x', { signal: ac.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('gives up after the bounded retries with a status:0 ApiError', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    const p = apiFetch('/x');
    const assertion = expect(p).rejects.toMatchObject({ status: 0 });
    await vi.advanceTimersByTimeAsync(400 + 900 + 1600 + 100);
    await assertion;
    // initial attempt + 3 bounded retries. Count only the transport attempts
    // against the requested path — the give-up branch also probes the crash
    // forensics endpoint in browser mode (#1164), which is not a retry.
    const transportCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/x'));
    expect(transportCalls).toHaveLength(4);
  });
});
