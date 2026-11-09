// @vitest-environment node
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AUTO_PYPI_MIRROR,
  PYPI_FILES_ORIGIN,
  PYPI_SPEED_SAMPLE_BYTES,
  mirrorLockUrls,
  normalizeLockHosts,
  pypiFilesHost,
  pypiFilesMirror,
  raceAutoPypiMirror,
  speedSampleUrl,
  timeSample,
} from './pypi-mirror';

const LOCK = readFileSync(join(__dirname, '..', '..', '..', 'uv.lock'), 'utf8');
const urls = (lock: string) => [...lock.matchAll(/\burl = "([^"]+)"/g)].map((m) => m[1]!);
const hashes = (lock: string) => [...lock.matchAll(/\bhash = "([^"]+)"/g)].map((m) => m[1]!);

describe('regional PyPI file mirror for the locked install (#3444)', () => {
  const mirror = pypiFilesMirror('china')!;
  const mirrored = mirrorLockUrls(LOCK, mirror);

  it('maps only the china region to a sanctioned mirror', () => {
    expect(mirror).toBe('https://mirrors.aliyun.com/pypi/packages/');
    expect(pypiFilesHost('china')).toBe('mirrors.aliyun.com');
    for (const region of ['auto', 'global', 'russia', 'restricted']) {
      expect(pypiFilesMirror(region)).toBeNull();
      expect(pypiFilesHost(region)).toBe('files.pythonhosted.org');
    }
  });

  it('keeps every pinned hash and changes only the PyPI host', () => {
    expect(LOCK).toContain(PYPI_FILES_ORIGIN);
    expect(mirrored).not.toContain(PYPI_FILES_ORIGIN);
    expect(hashes(mirrored)).toEqual(hashes(LOCK));
    const before = urls(LOCK);
    const after = urls(mirrored);
    expect(after).toHaveLength(before.length);
    after.forEach((url, i) => {
      const original = before[i]!;
      if (original.startsWith(PYPI_FILES_ORIGIN)) {
        expect(url).toBe(mirror + original.slice(PYPI_FILES_ORIGIN.length));
      } else {
        // PyTorch's index, GitHub and registry sources have no sanctioned mirror.
        expect(url).toBe(original);
      }
    });
    // Nothing but those URL prefixes differs.
    expect(mirrored.split(mirror).join(PYPI_FILES_ORIGIN)).toBe(LOCK);
    expect(mirrored).toContain('https://download-r2.pytorch.org/');
  });

  it('leaves the lock byte-identical without a mirror', () => {
    expect(mirrorLockUrls(LOCK, null)).toBe(LOCK);
    expect(mirrorLockUrls(LOCK, pypiFilesMirror('global'))).toBe(LOCK);
  });

  it('normalizes a mirrored lock back to the original', () => {
    expect(normalizeLockHosts(mirrored)).toBe(LOCK);
    expect(normalizeLockHosts(LOCK)).toBe(LOCK);
  });
});

describe('"Auto" PyPI mirror speed check (#3444)', () => {
  afterEach(() => vi.unstubAllGlobals());
  const signal = new AbortController().signal;
  const sample = speedSampleUrl(LOCK)!;
  const onMirror = AUTO_PYPI_MIRROR + sample.slice(PYPI_FILES_ORIGIN.length);
  const timer = (origin: number | null, mirror: number | null) =>
    vi.fn(async (url: string) => (url.startsWith(PYPI_FILES_ORIGIN) ? origin : mirror));

  it('samples one locked PyPI wheel large enough to time', () => {
    expect(sample.startsWith(PYPI_FILES_ORIGIN)).toBe(true);
    expect(sample.endsWith('.whl')).toBe(true);
    expect(speedSampleUrl('')).toBeNull();
  });

  it('races the same file on both hosts with exactly two requests', async () => {
    const time = timer(900, 100);
    expect(await raceAutoPypiMirror(LOCK, signal, time)).toEqual({
      mirror: AUTO_PYPI_MIRROR,
      originMs: 900,
      mirrorMs: 100,
    });
    expect(time.mock.calls.map(([url]) => url).sort()).toEqual([sample, onMirror].sort());
  });

  it.each([
    ['PyPI is unreachable', null, 400, AUTO_PYPI_MIRROR],
    ['the mirror is unreachable', 400, null, null],
    ['both fail', null, null, null],
    ['the mirror is not clearly faster', 400, 330, null],
    ['PyPI is faster', 100, 900, null],
  ])('when %s', async (_case, origin, mirror, expected) => {
    expect((await raceAutoPypiMirror(LOCK, signal, timer(origin, mirror))).mirror).toBe(expected);
  });

  it('keeps PyPI without any request when the lock has no sample wheel', async () => {
    const time = timer(900, 1);
    expect((await raceAutoPypiMirror('', signal, time)).mirror).toBeNull();
    expect(time).not.toHaveBeenCalled();
  });

  it('times one ranged GET with a User-Agent and fails on anything short of the sample', async () => {
    const fetch = vi.fn(
      async () => new Response(new Uint8Array(PYPI_SPEED_SAMPLE_BYTES), { status: 206 }),
    );
    vi.stubGlobal('fetch', fetch);
    expect(await timeSample(sample, signal)).toEqual(expect.any(Number));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]).toEqual([
      sample,
      expect.objectContaining({
        headers: expect.objectContaining({
          Range: `bytes=0-${PYPI_SPEED_SAMPLE_BYTES - 1}`,
          'User-Agent': 'VoiceStudio-setup',
        }),
      }),
    ]);
    fetch.mockImplementationOnce(async () => new Response(new Uint8Array(10), { status: 206 }));
    expect(await timeSample(sample, signal)).toBeNull();
    fetch.mockImplementationOnce(async () => new Response('denied', { status: 403 }));
    expect(await timeSample(sample, signal)).toBeNull();
    fetch.mockImplementationOnce(async () => {
      throw new Error('ECONNRESET');
    });
    expect(await timeSample(sample, signal)).toBeNull();
  });
});
