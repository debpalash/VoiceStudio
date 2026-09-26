import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  API,
  _bootstrapBrowserCredentials,
  _isApiTarget,
  _parseDeepLinkCredentials,
  wsUrl,
} from './client';
import { ADMIN_SESSION_STORAGE_KEY, CSRF_HEADER_NAME } from './authSession';

describe('apiFetch PIN header', () => {
  let realFetch: typeof globalThis.fetch;
  beforeEach(() => {
    realFetch = globalThis.fetch;
    sessionStorage.clear();
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    sessionStorage.clear();
  });

  it('attaches X-OmniVoice-Pin when present in sessionStorage', async () => {
    sessionStorage.setItem('ov_pin', '424242');
    const seen: any = {};
    globalThis.fetch = vi.fn((_url, opts) => {
      Object.assign(seen, opts);
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }) as any;
    const { apiFetch } = await import('./client');
    await apiFetch('/system/info');
    expect(new Headers(seen.headers).get('X-OmniVoice-Pin')).toBe('424242');
    expect(seen.credentials).toBe('include');
  });

  it('omits the header when no pin', async () => {
    const seen: any = {};
    globalThis.fetch = vi.fn((_url, opts) => {
      Object.assign(seen, opts);
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }) as any;
    const { apiFetch } = await import('./client');
    await apiFetch('/system/info');
    expect(new Headers(seen.headers).get('X-OmniVoice-Pin')).toBeNull();
  });

  it('keeps cookie and loopback requests usable when Web Storage is blocked', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    globalThis.fetch = fetchMock as any;

    try {
      const { apiFetch } = await import('./client');
      await expect(apiFetch('/system/info')).resolves.toMatchObject({ ok: true });
    } finally {
      getItem.mockRestore();
    }

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('X-OmniVoice-Pin')).toBeNull();
    expect(headers.get('Authorization')).toBeNull();
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('include');
  });

  it('turns a thrown fetch into an actionable ApiError (backend unreachable)', async () => {
    // Backend down / still starting → fetch() rejects with a TypeError.
    globalThis.fetch = vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))) as any;
    const { apiFetch, ApiError } = await import('./client');
    let err: any;
    try {
      await apiFetch('/system/info');
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0); // transport failure, not HTTP
    expect(String(err.message)).toMatch(/reach the local VoiceStudio backend/i);
    // #1164: the detail is now structured diagnostics — the transport cause
    // is preserved, plus mode/last-contact for the bug-report prefill.
    expect(String(err.detail.transport)).toMatch(/Failed to fetch/);
    expect(err.detail).toMatchObject({ mode: 'dev', attempts: 4 });
  });
});

describe('apiFetch short-lived admin authentication', () => {
  let realFetch: typeof globalThis.fetch;

  beforeEach(() => {
    realFetch = globalThis.fetch;
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    sessionStorage.clear();
    localStorage.clear();
  });

  it('attaches only the backend-bound short-lived session, never the persisted master', async () => {
    const session = `ovs_admin_session_${'S'.repeat(43)}`;
    localStorage.setItem('ov_api_key', 'legacy-master');
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({ token: session, expiresAt: Date.now() / 1000 + 3600, apiBase: API }),
    );
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    globalThis.fetch = fetchMock as any;

    const { apiFetch } = await import('./client');
    await apiFetch('/system/info');

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get('Authorization')).toBe(`Bearer ${session}`);
    expect(headers.get('Authorization')).not.toContain('legacy-master');
  });

  it('builds credential-free legacy WebSocket URLs even if old storage is populated', () => {
    localStorage.setItem('ov_api_key', 'legacy-master');
    const url = wsUrl('/ws/events?view=active');
    expect(url).toContain('/ws/events?view=active');
    expect(url).not.toContain('api_key');
    expect(url).not.toContain('legacy-master');
  });

  it('never sends backend credentials to an absolute foreign URL', async () => {
    const session = `ovs_admin_session_${'S'.repeat(43)}`;
    sessionStorage.setItem('ov_pin', '424242');
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({ token: session, expiresAt: Date.now() / 1000 + 3600, apiBase: API }),
    );
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    globalThis.fetch = fetchMock as any;

    const { apiFetch } = await import('./client');
    await apiFetch('https://voice.example.evil.test/public.wav', {
      credentials: 'omit',
      headers: { 'X-Public-Media': '1' },
    });

    const [target, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(target).toBe('https://voice.example.evil.test/public.wav');
    expect(headers.get('Authorization')).toBeNull();
    expect(headers.get('X-OmniVoice-Pin')).toBeNull();
    expect(headers.get(CSRF_HEADER_NAME)).toBeNull();
    expect(headers.get('X-Public-Media')).toBe('1');
    expect(init?.credentials).toBe('omit');
  });

  it('binds credentials to the exact configured API path prefix', () => {
    expect(_isApiTarget('https://voice.test/studio/v1/audio', 'https://voice.test/studio')).toBe(
      true,
    );
    expect(_isApiTarget('https://voice.test/studio-evil/v1', 'https://voice.test/studio')).toBe(
      false,
    );
    expect(_isApiTarget('https://voice.test/other', 'https://voice.test/studio')).toBe(false);
    expect(_isApiTarget('https://voice.test.evil/v1', 'https://voice.test')).toBe(false);
  });
});

