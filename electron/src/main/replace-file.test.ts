// @vitest-environment node
import {
  chmod,
  lstat,
  mkdtemp,
  open,
  readdir,
  readFile,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { replaceFile, type ReplaceFileSystem } from './replace-file';

let directory: string;
beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), 'voicestudio-replace-'));
});
afterEach(async () => {
  await rm(directory, { recursive: true, force: true });
});

/** A disk that accepts the first half of a write and then fails (ENOSPC). */
const partialWrite: ReplaceFileSystem = {
  rename,
  open: (async (...args: Parameters<typeof open>) => {
    const handle = await open(...args);
    const write = handle.writeFile.bind(handle);
    handle.writeFile = (async (data: string | Uint8Array) => {
      const bytes = typeof data === 'string' ? Buffer.from(data) : data;
      await write(bytes.subarray(0, bytes.length >> 1));
      throw Object.assign(new Error('no space left on device'), { code: 'ENOSPC' });
    }) as typeof handle.writeFile;
    return handle;
  }) as typeof open,
};

describe('replaceFile', () => {
  it('creates a new destination', async () => {
    const target = join(directory, 'take.wav');
    await replaceFile(target, new Uint8Array([1, 2, 3]));
    expect([...(await readFile(target))]).toEqual([1, 2, 3]);
    expect(await readdir(directory)).toEqual(['take.wav']);
  });

  it('replaces an existing export completely', async () => {
    const target = join(directory, 'take.wav');
    await writeFile(target, 'old export that is longer');
    await replaceFile(target, 'new');
    expect(await readFile(target, 'utf8')).toBe('new');
    expect(await readdir(directory)).toEqual(['take.wav']);
  });

  it('keeps an existing export intact when the write fails partway', async () => {
    const target = join(directory, 'take.wav');
    await writeFile(target, 'complete previous export');
    await expect(replaceFile(target, 'replacement bytes', partialWrite)).rejects.toThrow(
      'no space left',
    );
    expect(await readFile(target, 'utf8')).toBe('complete previous export');
    expect(await readdir(directory)).toEqual(['take.wav']);
  });

  it('keeps an existing export intact when the final replacement fails', async () => {
    const target = join(directory, 'take.wav');
    await writeFile(target, 'complete previous export');
    const failingRename: ReplaceFileSystem = {
      open,
      rename: async () => {
        throw Object.assign(new Error('resource busy'), { code: 'EBUSY' });
      },
    };
    await expect(replaceFile(target, 'replacement', failingRename)).rejects.toThrow('busy');
    expect(await readFile(target, 'utf8')).toBe('complete previous export');
    expect(await readdir(directory)).toEqual(['take.wav']);
  });

  describe('Windows rename retries (#2560)', () => {
    const transient = () => Object.assign(new Error('operation not permitted'), { code: 'EPERM' });
    const noWait = async () => {};

    it('retries a transient EPERM on win32 until the rename succeeds', async () => {
      const target = join(directory, 'take.wav');
      await writeFile(target, 'old');
      let calls = 0;
      const flaky: ReplaceFileSystem = {
        open,
        rename: async (from, to) => {
          if (++calls < 4) throw transient();
          await rename(from, to);
        },
      };
      await replaceFile(target, 'new', flaky, { platform: 'win32', sleep: noWait });
      expect(calls).toBe(4);
      expect(await readFile(target, 'utf8')).toBe('new');
      expect(await readdir(directory)).toEqual(['take.wav']);
    });

    it('rejects with the last error and removes the temp file when EPERM persists', async () => {
      const target = join(directory, 'take.wav');
      await writeFile(target, 'old');
      let calls = 0;
      const delays: number[] = [];
      const stuck: ReplaceFileSystem = {
        open,
        rename: async () => {
          calls++;
          throw transient();
        },
      };
      await expect(
        replaceFile(target, 'new', stuck, {
          platform: 'win32',
          sleep: async (ms) => void delays.push(ms),
        }),
      ).rejects.toMatchObject({ code: 'EPERM' });
      expect(calls).toBe(8);
      expect(delays.reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(1000);
      expect(delays.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(2000);
      expect(await readFile(target, 'utf8')).toBe('old');
      expect(await readdir(directory)).toEqual(['take.wav']);
    });

    it('does not retry on other platforms or for non-transient errors', async () => {
      const target = join(directory, 'take.wav');
      for (const [platform, code] of [
        ['linux', 'EPERM'],
        ['win32', 'ENOSPC'],
      ] as const) {
        let calls = 0;
        const failing: ReplaceFileSystem = {
          open,
          rename: async () => {
            calls++;
            throw Object.assign(new Error('fail'), { code });
          },
        };
        await expect(
          replaceFile(target, 'new', failing, { platform, sleep: noWait }),
        ).rejects.toMatchObject({ code });
        expect(calls).toBe(1);
        expect(await readdir(directory)).toEqual([]);
      }
    });
  });

  it.skipIf(process.platform === 'win32')('keeps the permissions of a replaced file', async () => {
    const target = join(directory, 'take.wav');
    await writeFile(target, 'old');
    await chmod(target, 0o640);
    await replaceFile(target, 'new');
    expect((await stat(target)).mode & 0o777).toBe(0o640);
  });

  it.skipIf(process.platform === 'win32')('writes through a working symlink', async () => {
    const real = join(directory, 'real.wav');
    const link = join(directory, 'link.wav');
    await writeFile(real, 'old');
    await symlink(real, link);
    await replaceFile(link, 'new');
    expect(await readFile(real, 'utf8')).toBe('new');
    expect((await lstat(link)).isSymbolicLink()).toBe(true);
  });
});
