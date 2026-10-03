// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { createServer, type ServerResponse } from 'node:http';
import { once } from 'node:events';
import { normalizeRemoteUrl, probeRemoteBackend, remoteWebSocketUrl } from './remote-backend';

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...init.headers },
  });
}

it.each(['/ws/transcribe', '/ws/events', '/ws/tts'] as const)(
  'preserves remote base prefixes and canonical ticket binding for %s',
  async (path) => {
    for (const base of ['https://gpu-box:3900/voice', 'https://gpu-box:3900/nested/voice/']) {
      const session = { token: `ovs_admin_session_${'a'.repeat(43)}`, expiresAt: 3601 };
      const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        expect(String(input)).toBe(`${base.replace(/\/+$/, '')}/api/auth/ws-ticket`);
        expect(init?.body).toBe(JSON.stringify({ path }));
        expect(new Headers(init?.headers).get('authorization')).toBe(`Bearer ${session.token}`);
        return json({ ticket: `ovs_ws_ticket_${'b'.repeat(43)}`, expires_in: 30 }, { status: 201 });
      });
      const expected = `wss://gpu-box:3900${new URL(base).pathname.replace(/\/+$/, '')}${path}`;
      const anonymous = await remoteWebSocketUrl(base, path, null);
      expect(anonymous).toBe(expected);
      const authorized = new URL(await remoteWebSocketUrl(base, path, session, {
        fetcher, now: () => 1000,
      }));
      expect(authorized.origin + authorized.pathname).toBe(expected);
      expect([...authorized.searchParams.keys()]).toEqual(['ws_ticket']);
      expect(authorized.searchParams.get('ws_ticket')).toBe(`ovs_ws_ticket_${'b'.repeat(43)}`);
      expect(authorized.href).not.toContain(session.token);
    }
    expect(await remoteWebSocketUrl('http://gpu-box:3900/voice', path, null)).toBe(
      `ws://gpu-box:3900/voice${path}`,
    );
  },
);

it('accepts only credential-free absolute HTTP backend bases', () => {
  expect(normalizeRemoteUrl(' https://gpu-box:3900/ ')).toBe('https://gpu-box:3900');
  for (const value of [
    '',
    'gpu-box:3900',
    'file:///tmp/server',
    'https://user:secret@gpu-box:3900',
    'https://gpu-box:3900?key=secret',
    'https://gpu-box:3900/#secret',
  ]) {
    expect(() => normalizeRemoteUrl(value)).toThrow();
  }
});

it('verifies both VoiceStudio identity and usable API access', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith('/health')) return json({ status: 'ok', version: '0.5.2', device: 'cuda' });
    if (url.endsWith('/system/info')) return json({ app_version: '0.5.2' });
    throw new Error(`unexpected ${url}`);
  });
  const result = await probeRemoteBackend('http://gpu-box:3900', '', { fetcher });
  expect(result).toMatchObject({
    ok: true,
    target: 'http://gpu-box:3900',
    detail: '0.5.2 on cuda',
  });
  expect(result.ok && result.session).toBeNull();
});

it('exchanges the master once and uses only the scoped session afterwards', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/health')) {
      expect(new Headers(init?.headers).has('authorization')).toBe(false);
      return json({ status: 'ok', version: '0.5.2', device: 'mps' });
    }
    if (url.endsWith('/api/auth/session')) {
      expect(new Headers(init?.headers).get('authorization')).toBe('Bearer master-secret');
      return json(
        {
          token: `ovs_admin_session_${'a'.repeat(43)}`,
          expires_in: 3600,
        },
        { status: 201 },
      );
    }
    if (url.endsWith('/system/info')) {
      expect(new Headers(init?.headers).get('authorization')).toBe(
        `Bearer ovs_admin_session_${'a'.repeat(43)}`,
      );
      return json({ app_version: '0.5.2' });
    }
    throw new Error(`unexpected ${url}`);
  });
  const result = await probeRemoteBackend('https://gpu-box:3900', 'master-secret', {
    fetcher,
    now: () => 1_000,
  });
  expect(result.ok && result.session).toEqual({
    token: `ovs_admin_session_${'a'.repeat(43)}`,
    expiresAt: 3_601,
  });
  expect(JSON.stringify(result)).not.toContain('master-secret');
});

it('distinguishes a missing key from an unrelated HTTP failure', async () => {
  const health = () => json({ status: 'ok', version: '0.5.2', device: 'cpu' });
  const authRequired = await probeRemoteBackend('http://gpu-box:3900', '', {
    fetcher: vi
      .fn()
      .mockImplementationOnce(health)
      .mockResolvedValueOnce(json({ detail: 'API key required' }, { status: 401 })),
  });
  expect(authRequired).toMatchObject({ ok: false, kind: 'auth', status: 401 });

  const unavailable = await probeRemoteBackend('http://gpu-box:3900', '', {
    fetcher: vi
      .fn()
      .mockImplementationOnce(health)
      .mockResolvedValueOnce(json({ detail: 'busy' }, { status: 503 })),
  });
  expect(unavailable).toMatchObject({ ok: false, kind: 'http', status: 503 });
});

