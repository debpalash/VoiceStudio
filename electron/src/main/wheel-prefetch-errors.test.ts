// @vitest-environment node
import { EventEmitter } from 'node:events';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import type { get as httpsGet } from 'node:https';
import { expect, it } from 'vitest';
import { downloadSegmented, httpsRangeTransport, prefetchWheels } from './wheel-prefetch';

it('handles a runtime size-probe response reset after valid range headers', async () => {
  const response = Object.assign(new PassThrough(), {
    statusCode: 206,
    headers: { 'content-range': 'bytes 0-0/1000' },
    setTimeout: () => undefined,
  });
  const get = ((_url: unknown, _options: unknown, callback: (body: typeof response) => void) => {
    setImmediate(() => {
      callback(response);
      setImmediate(() => response.destroy(new Error('peer reset after headers')));
    });
    return Object.assign(new EventEmitter(), { setTimeout: () => undefined });
  }) as unknown as typeof httpsGet;
  const { probeSize, close } = httpsRangeTransport({}, get);
  try {
    expect(await probeSize('https://files.pythonhosted.org/runtime.whl', new AbortController().signal)).toBe(1000);
    await new Promise((resolve) => setTimeout(resolve, 20));
  } finally {
    close();
  }
});

it('preserves a response error for a body consumer that attaches after the reset', async () => {
  const reset = new Error('response reset before iterator handoff');
  let failed!: () => void;
  const failure = new Promise<void>((resolve) => { failed = resolve; });
  const response = Object.assign(new PassThrough(), {
    statusCode: 206,
    headers: { 'content-range': 'bytes 0-2/3' },
    setTimeout: () => undefined,
  });
  response.once('close', failed);
  const get = ((_url: unknown, _options: unknown, callback: (body: typeof response) => void) => {
    setImmediate(() => {
      callback(response);
      response.destroy(reset);
    });
    return Object.assign(new EventEmitter(), { setTimeout: () => undefined });
  }) as unknown as typeof httpsGet;
  const { transport, close } = httpsRangeTransport({}, get);
  try {
    const body = await transport('https://files.pythonhosted.org/runtime.whl', 0, 2, new AbortController().signal);
    await failure;
    await expect((async () => { for await (const _chunk of body) { /* Drain. */ } })()).rejects.toBe(reset);
  } finally {
    close();
  }
});

it.each([false, true])('never publishes an incomplete runtime wheel after a response failure (cancel=%s)', async (cancel) => {
  const root = await mkdtemp(join(tmpdir(), 'voice-wheel-response-'));
  const dest = join(root, 'runtime.whl');
  const data = Buffer.from('verified-runtime-wheel');
  const controller = new AbortController();
  const reset = new Error('response interrupted');
  const get = ((_url: unknown, options: { signal: AbortSignal }, callback: (body: PassThrough) => void) => {
    const response = Object.assign(new PassThrough(), {
      statusCode: 206,
      headers: { 'content-range': `bytes 0-${data.length - 1}/${data.length}` },
      setTimeout: () => undefined,
    });
    options.signal.addEventListener('abort', () => response.destroy(options.signal.reason), { once: true });
    setImmediate(() => {
      callback(response);
      response.write(data.subarray(0, 3));
      setImmediate(() => {
        if (cancel) controller.abort(reset);
        else response.destroy(reset);
      });
    });
    return Object.assign(new EventEmitter(), { setTimeout: () => undefined });
  }) as unknown as typeof httpsGet;
  const { transport, close } = httpsRangeTransport({}, get);
  try {
    const attempt = downloadSegmented(dest, {
      urls: ['https://files.pythonhosted.org/runtime.whl'],
      sha256: createHash('sha256').update(data).digest('hex'), size: data.length,
      connections: 1, chunkBytes: data.length, transport,
      signal: controller.signal, retryDelayMs: 0,
    });
    await expect(attempt).rejects.toBe(reset);
    expect(existsSync(dest)).toBe(false);
  } finally {
    close();
    await rm(root, { recursive: true, force: true });
  }
});

it('fails a download that never receives a byte instead of looping (#4476)', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-wheel-stall-'));
  try {
    const hung = () => new Promise<never>(() => undefined);
    const logs: string[] = [];
    const started = Date.now();
    const wheels = ['a', 'b'].map((name) => ({
      name,
      version: '1',
      url: `https://files.pythonhosted.org/${name}.whl`,
      filename: `${name}.whl`,
      sha256: createHash('sha256').update(name).digest('hex'),
      size: 100 * 1024 ** 2,
    }));
    const transfers: Array<[string, number, number]> = [];
    const local = await prefetchWheels(wheels, {
      dir: root,
      connections: 4,
      transport: hung,
      signal: new AbortController().signal,
      stallMs: 100,
      freeBytes: async () => Infinity,
      log: (line) => logs.push(line),
      transfer: (name, received, total) => transfers.push([name, received, total]),
    });
    expect(local.size).toBe(0);
    expect(Date.now() - started).toBeLessThan(2_000);
    expect(logs.filter((l) => l.includes('stalled; skipping')).length).toBe(1);
    expect(logs.some((l) => l.includes('uv will download it instead'))).toBe(true);
    expect(transfers.filter(([, , total]) => total === 0).map(([name]) => name)).toEqual(['a', 'b']);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
