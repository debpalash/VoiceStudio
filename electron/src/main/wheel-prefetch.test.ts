// @vitest-environment node
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import type { get as httpsGet } from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  PREFETCH_MIN_BYTES,
  RangeUnsupportedError,
  downloadSegmented,
  httpsRangeTransport,
  localizeLockUrls,
  lockedWheels,
  parseDryRun,
  planWheelPrefetch,
  prefetchConnections,
  prefetchWheels,
  wheelMatchesHost,
  type RangeTransport,
} from './wheel-prefetch';

const LOCK = readFileSync(join(__dirname, '..', '..', '..', 'uv.lock'), 'utf8');
// The torch pin the real lock carries, so a torch bump does not break these
// lock-reading tests (the pin lives in pyproject's uv constraint-dependencies).
const TORCH = /"torch==([^"]+)"/.exec(
  readFileSync(join(__dirname, '..', '..', '..', 'pyproject.toml'), 'utf8'),
)![1];
const roots: string[] = [];
async function scratch(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'vs-prefetch-'));
  roots.push(root);
  return root;
}
afterEach(async () => {
  vi.unstubAllEnvs();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

const sha = (data: Buffer) => createHash('sha256').update(data).digest('hex');

/** Serves `data` by range, recording every request; `fail` can reject a request. */
function memoryTransport(
  data: Buffer,
  fail: (url: string, start: number, call: number) => Error | undefined = () => undefined,
) {
  const calls: Array<{ url: string; start: number; end: number }> = [];
  let active = 0;
  let peak = 0;
  const transport: RangeTransport = async (url, start, end) => {
    calls.push({ url, start, end });
    const error = fail(url, start, calls.length);
    if (error) throw error;
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 2));
    const slice = data.subarray(start, end + 1);
    return (async function* () {
      try {
        yield slice.subarray(0, Math.ceil(slice.length / 2));
        yield slice.subarray(Math.ceil(slice.length / 2));
      } finally {
        active -= 1;
      }
    })();
  };
  return { transport, calls, peak: () => peak };
}

