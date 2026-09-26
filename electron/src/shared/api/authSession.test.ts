import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ADMIN_SESSION_STORAGE_KEY,
  AuthSessionError,
  LEGACY_API_KEY_STORAGE_KEY,
  authenticatedWsUrl,
  clearAdminSession,
  exchangeApiKey,
  getAdminSession,
  isSameOriginApi,
  requestWebSocketTicket,
  revokeAdminSession,
} from './authSession';

const SESSION = `ovs_admin_session_${'A'.repeat(43)}`;
const TICKET = `ovs_ws_ticket_${'B'.repeat(43)}`;
const MASTER = 'master-must-never-persist';
const NOW_SECONDS = 1_800_000_000;

const response = (body: unknown, status = 201) =>
  new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: body === null ? undefined : { 'content-type': 'application/json' },
  });

const sameOriginWindow = {
  location: { origin: 'https://voice.test' },
  dispatchEvent: vi.fn(),
};

const crossOriginWindow = {
  location: { origin: 'tauri://localhost' },
  __TAURI_INTERNALS__: {},
  dispatchEvent: vi.fn(),
};

describe('short-lived admin session client', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('selects cookie transport only for an exact same-origin HTTP API', () => {
    expect(isSameOriginApi('https://voice.test', sameOriginWindow)).toBe(true);
    expect(isSameOriginApi('https://voice.test:444', sameOriginWindow)).toBe(false);
    expect(isSameOriginApi('http://voice.test', sameOriginWindow)).toBe(false);
    expect(isSameOriginApi('https://voice.test.evil.test', sameOriginWindow)).toBe(false);
    expect(isSameOriginApi('http://127.0.0.1:3900', crossOriginWindow)).toBe(false);
  });

  it('exchanges a same-origin master for an HttpOnly cookie without persisting any token', async () => {
    localStorage.setItem(LEGACY_API_KEY_STORAGE_KEY, MASTER);
    const fetchImpl = vi.fn().mockResolvedValue(response(null, 204));

    await expect(
      exchangeApiKey(MASTER, {
        apiBase: 'https://voice.test/',
        fetchImpl,
        windowLike: sameOriginWindow,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toEqual({ transport: 'cookie' });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://voice.test/api/auth/session',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        cache: 'no-store',
        referrerPolicy: 'no-referrer',
        headers: expect.objectContaining({ Authorization: `Bearer ${MASTER}` }),
        body: JSON.stringify({ transport: 'cookie' }),
      }),
    );
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });

  it('stores only a backend-bound short-lived bearer session for cross-origin clients', async () => {
    localStorage.setItem(LEGACY_API_KEY_STORAGE_KEY, MASTER);
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(response({ token: SESSION, expires_at: NOW_SECONDS + 3600 }));

    await expect(
      exchangeApiKey(MASTER, {
        apiBase: 'https://gpu.test:3900/',
        fetchImpl,
        windowLike: crossOriginWindow,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toEqual({ transport: 'bearer', expiresAt: NOW_SECONDS + 3600 });

    const persisted = sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY) ?? '';
    expect(persisted).toContain(SESSION);
    expect(persisted).toContain('https://gpu.test:3900');
    expect(persisted).not.toContain(MASTER);
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBeNull();
    expect(getAdminSession('https://gpu.test:3900', { now: () => NOW_SECONDS * 1000 })).toEqual({
      token: SESSION,
      expiresAt: NOW_SECONDS + 3600,
      apiBase: 'https://gpu.test:3900',
    });
  });

  it('uses relative lifetime when remote and browser clocks are not synchronized', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      response({
        token: SESSION,
        expires_at: 1,
        expires_in: 3600,
      }),
    );

    await expect(
      exchangeApiKey(MASTER, {
        apiBase: 'https://gpu.test:3900',
        fetchImpl,
        windowLike: crossOriginWindow,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toEqual({ transport: 'bearer', expiresAt: NOW_SECONDS + 3600 });

    expect(getAdminSession('https://gpu.test:3900', { now: () => NOW_SECONDS * 1000 })).toEqual({
      token: SESSION,
      expiresAt: NOW_SECONDS + 3600,
      apiBase: 'https://gpu.test:3900',
    });
  });

  it('retains the legacy master while the exchange is pending and removes it on success', async () => {
    localStorage.setItem(LEGACY_API_KEY_STORAGE_KEY, MASTER);
    let resolveFetch: (value: Response) => void = () => {};
    const fetchImpl = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    const pending = exchangeApiKey(MASTER, {
      apiBase: 'https://gpu.test:3900',
      fetchImpl,
      windowLike: crossOriginWindow,
      now: () => NOW_SECONDS * 1000,
    });

    // Not yet: only a session that actually exists may consume the stored key.
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBe(MASTER);
    resolveFetch(response({ token: SESSION, expires_at: NOW_SECONDS + 3600 }));
    await pending;
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBeNull();
  });

  it('never retries a failed exchange and exposes no master or response body in its error', async () => {
    localStorage.setItem(LEGACY_API_KEY_STORAGE_KEY, MASTER);
    const reflected = `invalid credential: ${MASTER}`;
    const fetchImpl = vi.fn().mockResolvedValue(response({ detail: reflected }, 401));

    const error = await exchangeApiKey(MASTER, {
      apiBase: 'https://gpu.test:3900',
      fetchImpl,
      windowLike: crossOriginWindow,
      now: () => NOW_SECONDS * 1000,
    }).catch((value) => value);

    expect(error).toBeInstanceOf(AuthSessionError);
    expect(error.status).toBe(401);
    expect(String(error)).not.toContain(MASTER);
    expect(String(error)).not.toContain(reflected);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(sessionStorage.length).toBe(0);
    // A failed exchange leaves the durable key for the next launch's retry.
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBe(MASTER);
  });

  it('bounds a hung exchange and retains the durable master for the next migration attempt', async () => {
    vi.useFakeTimers();
    localStorage.setItem(LEGACY_API_KEY_STORAGE_KEY, MASTER);
    const fetchImpl = vi.fn(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );

    const pending = exchangeApiKey(MASTER, {
      apiBase: 'https://gpu.test:3900',
      fetchImpl: fetchImpl as typeof fetch,
      windowLike: crossOriginWindow,
      timeoutMs: 25,
    });
    const observed = pending.catch((error) => error);
    await vi.advanceTimersByTimeAsync(25);

    expect(await observed).toBeInstanceOf(AuthSessionError);
    expect(fetchImpl).toHaveBeenCalledOnce();
    // Unreachable/hung backend: the stored copy is the user's only copy.
    expect(localStorage.getItem(LEGACY_API_KEY_STORAGE_KEY)).toBe(MASTER);
    expect(sessionStorage.length).toBe(0);
    vi.useRealTimers();
  });

  it.each([
    [{ token: MASTER, expires_at: NOW_SECONDS + 3600 }, 'master-shaped token'],
    [{ token: SESSION, expires_at: NOW_SECONDS - 1 }, 'expired session'],
    [{ token: SESSION, expires_at: NOW_SECONDS + 40_000 }, 'implausible expiry'],
    [{ token: SESSION }, 'missing expiry'],
    [null, 'missing body'],
  ])('rejects and does not persist a malformed bearer response: %s (%s)', async (body, _label) => {
    const fetchImpl = vi.fn().mockResolvedValue(response(body));

    await expect(
      exchangeApiKey(MASTER, {
        apiBase: 'https://gpu.test:3900',
        fetchImpl,
        windowLike: crossOriginWindow,
        now: () => NOW_SECONDS * 1000,
      }),
    ).rejects.toBeInstanceOf(AuthSessionError);
    expect(sessionStorage.length).toBe(0);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 40_000, '3600'])(
    'rejects an invalid relative session lifetime: %s',
    async (expiresIn) => {
      const fetchImpl = vi.fn().mockResolvedValue(
        response({
          token: SESSION,
          expires_at: NOW_SECONDS + 3600,
          expires_in: expiresIn,
        }),
      );

      await expect(
        exchangeApiKey(MASTER, {
          apiBase: 'https://gpu.test:3900',
          fetchImpl,
          windowLike: crossOriginWindow,
          now: () => NOW_SECONDS * 1000,
        }),
      ).rejects.toBeInstanceOf(AuthSessionError);
      expect(sessionStorage.length).toBe(0);
    },
  );

  it('rejects oversized bearer responses before parsing them', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response('x'.repeat(20_000), {
        status: 201,
        headers: { 'content-length': '20000' },
      }),
    );

    await expect(
      exchangeApiKey(MASTER, {
        apiBase: 'https://gpu.test:3900',
        fetchImpl,
        windowLike: crossOriginWindow,
        now: () => NOW_SECONDS * 1000,
      }),
    ).rejects.toBeInstanceOf(AuthSessionError);
    expect(sessionStorage.length).toBe(0);
  });

  it('drops malformed, expired, or wrong-backend session storage', () => {
    sessionStorage.setItem(ADMIN_SESSION_STORAGE_KEY, '{bad json');
    expect(getAdminSession('https://gpu.test', { now: () => NOW_SECONDS * 1000 })).toBeNull();

    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({ token: SESSION, expiresAt: NOW_SECONDS - 1, apiBase: 'https://gpu.test' }),
    );
    expect(getAdminSession('https://gpu.test', { now: () => NOW_SECONDS * 1000 })).toBeNull();

    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 10,
        apiBase: 'https://other.test',
      }),
    );
    expect(getAdminSession('https://gpu.test', { now: () => NOW_SECONDS * 1000 })).toBeNull();

    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 40_000,
        apiBase: 'https://gpu.test',
      }),
    );
    expect(getAdminSession('https://gpu.test', { now: () => NOW_SECONDS * 1000 })).toBeNull();
    expect(sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('mints a path-bound WebSocket ticket with the session only in an HTTP header', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(response({ ticket: TICKET, expires_at: NOW_SECONDS + 30 }));

    await expect(
      requestWebSocketTicket('/ws/transcribe?model=live', {
        apiBase: 'https://gpu.test:3900',
        fetchImpl,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toBe(TICKET);

    expect(fetchImpl).toHaveBeenCalledWith(
      'https://gpu.test:3900/api/auth/ws-ticket',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: `Bearer ${SESSION}` }),
        body: JSON.stringify({ path: '/ws/transcribe' }),
      }),
    );
    expect(JSON.stringify(fetchImpl.mock.calls[0][0])).not.toContain(SESSION);
  });

  it('accepts a ticket lifetime independent of server wall-clock skew', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(response({ ticket: TICKET, expires_at: 1, expires_in: 30 }));

    await expect(
      requestWebSocketTicket('/ws/events', {
        apiBase: 'https://gpu.test:3900',
        fetchImpl,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toBe(TICKET);
  });

  it('places only the one-use ticket in a bearer-authenticated WebSocket URL', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(response({ ticket: TICKET, expires_at: NOW_SECONDS + 30 }));

    const url = await authenticatedWsUrl('/ws/transcribe?model=live&api_key=legacy', {
      apiBase: 'https://gpu.test:3900',
      fetchImpl,
      now: () => NOW_SECONDS * 1000,
    });

    expect(url).toBe(`wss://gpu.test:3900/ws/transcribe?model=live&ws_ticket=${TICKET}`);
    expect(url).not.toContain(SESSION);
    expect(url).not.toContain(MASTER);
    expect(url).not.toContain('api_key');
  });

  it('preserves a reverse-proxy base path while binding the ticket to the logical WS route', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test/studio',
      }),
    );
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(response({ ticket: TICKET, expires_in: 30, expires_at: 1 }));

    await expect(
      authenticatedWsUrl('/ws/events?view=active', {
        apiBase: 'https://gpu.test/studio',
        fetchImpl,
        now: () => NOW_SECONDS * 1000,
      }),
    ).resolves.toBe(`wss://gpu.test/studio/ws/events?view=active&ws_ticket=${TICKET}`);
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://gpu.test/studio/api/auth/ws-ticket',
      expect.objectContaining({ body: JSON.stringify({ path: '/ws/events' }) }),
    );
  });

  it.each(['/ws/events/../admin', '//evil.test/ws/events', 'https://gpu.test/ws/events'])(
    'rejects a non-canonical WebSocket target: %s',
    async (path) => {
      await expect(
        authenticatedWsUrl(path, { apiBase: 'https://gpu.test', fetchImpl: vi.fn() }),
      ).rejects.toBeInstanceOf(AuthSessionError);
    },
  );

  it('requests a fresh ticket for every WebSocket connection attempt', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    const secondTicket = `ovs_ws_ticket_${'C'.repeat(43)}`;
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(response({ ticket: TICKET, expires_at: NOW_SECONDS + 30 }))
      .mockResolvedValueOnce(response({ ticket: secondTicket, expires_at: NOW_SECONDS + 30 }));

    const first = await authenticatedWsUrl('/ws/events', {
      apiBase: 'https://gpu.test:3900',
      fetchImpl,
      now: () => NOW_SECONDS * 1000,
    });
    const second = await authenticatedWsUrl('/ws/events', {
      apiBase: 'https://gpu.test:3900',
      fetchImpl,
      now: () => NOW_SECONDS * 1000,
    });

    expect(first).toContain(TICKET);
    expect(second).toContain(secondTicket);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('uses a credential-free WebSocket URL when no bearer session exists', async () => {
    const fetchImpl = vi.fn();
    await expect(
      authenticatedWsUrl('/ws/events?api_key=must-be-removed', {
        apiBase: 'http://127.0.0.1:3900',
        fetchImpl,
      }),
    ).resolves.toBe('ws://127.0.0.1:3900/ws/events');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('clears an invalid session and raises the auth gate when ticket issuance is rejected', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    const windowLike = { ...crossOriginWindow, dispatchEvent: vi.fn() };

    await expect(
      requestWebSocketTicket('/ws/events', {
        apiBase: 'https://gpu.test:3900',
        fetchImpl: vi.fn().mockResolvedValue(response({ detail: 'expired' }, 401)),
        windowLike,
        now: () => NOW_SECONDS * 1000,
      }),
    ).rejects.toBeInstanceOf(AuthSessionError);

    expect(getAdminSession('https://gpu.test:3900', { now: () => NOW_SECONDS * 1000 })).toBeNull();
    expect(windowLike.dispatchEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'ov:auth-required' }),
    );
  });

  it('clears session state idempotently without touching unrelated storage', () => {
    sessionStorage.setItem(ADMIN_SESSION_STORAGE_KEY, 'value');
    sessionStorage.setItem('unrelated', 'keep');
    clearAdminSession();
    clearAdminSession();
    expect(sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem('unrelated')).toBe('keep');
  });

  it('revokes a bearer session while clearing local state before the request settles', async () => {
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({
        token: SESSION,
        expiresAt: NOW_SECONDS + 3600,
        apiBase: 'https://gpu.test:3900',
      }),
    );
    let resolveFetch: (response: Response) => void = () => {};
    const fetchImpl = vi.fn(() => new Promise<Response>((resolve) => (resolveFetch = resolve)));

    const pending = revokeAdminSession('https://gpu.test:3900', {
      fetchImpl,
      now: () => NOW_SECONDS * 1000,
    });

    expect(sessionStorage.getItem(ADMIN_SESSION_STORAGE_KEY)).toBeNull();
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://gpu.test:3900/api/auth/session',
      expect.objectContaining({
        method: 'DELETE',
        headers: expect.objectContaining({ Authorization: `Bearer ${SESSION}` }),
        credentials: 'include',
      }),
    );
    resolveFetch(response(null, 204));
    await expect(pending).resolves.toBe(true);
  });

  it('revokes a same-origin cookie session with the CSRF marker', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(response(null, 204));

    await expect(
      revokeAdminSession('https://voice.test', {
        fetchImpl,
        windowLike: sameOriginWindow,
      }),
    ).resolves.toBe(true);

    expect(fetchImpl).toHaveBeenCalledWith(
      'https://voice.test/api/auth/session',
      expect.objectContaining({
        headers: { 'X-VoiceStudio-CSRF': '1' },
        credentials: 'include',
      }),
    );
  });
});
