// @vitest-environment node
import { existsSync } from 'node:fs';
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, parse } from 'node:path';
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

it('moves into an existing empty destination, the folder-picker case (#2521)', async () => {
  const { source, target } = await fixture();
  await mkdir(target, { recursive: true });
  const prepared = await prepareDataRelocation(source, target);
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(existsSync(join(source, 'omnivoice.db'))).toBe(true);
  expect(existsSync(prepared.target)).toBe(true);
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

it('accepts a single-component destination directly under the volume root', async () => {
  const { root, source } = await fixture();
  // Only one path component below the drive root: previously rejected by
  // safeDestination's '>= 2' requirement even though dirname(target) !== target.
  const target = join(parse(root).root, 'VoiceStudio-relocation-single-test');
  const plan = await inspectDataRelocation(source, target);
  expect(plan.target.endsWith('VoiceStudio-relocation-single-test')).toBe(true);
  expect(plan.file_count).toBe(2);
});

it.each(['..nested', '..cache', '..nested.name'])(
  'rejects a destination inside the source through the legal component %s',
  async (component) => {
    const { source } = await fixture();
    const target = join(source, component, 'destination');
    await mkdir(target, { recursive: true });
    await expect(inspectDataRelocation(source, target)).rejects.toThrow('nested_path');
  },
);

it.each(['..nested', '..cache', '..nested.name'])(
  'rejects a source inside the destination through the legal component %s',
  async (component) => {
    const { root } = await fixture();
    const target = join(root, 'container');
    const source = join(target, component, 'data');
    await mkdir(source, { recursive: true });
    await writeFile(join(source, 'sample.wav'), 'voice');
    await expect(inspectDataRelocation(source, target)).rejects.toThrow('nested_path');
  },
);

it.each(['..neighbor', 'data-other', 'database'])(
  'allows the separate sibling %s without treating it as nested',
  async (component) => {
    const { source } = await fixture();
    const target = join(dirname(source), component);
    const plan = await inspectDataRelocation(source, target);
    expect(plan.target).toBe(join(dirname(await realpath(source)), component));
  },
);

it('never activates or removes a source whose dot-prefixed child contains the destination', async () => {
  const { root, source } = await fixture();
  const target = join(source, '..nested', 'destination');
  await mkdir(target, { recursive: true });
  let verified = false;
  const outcome = await relocateDataDirectory(source, target, {
    environmentPath: join(root, 'environment'),
    stop: async () => undefined,
    start: async () => undefined,
    verify: async () => {
      verified = true;
      return true;
    },
    progress: () => undefined,
  }).then(
    (result) => ({ result, targetExists: existsSync(target), sourceExists: existsSync(source) }),
    (error: Error) => ({ error }),
  );
  expect(outcome).toMatchObject({ error: expect.objectContaining({ message: 'nested_path' }) });
  expect(verified).toBe(false);
  expect(existsSync(source)).toBe(true);
  expect(existsSync(target)).toBe(true);
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
});

it.each([true, false])(
  'keeps a replacement for a newly created target when verification returns %s',
  async (verified) => {
    const { root, source, target } = await fixture();
    const moved = join(root, 'moved-copy');
    await expect(
      relocateDataDirectory(source, target, {
        environmentPath: join(root, 'env'),
        stop: async () => undefined,
        start: async () => undefined,
        verify: async () => {
          await rename(target, moved);
          await mkdir(target);
          await writeFile(join(target, 'personal.txt'), 'unrelated');
          return verified;
        },
        progress: () => undefined,
      }),
    ).rejects.toMatchObject({
      message: 'rollback_incomplete',
      cause: expect.objectContaining({
        message: verified ? 'target_changed' : 'backend_verification_failed',
      }),
    });
    expect(await readFile(join(target, 'personal.txt'), 'utf8')).toBe('unrelated');
    expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  },
);

it('rejects a retargeted parent alias for a newly created destination', async () => {
  const { root, source, target } = await fixture();
  const alias = join(root, 'target-alias');
  await symlink(dirname(target), alias, process.platform === 'win32' ? 'junction' : 'dir');
  const requested = join(alias, basename(target));
  const foreign = join(root, 'foreign-parent');
  await mkdir(join(foreign, basename(target)), { recursive: true });
  await writeFile(join(foreign, basename(target), 'personal.txt'), 'unrelated');
  await expect(
    relocateDataDirectory(source, requested, {
      environmentPath: join(root, 'env'),
      stop: async () => undefined,
      start: async () => undefined,
      verify: async () => {
        await rename(alias, join(root, 'previous-alias'));
        await symlink(foreign, alias, process.platform === 'win32' ? 'junction' : 'dir');
        return true;
      },
      progress: () => undefined,
    }),
  ).rejects.toMatchObject({
    message: 'rollback_incomplete',
    cause: expect.objectContaining({ message: 'target_changed' }),
  });
  expect(await readFile(join(source, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(await readFile(join(target, 'voices', 'sample.wav'), 'utf8')).toBe('voice');
  expect(await readFile(join(foreign, basename(target), 'personal.txt'), 'utf8')).toBe('unrelated');
});