describe('apiFetch 401 routing', () => {
  // The backend has two 401-returning middlewares distinguished only by their
  // `detail` body: "API key required" (BearerKeyMiddleware) vs "PIN required"
  // (NetworkAccessMiddleware). apiFetch reads the detail and dispatches a single
  // `ov:auth-required` CustomEvent whose `detail.mode` tells the gate which form.
  let realFetch: typeof globalThis.fetch;
  let dispatch: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    realFetch = globalThis.fetch;
    sessionStorage.clear();
    localStorage.clear();
    dispatch = vi.spyOn(window, 'dispatchEvent');
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    sessionStorage.clear();
    localStorage.clear();
    dispatch.mockRestore();
  });

  const stubStatus = (status: number, statusText: string, detail: string) =>
    vi.fn(() =>
      Promise.resolve({
        ok: false,
        status,
        statusText,
        text: async () => JSON.stringify({ detail }),
      }),
    ) as any;
  const stub401 = (detail: string) => stubStatus(401, 'Unauthorized', detail);

  const authEvent = () =>
    dispatch.mock.calls.map((c) => c[0]).find((e) => (e as Event).type === 'ov:auth-required');

  it('dispatches ov:auth-required {mode:"apikey"} on an "API key required" 401', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: `ovs_admin_session_${'S'.repeat(43)}`,
        expiresAt: Date.now() / 1000 + 3600,
        apiBase: API,
      }),
    );
    globalThis.fetch = stub401('API key required');
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/system/info');
    } catch {
      /* ApiError expected */
    }
    expect(authEvent()).toBeTruthy();
    expect((authEvent() as any).detail.mode).toBe('apikey');
    expect(sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('dispatches ov:auth-required {mode:"pin"} on a "PIN required" 401', async () => {
    globalThis.fetch = stub401('PIN required');
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/system/info');
    } catch {
      /* ApiError expected */
    }
    expect(authEvent()).toBeTruthy();
    expect((authEvent() as any).detail.mode).toBe('pin');
  });

  const stub403 = (detail: string) => stubStatus(403, 'Forbidden', detail);

  it('dispatches ov:auth-required {mode:"apikey"} on an admin-gate 403 (#1525)', async () => {
    globalThis.fetch = stub403('loopback origin or admin API key required');
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/system/info');
    } catch {
      /* ApiError expected */
    }
    expect(authEvent()).toBeTruthy();
    expect((authEvent() as any).detail.mode).toBe('apikey');
  });

  it('does not dispatch ov:auth-required on other 403s (CSRF / desktop-only)', async () => {
    globalThis.fetch = stub403('browser origin rejected');
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/system/info');
    } catch {
      /* ApiError expected */
    }
    expect(authEvent()).toBeFalsy();
  });

  it('a stale 403 neither clears a new session nor reopens the auth gate (PR #1569 race)', async () => {
    // The request goes out with an old credential; while it is in flight the
    // user completes another key exchange. A late 403 may only invalidate the
    // credentials the failed request actually carried — wiping the fresh
    // session or reopening the gate would undo the successful login.
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: `ovs_admin_session_${'O'.repeat(43)}`,
        expiresAt: Date.now() / 1000 + 3600,
        apiBase: API,
      }),
    );
    globalThis.fetch = vi.fn(() => {
      sessionStorage.setItem(
        ADMIN_SESSION_STORAGE_KEY,
        JSON.stringify({
          token: `ovs_admin_session_${'N'.repeat(43)}`,
          expiresAt: Date.now() / 1000 + 3600,
          apiBase: API,
        }),
      );
      return Promise.resolve({
        ok: false,
        status: 403,
        statusText: 'Forbidden',
        text: async () => JSON.stringify({ detail: 'loopback origin or admin API key required' }),
      });
    }) as any;
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/system/info');
    } catch {
      /* ApiError expected */
    }
    expect(authEvent()).toBeFalsy();
    expect(sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY)).not.toBeNull();
  });
});

