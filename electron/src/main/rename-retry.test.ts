import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  DIRECTORY_RENAME,
  FILE_RENAME,
  renameSyncWithRetry,
  renameWithRetry,
  retryTransient,
} from './rename-retry';

function failing(codes: string[]) {
  const calls: string[] = [];
  const rename = async (from: string, to: string) => {
    calls.push(`${from}->${to}`);
    const code = codes.shift();
    if (code) throw Object.assign(new Error(code), { code });
  };
  return { calls, rename };
}

describe('renameWithRetry', () => {
  it('retries a transient Windows lock until the rename succeeds (#2669)', async () => {
    const fs = failing(['EPERM', 'EBUSY', 'EACCES']);
    const waits: number[] = [];
    await renameWithRetry('.incoming-backend', 'backend', {
      ...DIRECTORY_RENAME,
      platform: 'win32',
      rename: fs.rename,
      sleep: async (ms) => void waits.push(ms),
    });
    expect(fs.calls).toHaveLength(4);
    expect(waits).toEqual([25, 50, 100]);
  });

  it('gives up after the attempt budget with the last error', async () => {
    const fs = failing(Array(20).fill('EPERM'));
    const waits: number[] = [];
    await expect(
      renameWithRetry('a', 'b', {
        ...DIRECTORY_RENAME,
        platform: 'win32',
        rename: fs.rename,
        sleep: async (ms) => void waits.push(ms),
      }),
    ).rejects.toMatchObject({ code: 'EPERM' });
    expect(fs.calls).toHaveLength(DIRECTORY_RENAME.attempts!);
    expect(Math.max(...waits)).toBe(DIRECTORY_RENAME.capMs);
    expect(waits.reduce((a, b) => a + b, 0)).toBeGreaterThan(5000);
  });

  it('fails at once on other errors and other platforms', async () => {
    const missing = failing(['ENOENT']);
    await expect(
      renameWithRetry('a', 'b', {
        platform: 'win32',
        rename: missing.rename,
        sleep: async () => {},
      }),
    ).rejects.toMatchObject({ code: 'ENOENT' });
    expect(missing.calls).toHaveLength(1);
    const linux = failing(['EPERM']);
    await expect(
      renameWithRetry('a', 'b', { platform: 'linux', rename: linux.rename, sleep: async () => {} }),
    ).rejects.toMatchObject({ code: 'EPERM' });
    expect(linux.calls).toHaveLength(1);
  });

  it('retries any filesystem step a Windows lock blocks, not only rename', async () => {
    let calls = 0;
    const result = await retryTransient(
      async () => {
        if (++calls < 3) throw Object.assign(new Error('EBUSY'), { code: 'EBUSY' });
        return 'copied';
      },
      { platform: 'win32', sleep: async () => {} },
    );
    expect(result).toBe('copied');
    expect(calls).toBe(3);
  });

  it('is the only main-process module that renames through node:fs directly', () => {
    const offenders = readdirSync(__dirname, { recursive: true, encoding: 'utf8' })
      .map((name) => name.replaceAll('\\', '/'))
      .filter(
        (name) => name.endsWith('.ts') && !name.endsWith('.test.ts') && name !== 'rename-retry.ts',
      )
      .filter((name) =>
        /(?<![\w.])rename(?:Sync)?\(/.test(readFileSync(join(__dirname, name), 'utf8')),
      );
    expect(offenders).toEqual([]);
  });
});

describe('renameSyncWithRetry', () => {
  function failingSync(codes: string[]) {
    const calls: string[] = [];
    const rename = (from: string, to: string) => {
      calls.push(`${from}->${to}`);
      const code = codes.shift();
      if (code) throw Object.assign(new Error(code), { code });
    };
    return { calls, rename };
  }

  it('replaces a journal Defender still holds on Windows', () => {
    const fs = failingSync(['EPERM', 'EBUSY']);
    const waits: number[] = [];
    renameSyncWithRetry('crashes.json.tmp', 'crashes.json', {
      platform: 'win32',
      rename: fs.rename,
      sleep: (ms) => void waits.push(ms),
    });
    expect(fs.calls).toHaveLength(3);
    expect(waits).toEqual([25, 50]);
  });

  it('gives up after the file budget with the last error', () => {
    const fs = failingSync(Array(20).fill('EACCES'));
    const waits: number[] = [];
    expect(() =>
      renameSyncWithRetry('a', 'b', {
        platform: 'win32',
        rename: fs.rename,
        sleep: (ms) => void waits.push(ms),
      }),
    ).toThrow(expect.objectContaining({ code: 'EACCES' }));
    expect(fs.calls).toHaveLength(FILE_RENAME.attempts!);
    expect(Math.max(...waits)).toBe(FILE_RENAME.capMs);
  });

  it('fails at once on other errors and other platforms', () => {
    const missing = failingSync(['ENOENT']);
    expect(() =>
      renameSyncWithRetry('a', 'b', { platform: 'win32', rename: missing.rename, sleep: () => {} }),
    ).toThrow(expect.objectContaining({ code: 'ENOENT' }));
    expect(missing.calls).toHaveLength(1);
    const linux = failingSync(['EPERM']);
    expect(() =>
      renameSyncWithRetry('a', 'b', { platform: 'linux', rename: linux.rename, sleep: () => {} }),
    ).toThrow(expect.objectContaining({ code: 'EPERM' }));
    expect(linux.calls).toHaveLength(1);
  });

  it('waits for real between attempts by default', () => {
    const fs = failingSync(['EBUSY']);
    const started = performance.now();
    renameSyncWithRetry('a', 'b', { platform: 'win32', rename: fs.rename });
    expect(fs.calls).toHaveLength(2);
    expect(performance.now() - started).toBeGreaterThanOrEqual(20);
  });
});
