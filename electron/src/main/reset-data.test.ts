// @vitest-environment node
import { mkdtemp, mkdir, readFile, realpath, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, parse } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import {
  isValidVoiceStudioRoot,
  directorySize,
  modelsAreShared,
  isAppOwnedRoot,
  physicalPath,
  within,
  purgeResetScopes,
  scanResetScopes,
  type ResetRoots,
} from './reset-data';
import { createUninstallPlan, type UninstallTarget } from './uninstall-data';

const created: string[] = [];

it('distinguishes literal dot-prefixed children from parent traversal', () => {
  const root = join(tmpdir(), 'VoiceStudio-ownership');
  expect(within(root, root)).toBe(true);
  expect(within(join(root, '..cache', 'huggingface'), root)).toBe(true);
  expect(within(join(root, '...cache'), root)).toBe(true);
  expect(within(join(root, '..'), root)).toBe(false);
  expect(within(join(root, '..', 'VoiceStudio-ownership-copy'), root)).toBe(false);
});

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
  await Promise.all(
    [data, models, logs, temp, join(root, 'home')].map((path) => mkdir(path, { recursive: true })),
  );
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

it('does not inherit ownership from an app-named ancestor or its markers', async () => {
  const roots = await fixture();
  const app = join(roots.root, 'VoiceStudio');
  await mkdir(app, { recursive: true });
  await writeFile(join(app, 'omnivoice.db'), 'db');
  for (const suffix of ['Documents', 'personal/Documents', 'personal/project', 'models/other']) {
    await mkdir(join(app, suffix), { recursive: true });
    expect(await physicalPath(join(app, suffix))).not.toBeNull();
    expect(await isValidVoiceStudioRoot(join(app, suffix), null), suffix).toBe(false);
  }
  await mkdir(join(roots.root, 'VoiceStudio-copy', 'project'), { recursive: true });
  expect(await isValidVoiceStudioRoot(join(roots.root, 'VoiceStudio-copy', 'project'), null)).toBe(
    false,
  );
});

it('accepts managed layouts and signatures local to a custom root', async () => {
  const roots = await fixture();
  const layouts = [
    'VoiceStudio',
    '.omnivoice',
    'com.debpalash.omnivoice-studio',
    'OmniVoiceStudio-Data',
    'OmniVoice/data',
    'OmniVoice/logs',
    'OmniVoice/hf_cache',
    'OmniVoice/hf_cache/hub',
    'VoiceStudio/data',
    'VoiceStudio/models',
    'VoiceStudio/models/hub',
    'VoiceStudio/runtime',
    'VoiceStudio/runtime/project',
    'VoiceStudio/project',
    'VoiceStudio/.uv-cache',
    'VoiceStudio/.python',
    'VoiceStudio/.tmp',
    'VoiceStudio/tmp',
    '.cache/huggingface',
    '.cache/huggingface/hub',
  ];
  for (const suffix of layouts) {
    await mkdir(join(roots.root, suffix), { recursive: true });
    if (['VoiceStudio', '.omnivoice', 'com.debpalash.omnivoice-studio', 'OmniVoiceStudio-Data'].includes(suffix))
      await writeFile(join(roots.root, suffix, 'omnivoice.db'), 'synthetic app identity');
    expect(await isValidVoiceStudioRoot(join(roots.root, suffix), null, true), suffix).toBe(true);
  }
  const custom = join(roots.root, 'custom-data');
  await mkdir(custom);
  await writeFile(join(custom, 'omnivoice.db'), 'db');
  expect(await isValidVoiceStudioRoot(custom, null)).toBe(true);
  await mkdir(join(custom, 'Documents'));
  expect(await isValidVoiceStudioRoot(join(custom, 'Documents'), null)).toBe(false);
  expect(await isValidVoiceStudioRoot(custom, custom)).toBe(false);
});

