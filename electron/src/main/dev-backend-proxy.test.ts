// @vitest-environment node
import { createServer } from 'node:http';
import { afterEach, expect, it } from 'vitest';
import { startDevBackendProxy, type DevBackendProxy } from './dev-backend-proxy';

const cleanup: (() => Promise<void>)[] = [];
afterEach(async () => {
  await Promise.allSettled(cleanup.splice(0).map((close) => close()));
});

it('streams requests to the current backend with only main-owned authorization', async () => {
  let received: { path?: string; body?: string; authorization?: string; origin?: string } = {};
  const upstream = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      received = {
        path: request.url,
        body: Buffer.concat(chunks).toString('utf8'),
        authorization: request.headers.authorization,
        origin: request.headers.origin,
      };
      response.writeHead(201, { 'content-type': 'text/plain' });
      response.end('proxied');
    });
  });
  await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  cleanup.push(() => new Promise<void>((resolve) => upstream.close(() => resolve())));
  const address = upstream.address();
  if (!address || typeof address === 'string') throw new Error('upstream did not bind');

  const proxy: DevBackendProxy = await startDevBackendProxy(
    () => `http://127.0.0.1:${address.port}`,
    () => ({ Authorization: 'Bearer scoped-session' }),
    0,
  );
  cleanup.push(proxy.close);
  const response = await fetch(`${proxy.url}/system/info?full=1`, {
    method: 'POST',
    headers: { Authorization: 'Bearer renderer-secret', Origin: 'http://localhost:3902' },
    body: 'stream me',
  });

  expect(response.status).toBe(201);
  expect(await response.text()).toBe('proxied');
  expect(received).toEqual({
    path: '/system/info?full=1',
    body: 'stream me',
    authorization: 'Bearer scoped-session',
    origin: undefined,
  });
});

it('keeps the backend path prefix and never leaves the configured host', async () => {
  const paths: string[] = [];
  const upstream = createServer((request, response) => {
    paths.push(request.url || '');
    response.end('ok');
  });
  await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  cleanup.push(() => new Promise<void>((resolve) => upstream.close(() => resolve())));
  const address = upstream.address();
  if (!address || typeof address === 'string') throw new Error('upstream did not bind');

  const proxy = await startDevBackendProxy(
    () => `http://127.0.0.1:${address.port}/studio/`,
    () => ({}),
    0,
  );
  cleanup.push(proxy.close);
  await fetch(`${proxy.url}/system/info?full=1`);
  await fetch(`${proxy.url}//elsewhere.invalid/health`);

  expect(paths).toEqual(['/studio/system/info?full=1', '/studio/elsewhere.invalid/health']);
});

it('closes while a renderer keeps a streaming backend response open', async () => {
  const upstream = createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.write('still streaming');
  });
  await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  cleanup.push(
    () =>
      new Promise<void>((resolve) => {
        upstream.closeAllConnections();
        upstream.close(() => resolve());
      }),
  );
  const address = upstream.address();
  if (!address || typeof address === 'string') throw new Error('upstream did not bind');
  const proxy = await startDevBackendProxy(
    () => `http://127.0.0.1:${address.port}`,
    () => ({}),
    0,
  );
  const response = await fetch(proxy.url);

  await expect(proxy.close()).resolves.toBeUndefined();
  await expect(proxy.close()).resolves.toBeUndefined();
  await response.body?.cancel().catch(() => {});
});
