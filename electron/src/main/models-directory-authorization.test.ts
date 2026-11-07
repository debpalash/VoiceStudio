import { chmod, mkdir, mkdtemp, readFile, readdir, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { authorizeModelsDirectory, authorizeModelsDirectoryReadOnly } from './media-authorization';

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

it('mints one-shot models-directory capabilities only after a writable directory check', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voicestudio-model-dir-'));
  roots.push(root);
  const dataDir = join(root, 'data');
  const selected = join(root, 'model cache');

  const result = await authorizeModelsDirectory(dataDir, selected);
  expect(result.path).toBe(await realpath(selected));
  const payload = JSON.parse(
    await readFile(join(dataDir, '.path-authorizations', `${result.authorization}.json`), 'utf8'),
  );
  expect(payload).toEqual({
    token: result.authorization,
    kind: 'models_dir',
    path: await realpath(selected),
  });
});

it('authorizes a parent folder without creating or probing files', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voicestudio-model-parent-'));
  roots.push(root);
  const selected = join(root, 'existing-parent');
  await mkdir(selected);
  await chmod(selected, 0o555);
  try {
    const result = await authorizeModelsDirectoryReadOnly(join(root, 'data'), selected);
    expect(result.path).toBe(await realpath(selected));
    await expect(readdir(selected)).resolves.toEqual([]);
  } finally {
    await chmod(selected, 0o755);
  }
});

it('represents restoring the platform default with an authorized empty path', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voicestudio-model-reset-'));
  roots.push(root);

  const result = await authorizeModelsDirectory(join(root, 'data'), '');
  const payload = JSON.parse(
    await readFile(
      join(root, 'data', '.path-authorizations', `${result.authorization}.json`),
      'utf8',
    ),
  );
  expect(result.path).toBe('');
  expect(payload.path).toBe('');
});