it('keeps external caches shared and recognizes only exact private cache layouts', async () => {
  const roots = await fixture();
  for (const suffix of [
    'VoiceStudio/personal/huggingface',
    'VoiceStudio/huggingface',
    'VoiceStudio/personal/models',
    'VoiceStudio/models/other',
    'custom-cache',
  ]) {
    await mkdir(join(roots.root, suffix), { recursive: true });
    expect(await physicalPath(join(roots.root, suffix))).not.toBeNull();
    expect(await modelsAreShared(join(roots.root, suffix), roots.data), suffix).toBe(true);
  }
  for (const suffix of [
    'OmniVoice/hf_cache',
    'OmniVoice/hf_cache/hub',
    'VoiceStudio/models',
    'VoiceStudio/models/hub',
  ]) {
    await mkdir(join(roots.root, suffix), { recursive: true });
    expect(await modelsAreShared(join(roots.root, suffix), roots.data), suffix).toBe(false);
  }
  await mkdir(join(roots.data, 'models'));
  expect(await modelsAreShared(join(roots.data, 'models'), roots.data)).toBe(false);
});

it('refuses reset of an unrelated descendant and preserves its sentinel', async () => {
  const roots = await fixture();
  const data = join(roots.root, 'VoiceStudio', 'personal', 'Documents');
  const target = join(data, 'gallery_cache');
  const sentinel = join(target, 'unrelated.txt');
  await mkdir(target, { recursive: true });
  await writeFile(sentinel, 'keep');
  const report = await purgeResetScopes({ ...roots, data }, ['caches'], null);
  expect(existsSync(sentinel)).toBe(true);
  expect(report.refused).toContain(target);
});

it('validates a custom root before a selected reset removes its local signature', async () => {
  const roots = await fixture();
  const data = join(roots.root, 'custom-data');
  const cache = join(data, 'gallery_cache');
  await mkdir(cache, { recursive: true });
  await writeFile(join(data, 'prefs.json'), 'prefs');
  await writeFile(join(data, 'unrelated.txt'), 'keep');
  const report = await purgeResetScopes({ ...roots, data }, ['settings', 'caches'], null);
  expect(report.refused).toEqual([]);
  expect(report.failed).toEqual([]);
  expect(existsSync(join(data, 'prefs.json'))).toBe(false);
  expect(existsSync(cache)).toBe(false);
  expect(existsSync(join(data, 'unrelated.txt'))).toBe(true);
});

it('requires staged app identity before recognizing custom runtime folders', async () => {
  const roots = await fixture();
  const runtime = join(roots.root, 'Votest', 'VoiceStudio-AMD-20261025', 'runtime');
  const project = join(runtime, 'project');
  await mkdir(join(project, 'backend'), { recursive: true });
  await writeFile(join(project, '.runtime-ready'), 'a'.repeat(64));
  await writeFile(join(project, 'uv.lock'), 'version = 1');
  await writeFile(join(project, 'backend', 'main.py'), 'app = None');
  await writeFile(join(project, 'pyproject.toml'), '[project]\nname = "unrelated"\n');
  await mkdir(join(runtime, '.uv-cache'));
  expect(await isValidVoiceStudioRoot(runtime, null)).toBe(false);
  expect(await isValidVoiceStudioRoot(join(runtime, '.uv-cache'), null)).toBe(false);
  await writeFile(join(project, 'pyproject.toml'), '[project]\nname = "omnivoice"\n');
  for (const path of [
    runtime,
    project,
    ...['.uv-cache', '.python', '.tmp', 'tmp', 'models'].map((name) => join(runtime, name)),
  ]) {
    await mkdir(path, { recursive: true });
    expect(await isValidVoiceStudioRoot(path, null, path.endsWith('models')), path).toBe(true);
  }
  await mkdir(join(runtime, 'Documents'));
  await mkdir(join(runtime, 'huggingface'));
  expect(await isValidVoiceStudioRoot(join(runtime, 'Documents'), null)).toBe(false);
  expect(await modelsAreShared(join(runtime, 'models'), roots.data)).toBe(false);
  expect(await modelsAreShared(join(runtime, 'huggingface'), roots.data)).toBe(true);
  await writeFile(join(project, '.runtime-ready'), 'invalid');
  expect(await isValidVoiceStudioRoot(runtime, null)).toBe(false);
});

it('accepts local cache signatures without granting private ownership or inheritance', async () => {
  const roots = await fixture();
  const bare = join(roots.root, 'custom-profile');
  await mkdir(join(bare, 'runtime'), { recursive: true });
  expect(await isValidVoiceStudioRoot(bare, null)).toBe(false);
  for (const marker of ['hub', 'models--fixture']) {
    const cache = join(roots.root, marker, 'custom-cache');
    await mkdir(join(cache, marker), { recursive: true });
    expect(await isValidVoiceStudioRoot(cache, null, true)).toBe(true);
    await mkdir(join(cache, 'Documents'));
    expect(await isValidVoiceStudioRoot(join(cache, 'Documents'), null)).toBe(false);
    expect(await modelsAreShared(cache, roots.data)).toBe(true);
  }
});

