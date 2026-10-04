// @vitest-environment node
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import type { IncomingMessage } from 'node:http';
import { get } from 'node:https';
import { afterEach, expect, it, vi } from 'vitest';
import {
  downloadRuntimeArchive,
  downloadRuntimeInstaller,
  setupProxyForUrl,
} from './runtime-download';
vi.mock('node:https', async (original) => ({
  ...(await original<typeof import('node:https')>()),
  get: vi.fn(),
}));
const directories: string[] = [];
afterEach(async () => {
  vi.mocked(get).mockReset();
  await Promise.all(
    directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })),
  );
});
async function archiveDestination() {
  const directory = await mkdtemp(join(tmpdir(), 'runtime-archive-test-'));
  directories.push(directory);
  return join(directory, 'rocm-python-wheels-Windows-v4.8.2.zip');
}
function sha256(body: string) {
  return createHash('sha256').update(body).digest('hex').toUpperCase();
}
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
function reply(status: number, body: string, location?: string, contentLength?: string) {
  vi.mocked(get).mockImplementationOnce(((_url, _options, callback) => {
    const request = new EventEmitter();
    queueMicrotask(() => {
      const response = Object.assign(new PassThrough(), {
        statusCode: status,
        headers: { location, 'content-length': contentLength },
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

const archiveUrl =
  'https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip';

it('downloads the archive through HTTPS redirects with proxy bypass and reuses only verified cache', async () => {
  const destination = await archiveDestination();
  const env = { HTTPS_PROXY: 'socks5h://user:pass@proxy.test:1080', NO_PROXY: 'assets.test' };
  reply(302, '', 'https://assets.test/archive.zip');
  reply(200, 'archive bytes');
  await downloadRuntimeArchive(
    archiveUrl,
    destination,
    sha256('archive bytes'),
    env,
    new AbortController().signal,
  );
  expect(await readFile(destination, 'utf8')).toBe('archive bytes');
  expect(await readdir(join(destination, '..'))).toEqual(['rocm-python-wheels-Windows-v4.8.2.zip']);
  expect(get).toHaveBeenCalledTimes(2);
  const options = vi.mocked(get).mock.calls[0][1] as unknown as {
    agent: { getProxyForUrl: (url: string) => string };
  };
  expect(options.agent.getProxyForUrl(archiveUrl)).toBe(env.HTTPS_PROXY);
  expect(options.agent.getProxyForUrl('https://assets.test/archive.zip')).toBe('');

  await downloadRuntimeArchive(
    archiveUrl,
    destination,
    sha256('archive bytes'),
    {},
    new AbortController().signal,
  );
  expect(get).toHaveBeenCalledTimes(2);

  await writeFile(destination, 'corrupted cache');
  reply(200, 'archive bytes');
  await downloadRuntimeArchive(
    archiveUrl,
    destination,
    sha256('archive bytes'),
    {},
    new AbortController().signal,
  );
  expect(get).toHaveBeenCalledTimes(3);
  expect(await readFile(destination, 'utf8')).toBe('archive bytes');
});

it('rejects bad checksums without replacing an existing archive or leaving a partial file', async () => {
  const destination = await archiveDestination();
  await writeFile(destination, 'existing cache');
  reply(200, 'unverified');
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('expected'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('SHA-256');
  expect(await readFile(destination, 'utf8')).toBe('existing cache');
  expect(await readdir(join(destination, '..'))).toEqual(['rocm-python-wheels-Windows-v4.8.2.zip']);
});

it('creates the sibling cache directory when needed and rejects failed HTTP responses', async () => {
  const base = await archiveDestination();
  const destination = join(base, '..', '.uv-cache', 'rocm-python-wheels-Windows-v4.8.2.zip');
  reply(503, 'unavailable');
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('archive'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('(503)');
  reply(200, 'archive');
  await downloadRuntimeArchive(
    archiveUrl,
    destination,
    sha256('archive'),
    {},
    new AbortController().signal,
  );
  expect(await readFile(destination, 'utf8')).toBe('archive');
  expect(await readdir(join(destination, '..'))).toEqual(['rocm-python-wheels-Windows-v4.8.2.zip']);
});

it('rejects oversized archives without leaving a temporary file', async () => {
  const destination = await archiveDestination();
  reply(200, 'no body needed', undefined, String(200 * 1024 * 1024 + 1));
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('no body needed'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('size limit');
  expect(await readdir(join(destination, '..'))).toEqual([]);
});

it('enforces the size limit on the stream even when the server omits content length', async () => {
  const destination = await archiveDestination();
  vi.mocked(get).mockImplementationOnce(((_url, _options, callback) => {
    const request = new EventEmitter();
    queueMicrotask(() => {
      const response = Object.assign(new PassThrough(), { statusCode: 200, headers: {} });
      callback!(response as unknown as IncomingMessage);
      const chunk = Buffer.alloc(1024 * 1024);
      for (let index = 0; index <= 200; index++) response.write(chunk);
      response.end();
    });
    return request;
  }) as typeof get);
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('archive'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('size limit');
  expect(await readdir(join(destination, '..'))).toEqual([]);
});

it('does not expose proxy credentials in transport errors', async () => {
  const destination = await archiveDestination();
  vi.mocked(get).mockImplementationOnce(((_url, _options, _callback) => {
    const request = new EventEmitter();
    queueMicrotask(() => request.emit('error', new Error('proxy secret-pass rejected')));
    return request;
  }) as typeof get);
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('archive'),
      { HTTPS_PROXY: 'http://user:secret-pass@proxy.test:8080' },
      new AbortController().signal,
    ),
  ).rejects.toThrow('Runtime archive request failed');
  expect(await readdir(join(destination, '..'))).toEqual([]);
});

it('rejects insecure redirects, invalid digests, and URL credentials without leaking secrets', async () => {
  const destination = await archiveDestination();
  reply(302, '', 'http://private-user:private-pass@assets.test/archive.zip');
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('archive'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('requires HTTPS');
  expect(get).toHaveBeenCalledTimes(1);
  await expect(
    downloadRuntimeArchive(
      'https://private-user:private-pass@assets.test/archive.zip',
      destination,
      sha256('archive'),
      {},
      new AbortController().signal,
    ),
  ).rejects.not.toThrow('private-pass');
  await expect(
    downloadRuntimeArchive(archiveUrl, destination, 'invalid', {}, new AbortController().signal),
  ).rejects.toThrow('SHA-256');
  expect(get).toHaveBeenCalledTimes(1);
  expect(await readdir(join(destination, '..'))).toEqual([]);
});

it('rejects after five redirects and cleans up on cancellation', async () => {
  const destination = await archiveDestination();
  for (let redirect = 0; redirect < 6; redirect++) {
    reply(302, '', `https://assets.test/archive-${redirect}.zip`);
  }
  await expect(
    downloadRuntimeArchive(
      archiveUrl,
      destination,
      sha256('archive'),
      {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('redirect limit');
  expect(get).toHaveBeenCalledTimes(6);

  const controller = new AbortController();
  vi.mocked(get).mockImplementationOnce(((_url, _options, callback) => {
    const request = new EventEmitter();
    queueMicrotask(() => {
      const response = Object.assign(new PassThrough(), { statusCode: 200, headers: {} });
      callback!(response as unknown as IncomingMessage);
      response.write('partial');
      controller.abort();
    });
    return request;
  }) as typeof get);
  await expect(
    downloadRuntimeArchive(archiveUrl, destination, sha256('archive'), {}, controller.signal),
  ).rejects.toThrow();
  expect(await readdir(join(destination, '..'))).toEqual([]);
});

it('does not start an archive request when already cancelled', async () => {
  const destination = await archiveDestination();
  const controller = new AbortController();
  controller.abort();
  await expect(
    downloadRuntimeArchive(archiveUrl, destination, sha256('archive'), {}, controller.signal),
  ).rejects.toThrow();
  expect(get).not.toHaveBeenCalled();
  expect(await readdir(join(destination, '..'))).toEqual([]);
});
