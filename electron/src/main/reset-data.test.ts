// @vitest-environment node
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, parse } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import {
  isValidVoiceStudioRoot,
  purgeResetScopes,
  scanResetScopes,
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
  const data = join(root, 'OmniVoice', 'data');
  const models = join(root, '.cache', 'huggingface');
  const logs = join(root, 'OmniVoice', 'logs');
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

it('clears only log files when the log root is the data folder', async () => {
  // The backend reports dirname(crash_log_path), and crash_log.txt lives in
  // the data folder itself, so the packaged app passes logs === data.
  const roots = await fixture();
  await writeFile(join(roots.data, 'crash_log.txt'), 'crash');
  await writeFile(join(roots.data, 'omnivoice.log.1'), 'log');
  const shared = { ...roots, logs: roots.data };
  const logs = (await scanResetScopes(shared)).find((scope) => scope.key === 'logs')!;
  expect(logs.paths).not.toContain(roots.data);
  const report = await purgeResetScopes(shared, ['logs'], join(roots.root, 'home'));
  expect(report.removed).not.toContain(roots.data);
  expect(existsSync(join(roots.data, 'crash_log.txt'))).toBe(false);
  expect(existsSync(join(roots.data, 'omnivoice.log.1'))).toBe(false);
  expect(existsSync(join(roots.data, 'prefs.json'))).toBe(true);
  expect(existsSync(join(roots.data, 'voices', 'voice.wav'))).toBe(true);
  expect(existsSync(join(roots.data, 'keep.txt'))).toBe(true);
});

it('keeps a log root that contains the data or model folder', async () => {
  const roots = await fixture();
  const parent = join(roots.root, 'OmniVoice');
  const report = await purgeResetScopes({ ...roots, logs: parent }, ['logs'], null);
  expect(report.removed).toEqual([]);
  expect(existsSync(join(roots.data, 'voices', 'voice.wav'))).toBe(true);
  const cache = join(roots.root, '.cache');
  const models = await purgeResetScopes({ ...roots, logs: cache }, ['logs'], null);
  expect(models.removed).toEqual([]);
  expect(existsSync(join(roots.models, 'models--fixture', 'weights.bin'))).toBe(true);
});

it('refuses filesystem roots, home directories and unsigned custom roots', async () => {
  const root = await mkdtemp(join(tmpdir(), 'unsigned-reset-test-'));
  created.push(root);
  expect(await isValidVoiceStudioRoot(parse(root).root, null)).toBe(false);
  expect(await isValidVoiceStudioRoot(root, root)).toBe(false);
  expect(await isValidVoiceStudioRoot(root, null)).toBe(false);
});
