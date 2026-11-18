// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { mkdir, mkdtemp, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// A reader racing a concurrent publication can see the entry vanish and appear
// between its open and lstat, or see a different file id on the handle and the
// path. Both are contention, not failure: re-read, never return unknown.
const chaos = vi.hoisted(() => ({
  openEnoent: 0,
  lstatIno: 0,
  lstatInoAlways: false,
}));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    open: (async (path: string, ...rest: unknown[]) => {
      if (String(path).endsWith('.json') && chaos.openEnoent > 0) {
        chaos.openEnoent--;
        throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
      }
      return (actual.open as (...args: unknown[]) => unknown)(path, ...rest);
    }) as typeof actual.open,
    lstat: (async (path: string, ...rest: unknown[]) => {
      const info = (await (actual.lstat as (...args: unknown[]) => unknown)(path, ...rest)) as {
        ino: bigint;
      };
      if (String(path).endsWith('.json') && (chaos.lstatIno > 0 || chaos.lstatInoAlways)) {
        chaos.lstatIno--;
        return new Proxy(info, {
          get: (target, key) =>
            key === 'ino'
              ? target.ino + 1n
              : (Reflect.get(target, key) as unknown) instanceof Function
                ? (Reflect.get(target, key) as () => unknown).bind(target)
                : Reflect.get(target, key),
        });
      }
      return info;
    }) as typeof actual.lstat,
  };
});

import {
  localDataIdentity,
  localDataIdentityFailures,
  resetLocalDataIdentityFailures,
} from './local-data-identity';

let root: string;
let directory: string;
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'vs-identity-race-'));
  directory = join(root, 'data');
  await mkdir(directory);
  resetLocalDataIdentityFailures();
});
afterEach(async () => {
  Object.assign(chaos, { openEnoent: 0, lstatIno: 0, lstatInoAlways: false });
  await rm(root, { recursive: true, force: true });
});

it('re-reads an identity published between a read open (ENOENT) and its lstat', async () => {
  const id = await localDataIdentity(directory);
  expect(id).toMatch(/^[0-9a-f-]{36}$/);
  chaos.openEnoent = 1;
  expect(await localDataIdentity(directory)).toBe(id);
  expect(localDataIdentityFailures()).toEqual([]);
});

it('re-reads when the handle and the path disagree on file id mid-replace', async () => {
  const id = await localDataIdentity(directory);
  chaos.lstatIno = 2;
  expect(await localDataIdentity(directory)).toBe(id);
  expect(localDataIdentityFailures()).toEqual([]);
});

it('stays unknown, with a recorded stage and code, when the ids never agree', async () => {
  await localDataIdentity(directory);
  chaos.lstatInoAlways = true;
  expect(await localDataIdentity(directory)).toBeUndefined();
  expect(localDataIdentityFailures()).toEqual([{ stage: 'read', code: 'RACING' }]);
});

it('records the stage and code, never a path, when the root is missing', async () => {
  expect(await localDataIdentity(join(root, 'missing'))).toBeUndefined();
  const failures = localDataIdentityFailures();
  expect(failures).toEqual([{ stage: 'root', code: 'ENOENT' }]);
  expect(JSON.stringify(failures)).not.toContain(root);
});

it('never follows a symlinked identity file', async (context) => {
  try {
    await symlink(join(root, 'elsewhere'), join(directory, '.voicestudio-data-identity.json'));
  } catch (error) {
    // Windows without the symlink privilege or Developer Mode cannot create
    // a file symlink; a junction would test a directory, not this case.
    if ((error as NodeJS.ErrnoException).code === 'EPERM') return context.skip();
    throw error;
  }
  expect(await localDataIdentity(directory)).toBeUndefined();
});
