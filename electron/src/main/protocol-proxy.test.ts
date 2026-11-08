// @vitest-environment node
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ handle: vi.fn(), fetch: vi.fn() }));
vi.mock('electron', () => ({
  protocol: { handle: mocks.handle },
  net: { fetch: mocks.fetch },
}));
import { APP_ORIGIN, installAppProtocol } from './protocol';

it('replaces caller origin with main-owned desktop identity on packaged API requests', async () => {
  mocks.fetch.mockResolvedValue(new Response('accepted'));
  installAppProtocol(
    () => 'http://127.0.0.1:3900',
    '/unused',
    () => ({
      'X-VoiceStudio-Desktop-Origin': APP_ORIGIN,
      Authorization: 'Bearer scoped-session',
    }),
  );
  const handler = mocks.handle.mock.calls[0][1];
  const response = await handler(
    new Request(`${APP_ORIGIN}/api/dub/ingest-url`, {
      method: 'POST',
      headers: {
        Origin: 'https://untrusted.example',
        Authorization: 'Bearer renderer-secret',
        'X-VoiceStudio-Desktop-Origin': 'app://forged',
      },
      body: '{}',
    }),
  );
  expect(await response.text()).toBe('accepted');
  const [url, options] = mocks.fetch.mock.calls[0];
  expect(url).toBe('http://127.0.0.1:3900/dub/ingest-url');
  expect(options.headers.get('origin')).toBeNull();
  expect(options.headers.get('x-voicestudio-desktop-origin')).toBe(APP_ORIGIN);
  expect(options.headers.get('authorization')).toBe('Bearer scoped-session');
});

it('streams a local dub upload before the request body finishes (#3853)', async () => {
  let releaseTail!: () => void;
  const receivedHead = new Promise<void>((resolve) => {
    releaseTail = resolve;
  });
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) {
      chunks.push(chunk);
      releaseTail();
    }
    res.statusCode = 202;
    res.setHeader('content-type', 'application/json');
    res.end(
      JSON.stringify({
        body: Buffer.concat(chunks).toString(),
        authorization: req.headers.authorization,
        origin: req.headers['x-voicestudio-desktop-origin'],
        contentType: req.headers['content-type'],
        path: req.url,
      }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const address = server.address() as AddressInfo;
    installAppProtocol(
      () => `http://127.0.0.1:${address.port}`,
      '/unused',
      () => ({
        'X-VoiceStudio-Desktop-Origin': APP_ORIGIN,
        Authorization: 'Bearer scoped-session',
      }),
    );
    const handler = mocks.handle.mock.lastCall![1];
    const contentType = 'multipart/form-data; boundary=upload-test';
    const head =
      '--upload-test\r\nContent-Disposition: form-data; name="video"; filename="clip.mp4"\r\n\r\n';
    const tail = 'video bytes\r\n--upload-test--\r\n';
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encoder.encode(head));
        await receivedHead;
        controller.enqueue(encoder.encode(tail));
        controller.close();
      },
    });
    const response = await handler(
      new Request(`${APP_ORIGIN}/api/dub/upload?test=1`, {
        method: 'POST',
        headers: { 'content-type': contentType, Authorization: 'Bearer renderer-secret' },
        body,
        duplex: 'half',
        signal: AbortSignal.timeout(2000),
      }),
    );
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({
      body: head + tail,
      authorization: 'Bearer scoped-session',
      origin: APP_ORIGIN,
      contentType,
      path: '/dub/upload?test=1',
    });
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

it('keeps remote dub uploads on the authenticated Electron transport', async () => {
  const backend = 'https://remote.example/voicestudio';
  mocks.fetch.mockResolvedValueOnce(new Response('remote accepted'));
  installAppProtocol(
    () => backend,
    '/unused',
    () => ({ Authorization: 'Bearer remote-session' }),
  );
  const handler = mocks.handle.mock.lastCall![1];
  const response = await handler(
    new Request(`${APP_ORIGIN}/api/dub/upload`, {
      method: 'POST',
      body: 'video bytes',
    }),
  );
  expect(await response.text()).toBe('remote accepted');
  const [url, options] = mocks.fetch.mock.lastCall!;
  expect(url).toBe(`${backend}/dub/upload`);
  expect(options.headers.get('authorization')).toBe('Bearer remote-session');
});