it.each(['/ws/transcribe', '/ws/events', '/ws/tts'] as const)(
  'mints a path-bound ticket for authenticated remote WebSocket %s',
  async (path) => {
    const session = { token: `ovs_admin_session_${'a'.repeat(43)}`, expiresAt: 3601 };
    const fetcher = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('authorization')).toBe(`Bearer ${session.token}`);
      expect(init?.body).toBe(JSON.stringify({ path }));
      return json({ ticket: `ovs_ws_ticket_${'b'.repeat(43)}`, expires_in: 30 }, { status: 201 });
    });
    expect(
      await remoteWebSocketUrl('https://gpu-box:3900', path, session, {
        fetcher,
        now: () => 1000,
      }),
    ).toBe(`wss://gpu-box:3900${path}?ws_ticket=ovs_ws_ticket_${'b'.repeat(43)}`);
    expect(await remoteWebSocketUrl('http://gpu-box:3900', path, null)).toBe(
      `ws://gpu-box:3900${path}`,
    );
  },
);


const nativeResponses: Record<string, object> = {
  '/health': { status: 'ok', version: 'fixture', device: 'cpu' },
  '/system/info': { app_version: 'fixture' },
  '/api/auth/session': { token: `ovs_admin_session_${'a'.repeat(43)}`, expires_in: 3600 },
  '/api/auth/ws-ticket': { ticket: `ovs_ws_ticket_${'b'.repeat(43)}`, expires_in: 30 },
};

async function nativeBackend(
  handle: (path: string, response: ServerResponse) => void,
  run: (base: string) => Promise<void>,
) {
  const server = createServer((request, response) => handle(request.url!, response));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No fixture port');
  try {
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

it.each(Object.keys(nativeResponses))('bounds a stalled remote %s body after headers', async (path) => {
  await nativeBackend((requested, response) => {
    const body = JSON.stringify(nativeResponses[requested]);
    response.writeHead(requested.includes('/auth/') ? 201 : 200, {
      'Content-Type': 'application/json',
    });
    if (requested === path) {
      response.write(body.slice(0, 1));
      // Release the original implementation too, so a RED run cannot hang.
      const timer = setTimeout(() => response.end(body.slice(1)), 200);
      response.once('close', () => clearTimeout(timer));
    } else response.end(body);
  }, async (base) => {
    if (path === '/api/auth/ws-ticket') {
      await expect(remoteWebSocketUrl(base, '/ws/tts', {
        token: `ovs_admin_session_${'a'.repeat(43)}`,
        expiresAt: Date.now() / 1000 + 60,
      }, { timeoutMs: 50 })).rejects.toMatchObject({ name: 'AbortError' });
    } else {
      expect(await probeRemoteBackend(base, path === '/api/auth/session' ? 'fixture-key' : '', {
        timeoutMs: 50,
      })).toMatchObject({ ok: false, kind: 'timeout' });
    }
  });
});

it('keeps immediate authenticated remote responses and header-only auth failures', async () => {
  let deny = false;
  await nativeBackend((path, response) => {
    if (deny && path === '/system/info') {
      response.writeHead(401, { 'Content-Type': 'application/json' });
      response.write('{'); // Error classification does not require this body.
    } else {
      response.writeHead(path.includes('/auth/') ? 201 : 200, {
        'Content-Type': 'application/json',
      });
      response.end(JSON.stringify(nativeResponses[path]));
    }
  }, async (base) => {
    expect(await probeRemoteBackend(base, 'fixture-key', { timeoutMs: 100 })).toMatchObject({
      ok: true, session: { token: `ovs_admin_session_${'a'.repeat(43)}` },
    });
    deny = true;
    expect(await probeRemoteBackend(base, '', { timeoutMs: 100 })).toMatchObject({
      ok: false, kind: 'auth', status: 401,
    });
  });
});

it('keeps remote session exchanges from following redirects', async () => {
  const paths: string[] = [];
  await nativeBackend((path, response) => {
    paths.push(path);
    if (path === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(nativeResponses[path]));
    } else {
      response.writeHead(302, { Location: '/trap' });
      response.end();
    }
  }, async (base) => {
    expect(await probeRemoteBackend(base, 'fixture-key', { timeoutMs: 100 })).toMatchObject({
      ok: false, kind: 'network',
    });
    expect(paths).toEqual(['/health', '/api/auth/session']);
  });
});
