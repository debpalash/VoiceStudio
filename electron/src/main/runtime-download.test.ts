// @vitest-environment node
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import type { IncomingMessage } from 'node:http';
import { get } from 'node:https';
import { afterEach, expect, it, vi } from 'vitest';
import { downloadRuntimeInstaller, setupProxyForUrl } from './runtime-download';
vi.mock('node:https', async (original) => ({
  ...(await original<typeof import('node:https')>()),
  get: vi.fn(),
}));
afterEach(() => vi.mocked(get).mockReset());
it('resolves protocols, suffixes, ports, IPv6 and credential-bearing proxies', () => {
  const env = {
    HTTPS_PROXY: 'socks5h://user:pass@proxy:1080',
    HTTP_PROXY: 'http://other:80',
    NO_PROXY: '.internal,example.test:8443,[::1],*',
  };
  expect(setupProxyForUrl('https://public.test', env)).toBe('');
  env.NO_PROXY = '.internal,example.test:8443,[::1]';
  for (const url of [
    'https://api.internal',
    'https://internal',
    'https://example.test:8443',
    'https://[::1]',
  ])
    expect(setupProxyForUrl(url, env)).toBe('');
  expect(setupProxyForUrl('https://example.test', env)).toBe(env.HTTPS_PROXY);
  expect(setupProxyForUrl('http://public.test', env)).toBe(env.HTTP_PROXY);
  expect(setupProxyForUrl('https://public.test', { ALL_PROXY: 'localhost:3128' })).toBe(
    'http://localhost:3128',
  );
});
function reply(status: number, body: string, location?: string) {
  vi.mocked(get).mockImplementationOnce(((_url, _options, callback) => {
    const request = new EventEmitter();
    queueMicrotask(() => {
      const response = Object.assign(new PassThrough(), {
        statusCode: status,
        headers: { location },
      });
      callback!(response as unknown as IncomingMessage);
      response.end(body);
    });
    return request;
  }) as typeof get);
}
it('keeps proxy and bypass settings across redirects', async () => {
  reply(302, '', 'https://downloads.test/install.sh');
  reply(200, '# installer');
  const env = { HTTPS_PROXY: 'socks5h://localhost:1080', NO_PROXY: 'downloads.test' };
  expect(
    await downloadRuntimeInstaller(
      'https://astral.sh/install.sh',
      env,
      new AbortController().signal,
    ),
  ).toBe('# installer');
  expect(get).toHaveBeenCalledTimes(2);
  const options = vi.mocked(get).mock.calls[0][1] as unknown as {
    agent: { getProxyForUrl: (url: string) => string };
  };
  expect(options.agent.getProxyForUrl('https://astral.sh')).toBe(env.HTTPS_PROXY);
  expect(options.agent.getProxyForUrl('https://downloads.test')).toBe('');
});
it('rejects insecure redirects before contacting them', async () => {
  reply(302, '', 'http://downloads.test/install.sh');
  await expect(
    downloadRuntimeInstaller('https://astral.sh', {}, new AbortController().signal),
  ).rejects.toThrow('requires HTTPS');
  expect(get).toHaveBeenCalledTimes(1);
});
it('rejects oversized scripts and HTTP failures', async () => {
  reply(200, 'x'.repeat(2 * 1024 * 1024 + 1));
  await expect(
    downloadRuntimeInstaller('https://astral.sh', {}, new AbortController().signal),
  ).rejects.toThrow('size limit');
  for (let i = 0; i < 3; i += 1) reply(503, 'unavailable');
  await expect(
    downloadRuntimeInstaller('https://astral.sh', {}, new AbortController().signal, 0),
  ).rejects.toThrow('(503)');
  expect(get).toHaveBeenCalledTimes(4); // 1 oversized + 3 bounded attempts
});
it('retries transient failures so a flaky first-run network still bootstraps', async () => {
  reply(503, 'unavailable');
  vi.mocked(get).mockImplementationOnce((() => {
    const request = new EventEmitter();
    queueMicrotask(() =>
      request.emit('error', Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' })),
    );
    return request;
  }) as unknown as typeof get);
  reply(200, '# installer');
  expect(
    await downloadRuntimeInstaller('https://astral.sh', {}, new AbortController().signal, 0),
  ).toBe('# installer');
  expect(get).toHaveBeenCalledTimes(3);
});
it('does not retry definitive answers such as HTTP 404', async () => {
  reply(404, 'missing');
  await expect(
    downloadRuntimeInstaller('https://astral.sh', {}, new AbortController().signal, 0),
  ).rejects.toThrow('(404)');
  expect(get).toHaveBeenCalledTimes(1);
});
it('does not start cancelled downloads', async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(
    downloadRuntimeInstaller('https://astral.sh', {}, controller.signal),
  ).rejects.toThrow();
  expect(get).not.toHaveBeenCalled();
});

it.each([
  'ftp://private-user:private-pass@proxy.test',
  'https://private-user:private-pass@[invalid',
])('does not expose credentials in invalid proxy errors', (proxy) => {
  expect(() => setupProxyForUrl('https://astral.sh', { HTTPS_PROXY: proxy })).toThrow(
    'Invalid or unsupported proxy URL; use HTTP, HTTPS, or SOCKS.',
  );
});

it('propagates a credential-free transport error to setup', async () => {
  vi.mocked(get).mockImplementationOnce(((_url, options) => {
    const request = new EventEmitter();
    const agent = (options as unknown as { agent: { getProxyForUrl: (url: string) => string } })
      .agent;
    queueMicrotask(() => {
      try {
        agent.getProxyForUrl('https://astral.sh');
      } catch (error) {
        request.emit('error', error);
      }
    });
    return request;
  }) as typeof get);
  await expect(
    downloadRuntimeInstaller(
      'https://astral.sh',
      { HTTPS_PROXY: 'ftp://private-user:private-pass@proxy.test' },
      new AbortController().signal,
    ),
  ).rejects.toThrow('Invalid or unsupported proxy URL; use HTTP, HTTPS, or SOCKS.');
});
