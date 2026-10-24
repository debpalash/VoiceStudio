import { expect, it, vi } from 'vitest';
import { normalizeRemoteUrl, probeRemoteBackend, remoteWebSocketUrl } from './remote-backend';

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...init.headers },
  });
}

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

/** Headers arrive at once, then the body stalls after its first byte. */
function stalledBody(status = 200): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{'));
      },
    }),
    { status, headers: { 'content-type': 'application/json' } },
  );
}

const SESSION = `ovs_admin_session_${'a'.repeat(43)}`;
const healthy = () => json({ status: 'ok', version: '0.5.2', device: 'cpu' });

it.each([
  ['/health', [] as Array<() => Response>, ''],
  ['/api/auth/session', [healthy], 'master-secret'],
  ['/system/info', [healthy], ''],
] as const)(
  'times out a %s response whose body stalls after the headers',
  async (phase, before, master) => {
    const replies = [...before, () => stalledBody(phase === '/api/auth/session' ? 201 : 200)];
    const fetcher = vi.fn(async () => replies.shift()!());
    const started = Date.now();
    const result = await probeRemoteBackend('http://gpu-box:3900', master, {
      fetcher,
      timeoutMs: 50,
    });
    expect(result).toMatchObject({ ok: false, kind: 'timeout' });
    expect(Date.now() - started).toBeLessThan(2000);
  },
);

it('times out a WebSocket ticket response whose body stalls after the headers', async () => {
  const fetcher = vi.fn(async () => stalledBody(201));
  await expect(
    remoteWebSocketUrl(
      'http://gpu-box:3900',
      '/ws/tts',
      { token: SESSION, expiresAt: 3601 },
      { fetcher, now: () => 1000, timeoutMs: 50 },
    ),
  ).rejects.toThrow(/timed out/);
});

it('keeps HTTP and auth classifications when error bodies are left unread', async () => {
  const denied = await probeRemoteBackend('http://gpu-box:3900', 'master-secret', {
    fetcher: vi
      .fn()
      .mockImplementationOnce(healthy)
      .mockImplementationOnce(() => stalledBody(403)),
    timeoutMs: 50,
  });
  expect(denied).toMatchObject({ ok: false, kind: 'auth', status: 403 });
  await expect(
    remoteWebSocketUrl(
      'http://gpu-box:3900',
      '/ws/tts',
      { token: SESSION, expiresAt: 3601 },
      { fetcher: vi.fn(async () => stalledBody(503)), now: () => 1000, timeoutMs: 50 },
    ),
  ).rejects.toThrow('HTTP 503');
});

it.each(['/ws/transcribe', '/ws/events', '/ws/tts'] as const)(
  'keeps a reverse-proxy path prefix in remote WebSocket %s URLs',
  async (path) => {
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('https://gpu-box/studio/api/auth/ws-ticket');
      expect(init?.body).toBe(JSON.stringify({ path }));
      return json({ ticket: `ovs_ws_ticket_${'b'.repeat(43)}`, expires_in: 30 }, { status: 201 });
    });
    expect(
      await remoteWebSocketUrl(
        'https://gpu-box/studio/',
        path,
        { token: SESSION, expiresAt: 3601 },
        { fetcher, now: () => 1000 },
      ),
    ).toBe(`wss://gpu-box/studio${path}?ws_ticket=ovs_ws_ticket_${'b'.repeat(43)}`);
    expect(await remoteWebSocketUrl('http://gpu-box:8080/a/b', path, null)).toBe(
      `ws://gpu-box:8080/a/b${path}`,
    );
  },
);

it.each(['/health', '/api/auth/session', '/system/info', '/api/auth/ws-ticket'])(
  'cancels an oversized %s body before buffering the whole response',
  async (phase) => {
    let chunks = 0;
    let canceled = false;
    const oversized = new Response(
      new ReadableStream<Uint8Array>({
        pull(controller) {
          chunks++;
          controller.enqueue(new Uint8Array(1024));
          if (chunks === 100) controller.close();
        },
        cancel() {
          canceled = true;
        },
      }),
      { status: phase.startsWith('/api/auth/') ? 201 : 200 },
    );
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      if (String(input).endsWith(phase)) return oversized;
      return healthy();
    });
    if (phase === '/api/auth/ws-ticket') {
      await expect(
        remoteWebSocketUrl(
          'http://gpu-box:3900',
          '/ws/tts',
          { token: SESSION, expiresAt: 3601 },
          { fetcher, now: () => 1000 },
        ),
      ).rejects.toThrow('invalid WebSocket ticket');
    } else {
      const master = phase === '/api/auth/session' ? 'master-secret' : '';
      const result = await probeRemoteBackend('http://gpu-box:3900', master, { fetcher });
      expect(result.ok).toBe(false);
    }
    expect(canceled).toBe(true);
    expect(chunks).toBeLessThanOrEqual(18);
  },
);

it('accepts valid JSON split across byte and UTF-8 boundaries within the cap', async () => {
  const health = new TextEncoder().encode(
    JSON.stringify({ status: 'ok', version: '0.5.7', device: 'café' }),
  );
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    if (!String(input).endsWith('/health')) return json({ app_version: '0.5.7' });
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const byte of health) controller.enqueue(new Uint8Array([byte]));
          controller.close();
        },
      }),
    );
  });
  expect(await probeRemoteBackend('http://gpu-box:3900', '', { fetcher })).toMatchObject({
    ok: true,
    detail: '0.5.7 on café',
  });
});

it('accepts an identity response exactly at the byte cap', async () => {
  const body = JSON.stringify({ status: 'ok', version: '0.5.7', device: 'cpu' }).padEnd(16 * 1024);
  const fetcher = vi.fn(async (input: string | URL | Request) =>
    String(input).endsWith('/health')
      ? new Response(body)
      : json({ app_version: '0.5.7' }),
  );
  expect(await probeRemoteBackend('http://gpu-box:3900', '', { fetcher })).toMatchObject({
    ok: true,
  });
});