describe('apiFetch 404 from a non-VoiceStudio server (#1385)', () => {
  // A rehosted UI (static host, reverse proxy) whose API requests land on the
  // wrong host gets that host's 404 page back. Echoing it ("NOT_FOUND
  // bom1::…") is useless — the error must say where requests are going.
  let realFetch: typeof globalThis.fetch;
  beforeEach(() => {
    realFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  const stub404 = (body: string, headers: Record<string, string> = {}) =>
    vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        headers: { get: (k: string) => headers[k.toLowerCase()] ?? null },
        text: async () => body,
      }),
    ) as any;

  const thrownBy = async (body: string, headers?: Record<string, string>) => {
    globalThis.fetch = stub404(body, headers);
    const { apiFetch } = await import('./client');
    try {
      await apiFetch('/generate');
    } catch (e) {
      return e as Error;
    }
    throw new Error('expected apiFetch to throw');
  };

  it('names the misrouted host instead of echoing its 404 page', async () => {
    const err = await thrownBy(
      'The page could not be found\n\nNOT_FOUND\n\nbom1::2lhsl-1786000655272',
    );
    expect(err.message).toMatch(/not a VoiceStudio backend/);
    expect(err.message).toMatch(/Backend URL in Settings/);
    expect(err.message).not.toMatch(/bom1/);
  });

  it("keeps the plain message for the backend's own JSON 404", async () => {
    const err = await thrownBy(JSON.stringify({ detail: 'Voice not found' }));
    expect(err.message).toMatch(/404 Not Found: Voice not found/);
    expect(err.message).not.toMatch(/not a VoiceStudio backend/);
  });

  it('treats Starlette StaticFiles\' plain-text "Not Found" as the backend speaking', async () => {
    // Mounted StaticFiles apps answer text/plain "Not Found" — the one
    // non-JSON 404 the backend itself produces. Not a routing problem.
    const err = await thrownBy('Not Found');
    expect(err.message).not.toMatch(/not a VoiceStudio backend/);
  });

  it("believes the backend's marker header over any body shape", async () => {
    // The header is what makes this authoritative rather than a guess: a
    // backend 404 whose body looks like nothing we recognise is still a
    // backend 404.
    const err = await thrownBy('<html>weird proxy rewrite</html>', {
      'x-omnivoice-backend': '0.4.3',
    });
    expect(err.message).not.toMatch(/not a VoiceStudio backend/);
  });

  it('is not fooled by a proxy that imitates a JSON error body', async () => {
    // CodeRabbit: an unmarked `{"error": …}` 404 is a foreign server — the
    // backend never answers 404 with an `error` key, only `detail`.
    const err = await thrownBy(JSON.stringify({ error: 'Not Found' }));
    expect(err.message).toMatch(/not a VoiceStudio backend/);
  });
});

describe('_parseDeepLinkCredentials', () => {
  it('reads the API key from the fragment (not the query) and scrubs it', () => {
    const r = _parseDeepLinkCredentials('https://h:3900/#api_key=SECRET');
    expect(r.apiKey).toBe('SECRET');
    expect(r.pin).toBeNull();
    expect(r.scrubbed).toBe(true);
    expect(r.cleanUrl).toBe('/');
  });

  it('reads the PIN from the query and scrubs it', () => {
    const r = _parseDeepLinkCredentials('https://h/?pin=1234');
    expect(r.pin).toBe('1234');
    expect(r.apiKey).toBeNull();
    expect(r.cleanUrl).toBe('/');
  });

  it('scrubs a legacy ?api_key= from the query WITHOUT reading it (no leak)', () => {
    const r = _parseDeepLinkCredentials('https://h/?api_key=LEGACY');
    expect(r.apiKey).toBeNull();
    expect(r.scrubbed).toBe(true);
    expect(r.cleanUrl).toBe('/');
  });

  it('preserves other query state and a bare #settings fragment when no api_key is consumed', () => {
    const r = _parseDeepLinkCredentials('https://h/?pin=1&lang=fr#settings');
    expect(r.pin).toBe('1');
    expect(r.apiKey).toBeNull();
    expect(r.cleanUrl).toBe('/?lang=fr#settings');
  });

  it('preserves other fragment params alongside api_key', () => {
    const r = _parseDeepLinkCredentials('https://h/#api_key=S&theme=dark');
    expect(r.apiKey).toBe('S');
    expect(r.cleanUrl).toBe('/#theme=dark');
  });

  it('reports scrubbed=false and leaves the URL intact when no credential is present', () => {
    const r = _parseDeepLinkCredentials('https://h/path?page=2#top');
    expect(r.pin).toBeNull();
    expect(r.apiKey).toBeNull();
    expect(r.scrubbed).toBe(false);
    expect(r.cleanUrl).toBe('/path?page=2#top');
  });
});

