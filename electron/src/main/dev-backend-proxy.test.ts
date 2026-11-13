// @vitest-environment node
import { createServer } from 'node:http';
import { afterEach, expect, it } from 'vitest';
import { DEFAULT_ELECTRON_PROXY_PORT } from '../../../config/app-config.mjs';
import {
  electronProxyPort,
  proxyRequestRefused,
  startDevBackendProxy,
  type DevBackendProxy,
} from './dev-backend-proxy';

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
        origin: request.headers['x-voicestudio-desktop-origin'] as string | undefined,
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
    () => ({ Authorization: 'Bearer scoped-session', 'X-VoiceStudio-Desktop-Origin': 'app://voicestudio' }),
    0,
    ['http://localhost:3902'],
  );
  cleanup.push(proxy.close);
  const response = await fetch(`${proxy.url}/system/info?full=1`, {
    method: 'POST',
    headers: { Authorization: 'Bearer renderer-secret', Origin: 'http://localhost:3902', 'X-VoiceStudio-Desktop-Origin': 'app://forged' },
    body: 'stream me',
  });

  expect(response.status).toBe(201);
  expect(await response.text()).toBe('proxied');
  expect(received).toEqual({
    path: '/system/info?full=1',
    body: 'stream me',
    authorization: 'Bearer scoped-session',
    origin: 'app://voicestudio',
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

it('refuses other websites and rebound host names before injecting credentials', () => {
  const renderer = ['http://localhost:3902'];
  expect(proxyRequestRefused({ host: '127.0.0.1:3903' }, renderer)).toBe(false);
  expect(
    proxyRequestRefused({ host: 'localhost:3903', 'sec-fetch-site': 'same-origin' }, renderer),
  ).toBe(false);
  expect(
    proxyRequestRefused({ host: '127.0.0.1:3903', origin: 'http://localhost:3902' }, renderer),
  ).toBe(false);
  expect(proxyRequestRefused({ host: 'evil.example:3903' }, renderer)).toBe(true);
  expect(
    proxyRequestRefused({ host: '127.0.0.1:3903', origin: 'https://evil.example' }, renderer),
  ).toBe(true);
  expect(proxyRequestRefused({ host: '127.0.0.1:3903', origin: 'null' }, renderer)).toBe(true);
  expect(
    proxyRequestRefused({ host: '127.0.0.1:3903', 'sec-fetch-site': 'cross-site' }, renderer),
  ).toBe(true);
});

it('binds an OS-assigned port when asked for port 0, so no pre-picked port can be stolen', async () => {
  expect(electronProxyPort('0')).toBe(0);
  const proxy = await startDevBackendProxy(() => 'http://127.0.0.1:1', () => ({}), 0);
  cleanup.push(proxy.close);
  expect(Number(new URL(proxy.url).port)).toBeGreaterThan(0);
});

it('falls back to the default proxy port for a missing or invalid setting', () => {
  for (const raw of [undefined, '', '  ', 'abc', '-1', '65536', '1.5']) {
    expect(electronProxyPort(raw)).toBe(DEFAULT_ELECTRON_PROXY_PORT);
  }
  expect(electronProxyPort('49305')).toBe(49305);
});