describe('locked wheel planning (#3411)', () => {
  it('reads every locked wheel with its hash, and a 0 size where the index gives none', () => {
    const wheels = lockedWheels(LOCK);
    const torch = wheels.find(
      (wheel) => wheel.filename === `torch-${TORCH}+cu128-cp311-cp311-win_amd64.whl`,
    );
    expect(torch).toMatchObject({ name: 'torch', version: `${TORCH}+cu128`, size: 0 });
    expect(torch!.sha256).toMatch(/^[0-9a-f]{64}$/);
    const cudnn = wheels.find((wheel) =>
      wheel.filename.startsWith('nvidia_cudnn_cu12-9.10.2.21-py3-none-manylinux_2_27_x86_64'),
    );
    expect(cudnn!.size).toBeGreaterThan(PREFETCH_MIN_BYTES);
  });

  it.each([
    ['torch-2.8.0+cu128-cp311-cp311-win_amd64.whl', 'win32', 'x64', true],
    ['torch-2.8.0+cu128-cp311-cp311-win_amd64.whl', 'win32', 'arm64', true],
    ['torch-2.8.0+cu128-cp312-cp312-win_amd64.whl', 'win32', 'x64', false],
    ['torch-2.8.0-cp311-cp311-manylinux_2_28_x86_64.whl', 'linux', 'x64', true],
    ['torch-2.8.0-cp311-cp311-manylinux_2_28_aarch64.whl', 'linux', 'x64', false],
    ['torch-2.8.0-cp311-cp311-manylinux_2_28_aarch64.whl', 'linux', 'arm64', true],
    ['x-1-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl', 'linux', 'x64', true],
    ['x-1-cp39-abi3-musllinux_1_2_x86_64.whl', 'linux', 'x64', false],
    ['x-1-cp39-abi3-win_amd64.whl', 'win32', 'x64', true],
    ['torch-2.8.0-cp311-none-macosx_11_0_arm64.whl', 'darwin', 'arm64', true],
    ['torch-2.8.0-cp311-none-macosx_11_0_arm64.whl', 'darwin', 'x64', false],
    ['six-1.16.0-py2.py3-none-any.whl', 'linux', 'x64', true],
  ] as const)('%s on %s %s: %s', (filename, platform, arch, expected) => {
    expect(wheelMatchesHost(filename, { platform, arch, pythonMinor: 11 })).toBe(expected);
  });

  it('plans only the large, uncached wheels uv named, one exact match each', () => {
    const output = [
      'Using CPython 3.11.16 interpreter at: C:\\runtime\\.python\\python.exe',
      `DEBUG Identified uncached distribution: torch==${TORCH}+cu128`,
      'DEBUG Identified uncached distribution: nvidia-cudnn-cu12==9.10.2.21',
      'DEBUG Identified uncached distribution: six==1.17.0',
      'DEBUG Registry requirement already cached: nvidia-cublas-cu12==12.8.4.1',
    ].join('\n');
    const plan = planWheelPrefetch(LOCK, output, { platform: 'win32', arch: 'x64' });
    expect(plan.map((wheel) => wheel.filename)).toEqual([
      `torch-${TORCH}+cu128-cp311-cp311-win_amd64.whl`,
      'nvidia_cudnn_cu12-9.10.2.21-py3-none-win_amd64.whl',
    ]);
    // Cached packages and anything uv did not name are never fetched.
    expect(plan.some((wheel) => wheel.name === 'nvidia-cublas-cu12')).toBe(false);
  });

  it('plans nothing when the dry run format is not recognised', () => {
    expect(
      planWheelPrefetch(LOCK, 'Would install 240 packages', {
        platform: 'win32',
        arch: 'x64',
        pythonMinor: 11,
      }),
    ).toEqual([]);
    expect(parseDryRun('')).toEqual({ pythonMinor: null, uncached: [] });
  });

  it('points only the fetched URLs at local files and keeps every hash', () => {
    const [wheel] = planWheelPrefetch(
      LOCK,
      `Identified uncached distribution: torch==${TORCH}+cu128`,
      { platform: 'win32', arch: 'x64', pythonMinor: 11 },
    );
    const local = localizeLockUrls(LOCK, new Map([[wheel!.url, 'file:///C:/rt/w/torch.whl']]));
    const hashes = (lock: string) => [...lock.matchAll(/\bhash = "([^"]+)"/g)].map((m) => m[1]);
    expect(hashes(local)).toEqual(hashes(LOCK));
    expect(local).toContain(`url = "file:///C:/rt/w/torch.whl", hash = "sha256:${wheel!.sha256}"`);
    const before = LOCK.split('\n');
    expect(local.split('\n').filter((line, index) => line !== before[index])).toHaveLength(1);
  });

  it('reads the connection count, where 0 or 1 turns the prefetch off', () => {
    expect(prefetchConnections(undefined)).toBe(8);
    expect(prefetchConnections('4')).toBe(4);
    expect(prefetchConnections('1')).toBe(1);
    expect(prefetchConnections('64')).toBe(16);
    expect(prefetchConnections('nope')).toBe(8);
  });
});

