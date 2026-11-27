import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';

const fs = vi.hoisted(() => ({ fail: [] as (() => void)[] }));

vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>();
  return {
    ...actual,
    rmSync: vi.fn((...args: Parameters<typeof actual.rmSync>) => {
      fs.fail.shift()?.();
      return actual.rmSync(...args);
    }),
  };
});

const { rmSync } = await import('node:fs');
const { removeTempDir } = await import('./remove-temp-dir');

function coded(code: string) {
  return Object.assign(new Error(code), { code });
}

afterEach(() => {
  fs.fail.length = 0;
  vi.mocked(rmSync).mockClear();
});

it('restarts the whole removal when a file lands after the folder was listed', () => {
  const dir = mkdtempSync(join(tmpdir(), 'remove-temp-dir-'));
  mkdirSync(join(dir, 'logs'));
  fs.fail.push(() => {
    // The late writer: a file appears, then rmdir reports the folder not empty.
    writeFileSync(join(dir, 'logs', 'late.log'), 'x');
    throw coded('ENOTEMPTY');
  });
  removeTempDir(dir);
  expect(existsSync(dir)).toBe(false);
  expect(rmSync).toHaveBeenCalledTimes(2);
});

it('does not retry an unrelated failure', () => {
  fs.fail.push(() => {
    throw coded('EACCES');
  });
  expect(() => removeTempDir('/nonexistent-remove-temp-dir')).toThrow('EACCES');
  expect(rmSync).toHaveBeenCalledTimes(1);
});

it('gives up after the last attempt', () => {
  for (let i = 0; i < 3; i++)
    fs.fail.push(() => {
      throw coded('EBUSY');
    });
  expect(() => removeTempDir('/nonexistent-remove-temp-dir', 3)).toThrow('EBUSY');
  expect(rmSync).toHaveBeenCalledTimes(3);
});
