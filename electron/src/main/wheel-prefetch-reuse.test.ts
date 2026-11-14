// @vitest-environment node
import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it, vi } from 'vitest';
import { prefetchWheels, type RangeTransport } from './wheel-prefetch';

it('reuses a completed verified runtime wheel when only the install reserve remains free', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'voice-wheel-reuse-audit-'));
  const data = Buffer.from('complete-wheel');
  const wheel = {
    name: 'torch', version: 'test', filename: 'torch-test.whl',
    url: 'https://download.pytorch.org/torch-test.whl',
    sha256: createHash('sha256').update(data).digest('hex'), size: data.length,
  };
  const transport = vi.fn<RangeTransport>(async (_url, start, end) =>
    (async function* () { yield data.subarray(start, end + 1); })(),
  );
  const options = {
    dir, connections: 2, transport, signal: new AbortController().signal,
    reserveBytes: 100, freeBytes: async () => 1000,
  };
  try {
    expect((await prefetchWheels([wheel], options)).size).toBe(1);
    transport.mockClear();
    const reused = await prefetchWheels([wheel], { ...options, freeBytes: async () => 100 });
    expect(reused.size).toBe(1);
    expect(transport).not.toHaveBeenCalled();
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it.each([false, true])('preserves the install reserve with cached and missing wheels (enough=%s)', async (enough) => {
  const dir = await mkdtemp(join(tmpdir(), 'voice-wheel-mixed-'));
  const data = Buffer.from('cached-wheel');
  const missingData = Buffer.from('missing-wheel');
  const wheel = {
    name: 'torch', version: 'test', filename: 'torch-test.whl',
    url: 'https://download.pytorch.org/torch-test.whl',
    sha256: createHash('sha256').update(data).digest('hex'), size: data.length,
  };
  const missing = {
    name: 'dependency', version: 'test', filename: 'dependency-test.whl',
    url: 'https://files.pythonhosted.org/dependency-test.whl',
    sha256: createHash('sha256').update(missingData).digest('hex'), size: missingData.length,
  };
  const transport = vi.fn<RangeTransport>(async (url, start, end) =>
    (async function* () { yield (url === wheel.url ? data : missingData).subarray(start, end + 1); })(),
  );
  const options = {
    dir, connections: 2, transport, signal: new AbortController().signal,
    reserveBytes: 100, freeBytes: async () => 1000,
  };
  try {
    await prefetchWheels([wheel], options);
    transport.mockClear();
    const local = await prefetchWheels([wheel, missing], {
      ...options, freeBytes: async () => 100 + (enough ? missingData.length : 0),
    });
    expect(local.has(wheel.url)).toBe(true);
    expect(local.has(missing.url)).toBe(enough);
    if (enough) expect(transport.mock.calls.every(([url]) => url === missing.url)).toBe(true);
    else expect(transport).not.toHaveBeenCalled();
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it('does not discount a same-size cached file with the wrong hash', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'voice-wheel-corrupt-'));
  const data = Buffer.from('verified-wheel');
  const wheel = {
    name: 'torch', version: 'test', filename: 'torch-test.whl',
    url: 'https://download.pytorch.org/torch-test.whl',
    sha256: createHash('sha256').update(data).digest('hex'), size: data.length,
  };
  const transport = vi.fn<RangeTransport>(async (_url, start, end) =>
    (async function* () { yield data.subarray(start, end + 1); })(),
  );
  const options = {
    dir, connections: 2, transport, signal: new AbortController().signal,
    reserveBytes: 100, freeBytes: async () => 1000,
  };
  try {
    const first = await prefetchWheels([wheel], options);
    await writeFile(fileURLToPath(first.get(wheel.url)!), Buffer.alloc(data.length));
    transport.mockClear();
    const lowSpace = await prefetchWheels([wheel], { ...options, freeBytes: async () => 100 });
    expect(lowSpace.size).toBe(0);
    expect(transport).not.toHaveBeenCalled();
    const repaired = await prefetchWheels([wheel], {
      ...options, freeBytes: async () => 100 + data.length,
    });
    expect(repaired.size).toBe(1);
    expect(transport).toHaveBeenCalled();
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
