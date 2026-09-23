// @vitest-environment node
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import {
  inspectDataRelocation,
  prepareDataRelocation,
  readDataDirectorySetting,
  recordPreviousVoicesRoot,
  relocateDataDirectory,
  writeDataDirectorySetting,
} from './data-relocation';

const created: string[] = [];

afterEach(async () => {
  await Promise.allSettled(
    created.splice(0).map((path) => rm(path, { recursive: true, force: true })),
  );
});

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'VoiceStudio-relocation-test-'));
  created.push(root);
  const source = join(root, 'OmniVoice', 'data');
  const target = join(root, 'new-home', 'VoiceStudio');
  await mkdir(join(source, 'voices'), { recursive: true });
  await mkdir(dirname(target), { recursive: true });
  await writeFile(join(source, 'omnivoice.db'), 'database');
  await writeFile(join(source, 'voices', 'sample.wav'), 'voice');
  return { root, source, target };
}

it('copies and verifies app data before exposing source cleanup', async () => {
  const { source, target } = await fixture();
  const plan = await inspectDataRelocation(source, target);
  expect(plan).toMatchObject({ size_bytes: 13, file_count: 2 });
  const prepared = await prepareDataRelocation(source, target);
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(existsSync(source)).toBe(true);
  await prepared.removeSource();
  expect(existsSync(source)).toBe(false);
});

it('rolls back only the verified destination and keeps the source', async () => {
  const { source, target } = await fixture();
  const prepared = await prepareDataRelocation(source, target);
  await prepared.rollback();
  expect(existsSync(target)).toBe(false);
  expect(existsSync(join(source, 'omnivoice.db'))).toBe(true);
});

it('rejects nested and non-empty destinations', async () => {
  const { source, target } = await fixture();
  await expect(inspectDataRelocation(source, join(source, 'moved'))).rejects.toThrow('nested_path');
  await mkdir(target, { recursive: true });
  await writeFile(join(target, 'personal.txt'), 'keep');
  await expect(inspectDataRelocation(source, target)).rejects.toThrow('target_not_empty');
  expect(await readFile(join(target, 'personal.txt'), 'utf8')).toBe('keep');
});

it('updates only the data-directory line in the shared durable environment', async () => {
  const { root, target } = await fixture();
  const env = join(root, 'config', 'env');
  await mkdir(dirname(env), { recursive: true });
  await writeFile(env, 'HF_TOKEN=secret\nOMNIVOICE_DATA_DIR=/old\n');
  expect(await readDataDirectorySetting(env)).toBe('/old');
  const quotedTarget = join(dirname(target), "Voice Studio's data");
  await writeDataDirectorySetting(quotedTarget, env);
  expect(await readDataDirectorySetting(env)).toBe(quotedTarget);
  expect(await readFile(env, 'utf8')).toContain('HF_TOKEN=secret\n');
  await writeDataDirectorySetting(null, env);
  expect(await readDataDirectorySetting(env)).toBeNull();
  expect(await readFile(env, 'utf8')).toBe('HF_TOKEN=secret\n');
});

it('restores the setting and original backend when activation cannot be verified', async () => {
  const { root, source, target } = await fixture();
  const env = join(root, 'config', 'env');
  await mkdir(dirname(env), { recursive: true });
  await writeFile(env, `OMNIVOICE_DATA_DIR=${source}\nHF_TOKEN=secret\n`);
  const calls: string[] = [];

  await expect(
    relocateDataDirectory(source, target, {
      environmentPath: env,
      stop: async () => {
        calls.push('stop');
      },
      start: async () => {
        calls.push('start');
      },
      verify: async () => false,
      progress: (stage) => calls.push(stage),
    }),
  ).rejects.toThrow('backend_verification_failed');

  expect(await readDataDirectorySetting(env)).toBe(source);
  expect(existsSync(target)).toBe(false);
  expect(existsSync(join(source, 'omnivoice.db'))).toBe(true);
  expect(calls.filter((call) => call === 'stop')).toHaveLength(2);
  expect(calls.filter((call) => call === 'start')).toHaveLength(2);
  expect(calls).toContain('rolling_back');
});

it('records the old voices root in a moved longform cache (#2279)', async () => {
  const { root, source, target } = await fixture();
  const cache = join(source, 'outputs', 'longform_cache');
  await mkdir(cache, { recursive: true });
  // What the backend recorded at startup under the old data dir.
  await writeFile(join(cache, 'voices_roots.json'), JSON.stringify(['/spelled/by/backend/voices']));
  const env = join(root, 'config', 'env');
  await mkdir(dirname(env), { recursive: true });
  const canonicalSource = await realpath(source);
  const result = await relocateDataDirectory(source, target, {
    environmentPath: env,
    stop: async () => undefined,
    start: async () => undefined,
    verify: async () => true,
    progress: () => undefined,
  });
  expect(result.removed_source).toBe(true);
  const roots = JSON.parse(
    await readFile(join(result.path, 'outputs', 'longform_cache', 'voices_roots.json'), 'utf8'),
  );
  expect(roots).toEqual([join(canonicalSource, 'voices'), '/spelled/by/backend/voices']);
});

it('records nothing without a longform cache and is idempotent', async () => {
  const { source, target } = await fixture();
  await recordPreviousVoicesRoot(source, source);
  expect(existsSync(join(source, 'outputs'))).toBe(false);
  await mkdir(join(target, 'outputs', 'longform_cache'), { recursive: true });
  await recordPreviousVoicesRoot(source, target);
  await recordPreviousVoicesRoot(source, target);
  const roots = JSON.parse(
    await readFile(join(target, 'outputs', 'longform_cache', 'voices_roots.json'), 'utf8'),
  );
  expect(roots).toEqual([join(source, 'voices')]);
});