const cacheOnlyRoles = ['hub', 'models--fixture'].flatMap((marker) =>
  (['data', 'logs'] as const).map((role) => ({ marker, role })),
);

it.each(cacheOnlyRoles.flatMap((entry) => [false, true].map((alias) => ({ ...entry, alias }))))(
  'refuses cache-only $marker in the $role reset role with models alias=$alias',
  async ({ marker, role, alias }) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const cache = join(roots.root, 'cache-only');
    const target = role === 'data' ? join(cache, 'gallery_cache') : cache;
    await mkdir(join(cache, marker), { recursive: true });
    await mkdir(target, { recursive: true });
    const sentinel = join(target, 'unrelated.txt');
    await writeFile(sentinel, 'keep');
    const configured = { ...roots, [role]: cache, ...(alias ? { models: cache } : {}) };
    expect(await physicalPath(cache)).not.toBeNull();
    expect(await isValidVoiceStudioRoot(cache, home, true)).toBe(true);
    expect(await isAppOwnedRoot(cache, home)).toBe(false);
    expect(await modelsAreShared(cache, configured.data, home)).toBe(true);
    const report = await purgeResetScopes(configured, [role === 'data' ? 'caches' : 'logs'], home);
    expect(report.removed).not.toContain(target);
    expect(report.refused).toContain(target);
    expect(await readFile(sentinel, 'utf8')).toBe('keep');
  },
);

it.each(cacheOnlyRoles)(
  'accepts the $role reset after $marker is supplemented with local app identity',
  async ({ marker, role }) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const cache = join(roots.root, 'custom-owned');
    const target = role === 'data' ? join(cache, 'gallery_cache') : cache;
    await mkdir(join(cache, marker), { recursive: true });
    await mkdir(target, { recursive: true });
    await writeFile(join(target, 'selected.txt'), 'selected');
    expect(await isAppOwnedRoot(cache, home)).toBe(false);
    await writeFile(join(cache, 'omnivoice.db'), 'db');
    expect(await isAppOwnedRoot(cache, home)).toBe(true);
    const report = await purgeResetScopes(
      { ...roots, [role]: cache },
      [role === 'data' ? 'caches' : 'logs'],
      home,
    );
    expect(report.refused).toEqual([]);
    expect(report.failed).toEqual([]);
    expect(report.removed).toContain(target);
    expect(existsSync(target)).toBe(false);
    expect(await readFile(join(roots.data, 'keep.txt'), 'utf8')).toBe('keep');
  },
);

it.each(['hub', 'models--fixture'])(
  'keeps explicit model reset available for a shared cache recognized by %s',
  async (marker) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const models = join(roots.root, 'cache-only');
    await mkdir(join(models, marker), { recursive: true });
    await writeFile(join(models, marker, 'weights.bin'), 'synthetic');
    const configured = { ...roots, models };
    const scopes = await scanResetScopes(configured, home);
    expect(scopes.find((scope) => scope.key === 'models')?.shared).toBe(true);
    const report = await purgeResetScopes(configured, ['models'], home);
    expect(report.refused).toEqual([]);
    expect(report.failed).toEqual([]);
    expect(report.removed).toEqual([models]);
    expect(existsSync(models)).toBe(false);
    expect(await readFile(join(roots.data, 'keep.txt'), 'utf8')).toBe('keep');
  },
);

it.each(cacheOnlyRoles)(
  'keeps direct uninstall planning private across $marker/$role input orders',
  async ({ marker, role }) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const cache = join(roots.root, 'cache-only');
    await mkdir(join(cache, marker), { recursive: true });
    const sentinel = join(cache, marker, 'unrelated.txt');
    await writeFile(sentinel, 'keep');
    const other: UninstallTarget = {
      key: role,
      path: cache,
      shared: false,
      exists: true,
      size_bytes: 4,
    };
    expect(await createUninstallPlan([other], false, home)).toMatchObject({
      paths: [],
      refused: [cache],
    });
    const model: UninstallTarget = { ...other, key: 'models', shared: true };
    for (const targets of [
      [other, model],
      [model, other],
    ]) {
      expect(await createUninstallPlan(targets, false, home)).toMatchObject({
        paths: [],
        size_bytes: 0,
      });
      expect(await createUninstallPlan(targets, true, home)).toMatchObject({
        paths: [cache],
        size_bytes: 4,
      });
    }
    expect(await readFile(sentinel, 'utf8')).toBe('keep');
  },
);

