// @vitest-environment node
import { afterEach, beforeEach, expect, it } from 'vitest';
import {
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { localDataIdentity } from './local-data-identity';
let root: string;
const FILE = '.voicestudio-data-identity.json';
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'vs-opaque-data-'));
});
afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});
it('uses one opaque persisted UUID across restart and concurrent creators', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  const ids = await Promise.all(Array.from({ length: 6 }, () => localDataIdentity(directory)));
  expect(ids[0]).toMatch(/^[0-9a-f-]{36}$/);
  expect(new Set(ids).size).toBe(1);
  expect(await localDataIdentity(directory)).toBe(ids[0]);
  const text = await readFile(join(directory, FILE), 'utf8');
  expect(text).not.toContain(root);
  expect(Object.keys(JSON.parse(text)).sort()).toEqual(['dev', 'id', 'ino', 'version']);
  if (process.platform !== 'win32')
    expect((await stat(join(directory, FILE))).mode & 0o777).toBe(0o600);
});
it('preserves aliases and rename but separates copied metadata', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  const original = await localDataIdentity(directory);
  const alias = join(root, 'alias');
  await symlink(directory, alias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await localDataIdentity(alias)).toBe(original);
  await rm(alias);
  const moved = join(root, 'moved');
  await rename(directory, moved);
  expect(await localDataIdentity(moved)).toBe(original);
  const copied = join(root, 'copied');
  await cp(moved, copied, { recursive: true });
  const next = await localDataIdentity(copied);
  expect(next).toMatch(/^[0-9a-f-]{36}$/);
  expect(next).not.toBe(original);
  expect(await localDataIdentity(moved)).toBe(original);
});
it('does not create a missing root or invent identity after invalid metadata', async () => {
  const missing = join(root, 'missing');
  expect(await localDataIdentity(missing)).toBeUndefined();
  await expect(lstat(missing)).rejects.toMatchObject({ code: 'ENOENT' });
  const directory = join(root, 'data');
  await mkdir(directory);
  for (const text of ['invalid', '{}', 'x'.repeat(1025)]) {
    await writeFile(join(directory, FILE), text);
    expect(await localDataIdentity(directory)).toBeUndefined();
    expect(await readFile(join(directory, FILE), 'utf8')).toBe(text);
  }
});
it('rejects symlink metadata without modifying its target', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  const target = join(root, 'target');
  await writeFile(target, 'unchanged');
  if (process.platform === 'win32') {
    const folder = join(root, 'target-folder');
    await mkdir(folder);
    await symlink(folder, join(directory, FILE), 'junction');
  } else await symlink(target, join(directory, FILE));
  expect(await localDataIdentity(directory)).toBeUndefined();
  expect(await readFile(target, 'utf8')).toBe('unchanged');
});
it('leaves inaccessible publication unknown and preserves existing entries', async () => {
  const directory = join(root, 'data');
  await mkdir(directory);
  await mkdir(join(directory, FILE));
  expect(await localDataIdentity(directory)).toBeUndefined();
  expect((await lstat(join(directory, FILE))).isDirectory()).toBe(true);
});
