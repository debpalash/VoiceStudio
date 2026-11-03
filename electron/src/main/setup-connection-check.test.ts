// @vitest-environment node
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import type { get as httpsGet } from 'node:https';
import { expect, it, vi } from 'vitest';
import { checkSetupConnection, setupProbeUrl, SETUP_CHECK_BYTES } from './setup-connection-check';

const lock = `[[package]]
name = "torch"
version = "2.8.0"
wheels = [{ url = "https://download-r2.pytorch.org/whl/torch-2.8.0-cp311-cp311-win_amd64.whl", hash = "sha256:${'a'.repeat(64)}" }]
`;
it('checks the current locked artifact and rejects unrelated hosts/platforms', () => {
  const progress = {
    activePackage: 'torch',
    downloadHost: 'download-r2.pytorch.org',
    completedDownloads: 0,
  };
  expect(
    setupProbeUrl(lock, progress, { platform: 'win32', arch: 'x64', pythonMinor: 11 }),
  ).toContain('/torch-2.8.0-');
  expect(
    setupProbeUrl(
      lock,
      { ...progress, downloadHost: 'other.example' },
      { platform: 'win32', arch: 'x64', pythonMinor: 11 },
    ),
  ).toBeNull();
  expect(
    setupProbeUrl(lock, progress, { platform: 'darwin', arch: 'arm64', pythonMinor: 11 }),
  ).toBeNull();
});

function server(status: number, body: Buffer | Error | null) {
  const response = Object.assign(new PassThrough(), { statusCode: status, headers: {} });
  const req = Object.assign(new EventEmitter(), { destroy: vi.fn() });
  const request = vi.fn((_url, options, callback) => {
    options.signal.addEventListener('abort', () => req.emit('error', new Error('aborted')), {
      once: true,
    });
    queueMicrotask(() => {
      callback(response);
      if (body instanceof Error) response.destroy(body);
      else if (body) response.end(body);
    });
    return req;
  });
  return { request: request as unknown as typeof httpsGet, response, req };
}

it.each([200, 206])(
  'caps the sample and closes the response, including servers ignoring Range (%s)',
  async (status) => {
    const { request, response, req } = server(status, Buffer.alloc(SETUP_CHECK_BYTES * 2));
    const result = await checkSetupConnection(
      'https://download.example/torch.whl',
      {},
      undefined,
      request,
    );
    expect(result).toMatchObject({
      status: 'ok',
      host: 'download.example',
      bytes: SETUP_CHECK_BYTES,
    });
    expect(result.bytesPerSecond).toBeGreaterThan(0);
    expect(response.destroyed).toBe(true);
    expect(req.destroy).toHaveBeenCalled();
  },
);

it('retains the TLS cause for diagnostics', async () => {
  const { request } = server(206, new Error("cannot decrypt peer's message"));
  const result = await checkSetupConnection(
    'https://download.example/torch.whl',
    {},
    undefined,
    request,
  );
  expect(result).toMatchObject({
    status: 'failed',
    error: expect.stringContaining('cannot decrypt'),
  });
});

it('a server that stops sending cannot keep a cancelled check running', async () => {
  const { request, response } = server(206, null);
  const controller = new AbortController();
  const pending = checkSetupConnection(
    'https://download.example/torch.whl',
    {},
    controller.signal,
    request,
  );
  await Promise.resolve();
  controller.abort();
  expect(await pending).toMatchObject({
    status: 'failed',
    error: expect.stringContaining('timed out or was cancelled'),
  });
  expect(response.destroyed).toBe(true);
});

it('does not report HTTP errors or truncated samples as a successful speed test', async () => {
  for (const status of [403, 503, 206]) {
    const { request } = server(status, Buffer.alloc(10));
    expect(
      (await checkSetupConnection('https://download.example/torch.whl', {}, undefined, request))
        .status,
    ).toBe('failed');
  }
});

it('checks the CUDA wheel rather than the CPU wheel on the same PyTorch host', () => {
  const both = lock.replace('/whl/torch-', '/whl/cpu/torch-') + lock.replace('/whl/torch-', '/whl/cu128/torch-');
  const progress = { activePackage: 'torch', downloadHost: 'download-r2.pytorch.org', completedDownloads: 0 };
  const host = { platform: 'win32' as const, arch: 'x64', pythonMinor: 11 };
  expect(setupProbeUrl(both, progress, { ...host, torchVariant: 'default' })).toContain('/cu128/');
  expect(setupProbeUrl(both, progress, { ...host, torchVariant: 'cpu' })).toContain('/cpu/');
});