describe('segmented download (#3411)', () => {
  const data = Buffer.from(Array.from({ length: 10_000 }, (_, index) => index % 251));

  it('fetches byte ranges in parallel and verifies the pinned hash', async () => {
    const dir = await scratch();
    const { transport, calls, peak } = memoryTransport(data);
    const progress: number[] = [];
    await downloadSegmented(join(dir, 'w.whl'), {
      urls: ['https://example.test/w.whl'],
      sha256: sha(data),
      size: data.length,
      connections: 4,
      chunkBytes: 1_000,
      transport,
      signal: new AbortController().signal,
      onProgress: (received) => progress.push(received),
    });
    expect((await readFile(join(dir, 'w.whl'))).equals(data)).toBe(true);
    expect(calls).toHaveLength(10);
    expect(peak()).toBeGreaterThan(1);
    expect(progress.at(-1)).toBe(data.length);
    expect(existsSync(join(dir, 'w.whl.part.json'))).toBe(false);
  });

  it('resumes the chunks an interrupted attempt finished', async () => {
    const dir = await scratch();
    const dest = join(dir, 'w.whl');
    const broken = memoryTransport(data, (_url, start) =>
      start >= 6_000 ? Object.assign(new Error('reset'), { code: 'ECONNRESET' }) : undefined,
    );
    await expect(
      downloadSegmented(dest, {
        urls: ['https://example.test/w.whl'],
        sha256: sha(data),
        size: data.length,
        connections: 1,
        chunkBytes: 1_000,
        transport: broken.transport,
        signal: new AbortController().signal,
        retryDelayMs: 1,
      }),
    ).rejects.toThrow('reset');
    const resumed = memoryTransport(data);
    await downloadSegmented(dest, {
      urls: ['https://example.test/w.whl'],
      sha256: sha(data),
      size: data.length,
      connections: 2,
      chunkBytes: 1_000,
      transport: resumed.transport,
      signal: new AbortController().signal,
    });
    expect(resumed.calls.map((call) => call.start).sort((a, b) => a - b)).toEqual([
      6_000, 7_000, 8_000, 9_000,
    ]);
    expect((await readFile(dest)).equals(data)).toBe(true);
  });

  it('moves from a mirror that cannot serve the file to the origin', async () => {
    const dir = await scratch();
    const { transport, calls } = memoryTransport(data);
    const refusing: RangeTransport = (url, start, end, signal) =>
      url.includes('mirror')
        ? Promise.reject(new RangeUnsupportedError(url))
        : transport(url, start, end, signal);
    await downloadSegmented(join(dir, 'w.whl'), {
      urls: ['https://mirror.test/w.whl', 'https://origin.test/w.whl'],
      sha256: sha(data),
      size: data.length,
      connections: 3,
      chunkBytes: 2_500,
      transport: refusing,
      signal: new AbortController().signal,
    });
    expect(calls.every((call) => call.url.includes('origin'))).toBe(true);
    expect((await readFile(join(dir, 'w.whl'))).equals(data)).toBe(true);
  });

  it('deletes a file that does not match the lock instead of handing it to uv', async () => {
    const dir = await scratch();
    const { transport } = memoryTransport(data);
    await expect(
      downloadSegmented(join(dir, 'w.whl'), {
        urls: ['https://example.test/w.whl'],
        sha256: '0'.repeat(64),
        size: data.length,
        connections: 2,
        chunkBytes: 4_000,
        transport,
        signal: new AbortController().signal,
      }),
    ).rejects.toThrow('does not match the hash');
    expect(existsSync(join(dir, 'w.whl'))).toBe(false);
    expect(existsSync(join(dir, 'w.whl.part'))).toBe(false);
  });

  it('stops at once when setup is cancelled', async () => {
    const dir = await scratch();
    const controller = new AbortController();
    const { transport } = memoryTransport(data, (_url, _start, call) => {
      if (call === 2) controller.abort(new Error('cancelled'));
      return undefined;
    });
    await expect(
      downloadSegmented(join(dir, 'w.whl'), {
        urls: ['https://example.test/w.whl'],
        sha256: sha(data),
        size: data.length,
        connections: 1,
        chunkBytes: 1_000,
        transport,
        signal: controller.signal,
      }),
    ).rejects.toThrow('cancelled');
    expect(existsSync(join(dir, 'w.whl'))).toBe(false);
  });
});