describe('_bootstrapBrowserCredentials', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('scrubs the fragment before exchanging exactly once, deleting legacy storage on success', async () => {
    const order: string[] = [];
    localStorage.setItem('ov_api_key', 'older-master');
    const win = {
      location: { href: 'https://voice.test/app?pin=1234#api_key=fragment-master&tab=voices' },
      history: {
        replaceState: (_data: unknown, _unused: string, url?: string | URL | null) => {
          order.push(`scrub:${String(url)}`);
        },
      },
    };
    const exchange = vi.fn(async (master) => {
      order.push('exchange');
      expect(master).toBe('fragment-master');
      // The durable key survives until the backend accepts the exchange — a
      // failure at this point must leave it for the next launch to retry.
      expect(localStorage.getItem('ov_api_key')).toBe('older-master');
    });

    await _bootstrapBrowserCredentials(win, {
      apiBase: 'https://voice.test',
      exchange: exchange as any,
    });

    expect(sessionStorage.getItem('ov_pin')).toBe('1234');
    expect(order).toEqual(['scrub:/app#tab=voices', 'exchange']);
    expect(exchange).toHaveBeenCalledOnce();
    expect(localStorage.getItem('ov_api_key')).toBeNull();
  });

  it('retains the stored master when the backend is unreachable (no stranding)', async () => {
    // The upgrade-day disaster this guards against: a remote-backend user's
    // only copy of OMNIVOICE_API_KEY lives in localStorage, and the backend is
    // down at first launch. The failed exchange must NOT consume the key.
    localStorage.setItem('ov_api_key', 'legacy-master');
    const exchange = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(
      _bootstrapBrowserCredentials(
        { location: { href: 'https://voice.test/' }, history: { replaceState: vi.fn() } },
        { apiBase: 'https://voice.test', exchange },
      ),
    ).rejects.toThrow();

    expect(exchange).toHaveBeenCalledWith('legacy-master', { apiBase: 'https://voice.test' });
    expect(localStorage.getItem('ov_api_key')).toBe('legacy-master');
  });

  it('re-runs the migration on the next launch and consumes the key once it succeeds', async () => {
    localStorage.setItem('ov_api_key', 'legacy-master');
    const exchange = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ transport: 'bearer', expiresAt: Date.now() / 1000 + 60 });
    const launch = () =>
      _bootstrapBrowserCredentials(
        { location: { href: 'https://voice.test/' }, history: { replaceState: vi.fn() } },
        { apiBase: 'https://voice.test', exchange },
      );

    // Launch 1: backend unreachable — key survives.
    await expect(launch()).rejects.toThrow();
    expect(localStorage.getItem('ov_api_key')).toBe('legacy-master');

    // Launch 2: backend back — the retained key is retried and then removed.
    await launch();
    expect(exchange).toHaveBeenNthCalledWith(2, 'legacy-master', {
      apiBase: 'https://voice.test',
    });
    expect(localStorage.getItem('ov_api_key')).toBeNull();
  });

  it('consumes a legacy stored master without writing it anywhere else', async () => {
    localStorage.setItem('ov_api_key', 'legacy-master');
    const exchange = vi.fn().mockResolvedValue({ transport: 'bearer' });

    await _bootstrapBrowserCredentials(
      {
        location: { href: 'https://voice.test/' },
        history: { replaceState: vi.fn() },
      },
      { apiBase: 'https://voice.test', exchange },
    );

    expect(exchange).toHaveBeenCalledWith('legacy-master', { apiBase: 'https://voice.test' });
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(sessionStorage.getItem('ov_api_key')).toBeNull();
  });

  it('still deletes and exchanges the master when PIN session storage is blocked', async () => {
    localStorage.setItem('ov_api_key', 'legacy-master');
    const replaceState = vi.fn();
    const exchange = vi.fn().mockResolvedValue({ transport: 'bearer' });

    await _bootstrapBrowserCredentials(
      {
        location: { href: 'https://voice.test/?pin=1234#api_key=fragment-master' },
        history: { replaceState },
      },
      {
        apiBase: 'https://voice.test',
        sessionStore: {
          setItem: vi.fn(() => {
            throw new DOMException('blocked');
          }),
        },
        exchange,
      },
    );

    expect(replaceState).toHaveBeenCalledWith(null, '', '/');
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(exchange).toHaveBeenCalledWith('fragment-master', {
      apiBase: 'https://voice.test',
    });
  });
});
