// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Windows reports a racing open of a file another creator is deleting or
// replacing ("delete pending") as EPERM, not EEXIST/ENOENT. Simulate one such
// refusal per matching path and check the identity is still found, not unknown.
const refuse = vi.hoisted(() => ({ suffixes: new Set<string>() }));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    open: (async (path: string, ...rest: unknown[]) => {
      for (const suffix of refuse.suffixes)
        if (String(path).endsWith(suffix)) {
          refuse.suffixes.delete(suffix);
          throw Object.assign(new Error('EPERM: operation not permitted'), { code: 'EPERM' });
        }
      return (actual.open as (...args: unknown[]) => unknown)(path, ...rest);
    }) as typeof actual.open,
  };
});

import { localDataIdentity } from './local-data-identity';

const FILE = '.voicestudio-data-identity.json';
const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
let root: string;
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'vs-identity-win-'));
  Object.defineProperty(process, 'platform', { value: 'win32' });
});
afterEach(async () => {
  Object.defineProperty(process, 'platform', platform);
  refuse.suffixes.clear();
  await rm(root, { recursive: true, force: true });
});

it('retries a lock another creator is still deleting on Windows', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  refuse.suffixes.add(`${FILE}.lock`);
  const id = await localDataIdentity(directory);
  expect(id).toMatch(/^[0-9a-f-]{36}$/);
  expect(await localDataIdentity(directory)).toBe(id);
});

it('retries an identity file another creator is still replacing on Windows', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  const id = await localDataIdentity(directory);
  refuse.suffixes.add(FILE);
  expect(await localDataIdentity(directory)).toBe(id);
});

it('keeps a non-Windows EPERM a failure, never a guessed identity', async () => {
  Object.defineProperty(process, 'platform', { value: 'linux' });
  const directory = join(root, 'data');
  await mkdir(directory);
  await writeFile(join(directory, 'placeholder'), '');
  refuse.suffixes.add(`${FILE}.lock`);
  expect(await localDataIdentity(directory)).toBeUndefined();
});