const sharedLogLayouts = [
  'same',
  'models-alias',
  'logs-alias',
  'logs-parent',
  'models-parent',
] as const;

async function sharedLogsFixture(layout: (typeof sharedLogLayouts)[number]) {
  const roots = await fixture();
  const cache = join(roots.root, 'shared-cache');
  const modelRoot = layout === 'logs-parent' ? join(cache, 'nested', 'huggingface') : cache;
  const logRoot = layout === 'models-parent' ? join(cache, 'app-logs') : cache;
  await mkdir(join(modelRoot, 'hub'), { recursive: true });
  await mkdir(logRoot, { recursive: true });
  await writeFile(join(logRoot, 'omnivoice.db'), 'synthetic logs identity');
  const sentinel = join(layout === 'logs-parent' ? modelRoot : logRoot, 'synthetic-model.txt');
  await writeFile(sentinel, 'shared model sentinel');
  let models = modelRoot;
  let logs = logRoot;
  if (layout === 'models-alias') {
    models = join(roots.root, 'models-alias');
    await symlink(modelRoot, models, process.platform === 'win32' ? 'junction' : 'dir');
  }
  if (layout === 'logs-alias') {
    logs = join(roots.root, 'logs-alias');
    await symlink(logRoot, logs, process.platform === 'win32' ? 'junction' : 'dir');
  }
  return { roots: { ...roots, models, logs }, sentinel };
}

it.each(
  sharedLogLayouts.flatMap((layout) =>
    (['logs-only', 'logs-models', 'models-logs'] as const).map((selection) => ({
      layout,
      selection,
    })),
  ),
)('protects app-marked shared logs for $layout with $selection', async ({ layout, selection }) => {
  const { roots, sentinel } = await sharedLogsFixture(layout);
  const home = join(roots.root, 'home');
  expect(await isAppOwnedRoot(roots.logs, home)).toBe(true);
  expect(await modelsAreShared(roots.models, roots.data, home)).toBe(true);
  const scopes = await scanResetScopes(roots, home);
  const requested = selection === 'logs-only' ? ['logs'] : selection.split('-');
  const report = await purgeResetScopes(roots, requested, home);
  expect(report.failed).toEqual([]);
  if (selection === 'logs-only') {
    expect(existsSync(sentinel)).toBe(true);
    expect(report.refused).toContain(roots.logs);
    expect(report.removed).not.toContain(roots.logs);
    expect(scopes.find((scope) => scope.key === 'logs')?.shared).toBe(true);
    expect(scopes.find((scope) => scope.key === 'models')?.shared).toBe(true);
  } else {
    expect(report.refused).toEqual([]);
    expect(existsSync(sentinel)).toBe(false);
  }
});

it('keeps independent logs removable without selecting a disjoint shared model cache', async () => {
  const roots = await fixture();
  const home = join(roots.root, 'home');
  expect(await modelsAreShared(roots.models, roots.data, home)).toBe(true);
  const scopes = await scanResetScopes(roots, home);
  expect(scopes.find((scope) => scope.key === 'logs')?.shared).toBe(false);
  const report = await purgeResetScopes(roots, ['logs'], home);
  expect(report.refused).toEqual([]);
  expect(report.failed).toEqual([]);
  expect(existsSync(roots.logs!)).toBe(false);
  expect(await readFile(join(roots.models, 'models--fixture', 'weights.bin'), 'utf8')).toBe(
    'weights',
  );
});