describe('prefetching planned wheels (#3411)', () => {
  const data = Buffer.alloc(PREFETCH_MIN_BYTES + 5, 7);
  const wheel = {
    name: 'torch',
    version: '2.8.0+cu128',
    url: 'https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl',
    filename: 'torch-2.8.0+cu128-cp311-cp311-win_amd64.whl',
    sha256: sha(data),
    size: 0,
  };

  it('measures an unsized wheel, fetches it and maps its lock URL to the local file', async () => {
    const dir = join(await scratch(), 'prefetch');
    const { transport } = memoryTransport(data);
    const log = vi.fn();
    const transfer = vi.fn();
    const local = await prefetchWheels([wheel], {
      dir,
      connections: 8,
      transport,
      probeSize: async () => data.length,
      signal: new AbortController().signal,
      chunkBytes: 16 * 1024 ** 2,
      log,
      transfer,
    });
    const fileUrl = local.get(wheel.url)!;
    expect(fileUrl).toMatch(/^file:\/\/.*torch-2\.8\.0%2Bcu128-cp311-cp311-win_amd64\.whl$/);
    expect(sha(await readFile(fileURLToPath(fileUrl)))).toBe(wheel.sha256);
    expect(transfer).toHaveBeenLastCalledWith('torch', data.length, data.length);
    expect(log).toHaveBeenCalledWith(expect.stringContaining('over 8 connections'));
    for (const [line] of log.mock.calls) expect(line).not.toMatch(/^Download(ing|ed)\b/);
  });

  it('leaves a wheel to uv when its download fails, and withdraws its progress', async () => {
    const dir = join(await scratch(), 'prefetch');
    const transport: RangeTransport = (url) => Promise.reject(new RangeUnsupportedError(url));
    const log = vi.fn();
    const transfer = vi.fn();
    const local = await prefetchWheels([{ ...wheel, size: data.length }], {
      dir,
      connections: 8,
      transport,
      signal: new AbortController().signal,
      log,
      transfer,
    });
    expect(local.size).toBe(0);
    expect(transfer).toHaveBeenLastCalledWith('torch', 0, 0);
    expect(log).toHaveBeenCalledWith(expect.stringContaining('uv will download it instead'));
  });

  it('skips the prefetch when the disk cannot hold the wheels until uv installs them', async () => {
    const transport = vi.fn<RangeTransport>();
    const local = await prefetchWheels([{ ...wheel, size: data.length }], {
      dir: join(await scratch(), 'prefetch'),
      connections: 8,
      transport,
      signal: new AbortController().signal,
      reserveBytes: 10,
      freeBytes: async () => data.length,
    });
    expect(local.size).toBe(0);
    expect(transport).not.toHaveBeenCalled();
  });

  it('reuses a wheel an earlier attempt finished and drops wheels the lock no longer needs', async () => {
    const dir = join(await scratch(), 'prefetch');
    const first = memoryTransport(data);
    const options = {
      dir,
      connections: 4,
      signal: new AbortController().signal,
      chunkBytes: 32 * 1024 ** 2,
    };
    await prefetchWheels([{ ...wheel, size: data.length }], {
      ...options,
      transport: first.transport,
    });
    const stale = join(dir, 'stale0000000000');
    await writeFile(stale, 'old');
    const second = memoryTransport(data);
    const local = await prefetchWheels([{ ...wheel, size: data.length }], {
      ...options,
      transport: second.transport,
    });
    expect(second.calls).toHaveLength(0);
    expect(local.size).toBe(1);
    expect(existsSync(stale)).toBe(false);
  });
});

describe('HTTPS range transport (#3411)', () => {
  function fakeGet(status: number, headers: Record<string, string>, body = 'abc') {
    const calls: unknown[][] = [];
    const get = ((...args: unknown[]) => {
      calls.push(args);
      const callback = args[2] as (response: unknown) => void;
      const response = Object.assign(new PassThrough(), {
        statusCode: status,
        headers,
        setTimeout: () => undefined,
      });
      const request = Object.assign(new EventEmitter(), { setTimeout: () => undefined });
      setImmediate(() => {
        callback(response);
        response.end(body);
      });
      return request;
    }) as unknown as typeof httpsGet;
    return Object.assign(get, { calls });
  }

  it('sends a Range request with a User-Agent and accepts the matching 206', async () => {
    const get = fakeGet(206, { 'content-range': 'bytes 0-2/3461420395' });
    const { transport, close } = httpsRangeTransport({}, get);
    const body = await transport('https://x.test/a.whl', 0, 2, new AbortController().signal);
    const chunks: Buffer[] = [];
    for await (const chunk of body) chunks.push(chunk as Buffer);
    expect(Buffer.concat(chunks).toString()).toBe('abc');
    const options = get.calls[0]![1] as { headers: Record<string, string> };
    expect(options.headers).toMatchObject({
      Range: 'bytes=0-2',
      'User-Agent': 'VoiceStudio-setup',
    });
    const sizing = httpsRangeTransport(
      {},
      fakeGet(206, { 'content-range': 'bytes 0-0/3461420395' }),
    );
    expect(await sizing.probeSize('https://x.test/a.whl', new AbortController().signal)).toBe(
      3461420395,
    );
    close();
    sizing.close();
  });

  it('refuses a server that ignores the range, and plain HTTP', async () => {
    const whole = httpsRangeTransport({}, fakeGet(200, {}));
    await expect(
      whole.transport('https://x.test/a.whl', 0, 2, new AbortController().signal),
    ).rejects.toBeInstanceOf(RangeUnsupportedError);
    await expect(
      whole.transport('http://x.test/a.whl', 0, 2, new AbortController().signal),
    ).rejects.toThrow('HTTPS');
    whole.close();
  });
});
