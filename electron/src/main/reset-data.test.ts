// @vitest-environment node
import { mkdtemp, mkdir, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, parse, sep } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import {
  isValidVoiceStudioRoot,
  directorySize,
  modelsAreShared,
  purgeResetScopes,
  scanResetScopes,
  within,
  type ResetRoots,
} from './reset-data';

const created: string[] = [];
afterEach(async () => {
  const { rm } = await import('node:fs/promises');
  await Promise.allSettled(
    created.splice(0).map((path) => rm(path, { recursive: true, force: true })),
  );
});

async function fixture(): Promise<ResetRoots & { root: string }> {
  const root = await mkdtemp(join(tmpdir(), 'VoiceStudio-reset-test-'));
  created.push(root);
  const data = join(root, 'Application Support', 'OmniVoice', 'data');
  const models = join(root, '.cache', 'huggingface');
  const logs = join(root, 'Application Support', 'OmniVoice', 'logs');
  const temp = join(root, 'temp');
  await Promise.all([data, models, logs, temp].map((path) => mkdir(path, { recursive: true })));
  await mkdir(join(data, 'voices'), { recursive: true });
  await mkdir(join(data, 'gallery_cache'), { recursive: true });
  await mkdir(join(models, 'models--fixture'), { recursive: true });
  await mkdir(join(temp, 'omnivoice-job'), { recursive: true });
  await writeFile(join(data, 'prefs.json'), 'prefs');
  await writeFile(join(data, 'voices', 'voice.wav'), 'voice');
  await writeFile(join(data, 'gallery_cache', 'cache.bin'), 'cache');
  await writeFile(join(data, 'keep.txt'), 'keep');
  await writeFile(join(models, 'models--fixture', 'weights.bin'), 'weights');
  await writeFile(join(logs, 'backend.log'), 'log');
  await writeFile(join(temp, 'omnivoice-job', 'part.bin'), 'temp');
  await writeFile(join(temp, 'unrelated.txt'), 'keep');
  return { root, data, models, logs, temp };
}

it('reports real reset scopes and marks a cache outside app data as shared', async () => {
  const roots = await fixture();
  const scopes = await scanResetScopes(roots);
  expect(scopes.slice(0, 2).map((scope) => scope.key)).toEqual(['ui_prefs', 'history']);
  expect(scopes.find((scope) => scope.key === 'content')).toMatchObject({
    exists: true,
    shared: false,
  });
  expect(scopes.find((scope) => scope.key === 'models')).toMatchObject({
    exists: true,
    shared: true,
  });
  expect(scopes.find((scope) => scope.key === 'models')!.size_bytes).toBeGreaterThan(0);
});

it('removes only selected VoiceStudio targets and preserves unrelated siblings', async () => {
  const roots = await fixture();
  const report = await purgeResetScopes(
    roots,
    ['settings', 'content', 'models', 'caches', 'logs', 'unknown'],
    join(roots.root, 'home'),
  );
  expect(report.failed).toEqual([]);
  expect(report.refused).toEqual([]);
  expect(report.freed_bytes).toBeGreaterThan(0);
  expect(existsSync(join(roots.data, 'prefs.json'))).toBe(false);
  expect(existsSync(join(roots.data, 'voices'))).toBe(false);
  expect(existsSync(roots.models)).toBe(false);
  expect(existsSync(roots.logs!)).toBe(false);
  expect(existsSync(join(roots.temp, 'omnivoice-job'))).toBe(false);
  expect(existsSync(join(roots.data, 'keep.txt'))).toBe(true);
  expect(existsSync(join(roots.temp, 'unrelated.txt'))).toBe(true);
});

it('refuses filesystem roots, home directories and unsigned custom roots', async () => {
  const root = await mkdtemp(join(tmpdir(), 'unsigned-reset-test-'));
  created.push(root);
  expect(await isValidVoiceStudioRoot(parse(root).root, null)).toBe(false);
  expect(await isValidVoiceStudioRoot(root, root)).toBe(false);
  expect(await isValidVoiceStudioRoot(root, null)).toBe(false);
});

it('does not infer ownership from a lookalike ancestor or a model-cache signature', async () => {
  const root = await mkdtemp(join(tmpdir(), 'VoiceStudio-lookalike-'));
  created.push(root);
  const personal = join(root, 'Application Support', 'VoiceStudio', 'personal', 'documents');
  const cache = join(root, 'external-cache');
  await mkdir(join(cache, 'models--someone--model'), { recursive: true });
  await mkdir(personal, { recursive: true });
  expect(await isValidVoiceStudioRoot(personal, null)).toBe(false);
  expect(await isValidVoiceStudioRoot(cache, null)).toBe(false);
  expect(await isValidVoiceStudioRoot(cache, null, true)).toBe(true);
});

it('resolves aliases before deciding whether models are shared', async () => {
  const roots = await fixture();
  const externalAlias = join(roots.root, 'external-models-link');
  await symlink(roots.models, externalAlias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await modelsAreShared(externalAlias, roots.data)).toBe(true);

  const nestedModels = join(roots.data, 'models');
  const nestedAlias = join(roots.root, 'nested-models-link');
  await mkdir(nestedModels);
  await symlink(nestedModels, nestedAlias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await modelsAreShared(nestedAlias, roots.data)).toBe(false);
});