it.each(['logs-parent', 'logs-child'])(
  'preserves validated app-data model privacy for private %s reset',
  async (layout) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const models = join(roots.data, 'models');
    const logs = layout === 'logs-parent' ? roots.data : join(models, 'app-logs');
    await mkdir(models, { recursive: true });
    await mkdir(logs, { recursive: true });
    await writeFile(join(logs, 'omnivoice.db'), 'synthetic app identity');
    const sentinel = join(layout === 'logs-parent' ? models : logs, 'synthetic-model.txt');
    await writeFile(sentinel, 'private model sentinel');
    const configured = { ...roots, models, logs };
    expect(await modelsAreShared(models, roots.data, home)).toBe(false);
    const scopes = await scanResetScopes(configured, home);
    expect(scopes.find((scope) => scope.key === 'logs')?.shared).toBe(false);
    const report = await purgeResetScopes(configured, ['logs'], home);
    expect(report.refused).toEqual([]);
    expect(report.failed).toEqual([]);
    expect(existsSync(sentinel)).toBe(false);
  },
);

it.each(['home', 'unsigned', 'filesystem-root'])(
  'does not make a cache private through rejected %s data',
  async (kind) => {
    const roots = await fixture();
    const home = join(roots.root, 'home');
    const data =
      kind === 'home' ? home : kind === 'unsigned' ? join(home, 'personal') : parse(home).root;
    const models = join(kind === 'filesystem-root' ? home : data, '.cache', 'huggingface');
    await mkdir(models, { recursive: true });
    if (kind === 'home') await writeFile(join(home, 'omnivoice.db'), 'db');
    expect(await isValidVoiceStudioRoot(data, home)).toBe(false);
    expect(await modelsAreShared(models, data, home)).toBe(true);
    if (kind !== 'filesystem-root') {
      const scopes = await scanResetScopes({ ...roots, data, models }, home);
      expect(scopes.find((scope) => scope.key === 'models')?.shared).toBe(true);
    }
  },
);

it('keeps containment in a custom data root private when its local signature validates', async () => {
  const roots = await fixture();
  const home = join(roots.root, 'home');
  const data = join(home, 'custom-data');
  const models = join(data, '.cache', 'huggingface');
  await mkdir(models, { recursive: true });
  await writeFile(join(data, 'omnivoice.db'), 'db');
  expect(await isValidVoiceStudioRoot(data, home)).toBe(true);
  expect(await modelsAreShared(models, data, home)).toBe(false);
});

it('classifies a private-looking models link by its external shared cache target', async () => {
  const roots = await fixture();
  const external = join(roots.root, 'external', 'huggingface');
  const models = join(roots.root, 'VoiceStudio', 'models');
  await mkdir(external, { recursive: true });
  await mkdir(join(external, 'hub'));
  await mkdir(join(roots.root, 'VoiceStudio'), { recursive: true });
  await writeFile(join(external, 'unrelated.txt'), 'keep');
  await symlink(external, models, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await realpath(models)).toBe(await realpath(external));
  expect(await isValidVoiceStudioRoot(models, null, true)).toBe(true);
  expect(await isAppOwnedRoot(models, null)).toBe(false);
  expect(await modelsAreShared(models, roots.data)).toBe(true);
  const scopes = await scanResetScopes({ ...roots, models });
  expect(scopes.find((scope) => scope.key === 'models')?.shared).toBe(true);
  expect(existsSync(join(external, 'unrelated.txt'))).toBe(true);
});

it('refuses app-named links to home or unsigned roots and preserves canonical private caches', async () => {
  const roots = await fixture();
  const home = join(roots.root, 'home');
  const unsigned = join(roots.root, 'personal', 'Documents');
  await mkdir(home, { recursive: true });
  await mkdir(unsigned, { recursive: true });
  for (const [index, target] of [home, unsigned].entries()) {
    const alias = join(roots.root, `alias-${index}`, 'VoiceStudio');
    await mkdir(join(roots.root, `alias-${index}`));
    await symlink(target, alias, process.platform === 'win32' ? 'junction' : 'dir');
    expect(await isValidVoiceStudioRoot(alias, home)).toBe(false);
  }
  const cache = join(roots.root, 'VoiceStudio', 'models');
  const alias = join(roots.root, 'private-cache-alias');
  await mkdir(cache, { recursive: true });
  await symlink(cache, alias, process.platform === 'win32' ? 'junction' : 'dir');
  expect(await isValidVoiceStudioRoot(alias, home, true)).toBe(true);
  expect(await modelsAreShared(alias, roots.data, home)).toBe(false);
});
