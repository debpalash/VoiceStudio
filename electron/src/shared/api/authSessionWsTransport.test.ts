import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ADMIN_SESSION_STORAGE_KEY, AuthSessionError, authenticatedWsUrl } from './authSession';

const SESSION = `ovs_admin_session_${'A'.repeat(43)}`;
const TICKET = `ovs_ws_ticket_${'B'.repeat(43)}`;
const NOW_SECONDS = 1_800_000_000;

const response = () =>
  new Response(JSON.stringify({ ticket: TICKET, expires_at: NOW_SECONDS + 30 }), {
    status: 201,
    headers: { 'content-type': 'application/json' },
  });

const storeSession = (apiBase: string) => {
  sessionStorage.setItem(
    ADMIN_SESSION_STORAGE_KEY,
    JSON.stringify({ token: SESSION, expiresAt: NOW_SECONDS + 3600, apiBase }),
  );
};

describe('ticketed WebSocket transport', () => {
  beforeEach(() => sessionStorage.clear());

  it('mints a path-bound /ws/tts ticket for the live dub preview (#1769)', async () => {
    const apiBase = 'https://gpu.test:3900';
    storeSession(apiBase);
    const fetchImpl = vi.fn().mockResolvedValue(response());

    await expect(
      authenticatedWsUrl('/ws/tts', { apiBase, fetchImpl, now: () => NOW_SECONDS * 1000 }),
    ).resolves.toBe(`wss://gpu.test:3900/ws/tts?ws_ticket=${TICKET}`);
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({ path: '/ws/tts' });
  });

  it.each([
    'http://gpu-box.your-tailnet.ts.net:3900', // docs/remote-gpu.md tailnet flow
    'http://192.168.1.20:3900', // LAN Docker host
    'http://127.0.0.2:3900',
  ])('keeps ticketing the plaintext non-loopback bases the docs support: %s', async (apiBase) => {
    // The bearer session that mints the ticket already crossed this same
    // transport; refusing plaintext here would only cut /ws/events and
    // /ws/transcribe off for every documented remote-backend user.
    storeSession(apiBase);
    const fetchImpl = vi.fn().mockResolvedValue(response());

    await expect(
      authenticatedWsUrl('/ws/tts', { apiBase, fetchImpl, now: () => NOW_SECONDS * 1000 }),
    ).resolves.toBe(`${apiBase.replace('http:', 'ws:')}/ws/tts?ws_ticket=${TICKET}`);
  });

  it('returns a credential-free URL when no admin session exists (loopback desktop)', async () => {
    const fetchImpl = vi.fn();
    await expect(
      authenticatedWsUrl('/ws/tts', { apiBase: 'http://127.0.0.1:3900', fetchImpl }),
    ).resolves.toBe('ws://127.0.0.1:3900/ws/tts');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('refuses paths outside the ticket allowlist before any network call', async () => {
    const apiBase = 'https://gpu.test:3900';
    storeSession(apiBase);
    const fetchImpl = vi.fn().mockResolvedValue(response());

    await expect(
      authenticatedWsUrl('/ws/anything', { apiBase, fetchImpl, now: () => NOW_SECONDS * 1000 }),
    ).rejects.toBeInstanceOf(AuthSessionError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