it('authorizes reset roots per scope and rejects a logs alias to a shared cache', async () => {
  const roots = await fixture();
  const cacheLogs = join(roots.root, 'cache-logs');
  await symlink(roots.models, cacheLogs, process.platform === 'win32' ? 'junction' : 'dir');
  const report = await purgeResetScopes({ ...roots, logs: cacheLogs }, ['logs'], null);
  expect(report.refused).toContain(cacheLogs);
  expect(existsSync(join(roots.models, 'models--fixture', 'weights.bin'))).toBe(true);
});

it('measures a linked root but never follows links found inside it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'VoiceStudio-size-'));
  created.push(root);
  const data = join(root, 'Application Support', 'OmniVoice', 'data');
  const external = join(root, 'external');
  const alias = join(root, 'data-link');
  await mkdir(data, { recursive: true });
  await mkdir(external);
  await writeFile(join(data, 'owned.bin'), '1234');
  await writeFile(join(external, 'external.bin'), '12345678');
  await symlink(
    external,
    join(data, 'external-link'),
    process.platform === 'win32' ? 'junction' : 'dir',
  );
  await symlink(data, alias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await directorySize(alias)).toBe(4);
});

it('treats dot-prefixed names as children rather than parent traversal', () => {
  const root = join(parse(process.cwd()).root, 'tmp', 'VoiceStudio');
  expect(within(join(root, '..cache'), root)).toBe(true);
  expect(within(join(root, '..', 'cache'), root)).toBe(false);
});

it('treats a bare app-named folder as owned only under an app-data parent or with a signature', async () => {
  const root = await mkdtemp(join(tmpdir(), 'VoiceStudio-parent-test-'));
  created.push(root);
  const personal = join(root, 'Documents', 'VoiceStudio');
  const installed = join(root, '.config', 'VoiceStudio');
  const strayShare = join(root, 'share', 'OmniVoice');
  const localShare = join(root, '.local', 'share', 'OmniVoice');
  for (const path of [personal, installed, strayShare, localShare])
    await mkdir(path, { recursive: true });
  const home = join(root, 'home');
  expect(await isValidVoiceStudioRoot(personal, home)).toBe(false);
  expect(await isValidVoiceStudioRoot(strayShare, home)).toBe(false);
  expect(await isValidVoiceStudioRoot(installed, home)).toBe(true);
  expect(await isValidVoiceStudioRoot(localShare, home)).toBe(true);
  // Directly under the home folder, as ~/.omnivoice is.
  expect(await isValidVoiceStudioRoot(personal, join(root, 'Documents'))).toBe(true);
  await writeFile(join(personal, 'omnivoice.db'), 'db');
  expect(await isValidVoiceStudioRoot(personal, home)).toBe(true);
});

it('never removes the app data through a models root that is the same folder (#3512)', async () => {
  const roots = await fixture();
  const alias = join(roots.root, 'models-alias');
  await symlink(roots.data, alias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await modelsAreShared(alias, roots.data)).toBe(true);
  expect(await modelsAreShared(`${roots.data}${sep}`, roots.data)).toBe(true);
  const report = await purgeResetScopes({ ...roots, models: alias }, ['models'], null);
  expect(report.refused).toContain(alias);
  expect(existsSync(join(roots.data, 'voices', 'voice.wav'))).toBe(true);
});

it('never removes the app data through a models root that contains it (#3512)', async () => {
  const roots = await fixture();
  const data = join(roots.models, 'OmniVoice', 'data');
  await mkdir(data, { recursive: true });
  await writeFile(join(data, 'prefs.json'), 'prefs');
  const report = await purgeResetScopes({ ...roots, data }, ['models'], null);
  expect(report.refused).toContain(roots.models);
  expect(existsSync(join(data, 'prefs.json'))).toBe(true);
});

it('keeps a model cache inside another scope opt-in and counts it once (#3512)', async () => {
  const roots = await fixture();
  const engines = join(roots.data, 'engines');
  const models = join(engines, 'hf-cache');
  await mkdir(join(models, 'models--fixture'), { recursive: true });
  await writeFile(join(engines, 'engine.bin'), '1234');
  await writeFile(join(models, 'models--fixture', 'weights.bin'), '12345678');
  const nested = { ...roots, models };
  const scopes = await scanResetScopes(nested);
  expect(scopes.find((scope) => scope.key === 'engines')?.size_bytes).toBe(4);
  expect(scopes.find((scope) => scope.key === 'models')?.size_bytes).toBe(8);

  const kept = await purgeResetScopes(nested, ['engines'], null);
  expect(kept.refused).toContain(engines);
  expect(existsSync(join(models, 'models--fixture', 'weights.bin'))).toBe(true);

  const both = await purgeResetScopes(nested, ['engines', 'models'], null);
  expect(both.refused).toEqual([]);
  expect(existsSync(engines)).toBe(false);
});
