// @vitest-environment node
import { mkdtemp, mkdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { createUninstallPlan, scanUninstallTargets, type UninstallRoots } from './uninstall-data';

const created: string[] = [];
afterEach(async () => {
  await Promise.allSettled(
    created.splice(0).map((path) => rm(path, { recursive: true, force: true })),
  );
});

async function fixture(): Promise<UninstallRoots & { root: string }> {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'VoiceStudio-uninstall-test-')));
  created.push(root);
  const roots = {
    root,
    data: join(root, 'Application Support', 'OmniVoice', 'data'),
    environment: join(root, 'Application Support', 'VoiceStudio', 'shell'),
    logs: join(root, 'Application Support', 'OmniVoice', 'logs'),
    userEnvironment: join(root, '.config', 'omnivoice'),
    models: join(root, '.cache', 'huggingface'),
  };
  for (const path of Object.values(roots).filter((value) => value !== root)) {
    await mkdir(path, { recursive: true });
    await writeFile(join(path, 'fixture.bin'), 'data');
  }
  return roots;
}

it('scans every install-owned location and keeps a shared model cache opt-in', async () => {
  const roots = await fixture();
  const targets = await scanUninstallTargets(roots);
  expect(targets.map((target) => target.key)).toEqual(['data', 'env', 'logs', 'userenv', 'models']);
  expect(targets.every((target) => target.exists && target.size_bytes === 4)).toBe(true);
  expect(targets.find((target) => target.key === 'models')?.shared).toBe(true);
  expect((await createUninstallPlan(targets, false, join(roots.root, 'home'))).paths).not.toContain(
    roots.models,
  );
  expect((await createUninstallPlan(targets, true, join(roots.root, 'home'))).paths).toContain(
    roots.models,
  );
});

it('refuses unsigned arbitrary roots and collapses descendants under an owned parent', async () => {
  const roots = await fixture();
  const arbitrary = join(roots.root, 'personal', 'documents');
  await mkdir(arbitrary, { recursive: true });
  const targets = await scanUninstallTargets({
    ...roots,
    data: arbitrary,
    logs: join(roots.environment, 'logs'),
  });
  const plan = await createUninstallPlan(targets, false, join(roots.root, 'home'));
  expect(plan.refused).toContain(arbitrary);
  expect(plan.paths).toContain(roots.environment);
  expect(plan.paths).not.toContain(join(roots.environment, 'logs'));
});

it('does not offer a nested private model directory as a separately shared target', async () => {
  const roots = await fixture();
  const targets = await scanUninstallTargets({
    ...roots,
    models: join(roots.environment, 'models'),
  });
  expect(targets.some((target) => target.key === 'models')).toBe(false);
});

it('lists a setup-chosen location by its runtime folders so data and models stay separate choices (#2971)', async () => {
  const roots = await fixture();
  const chosen = join(roots.root, 'E', 'VoiceStudio');
  const data = join(chosen, 'data');
  const entries = [join(chosen, 'project'), join(chosen, '.uv-cache')];
  for (const path of [data, ...entries]) await mkdir(path, { recursive: true });
  const targets = await scanUninstallTargets({
    ...roots,
    data,
    models: join(chosen, 'models'),
    runtimeEnvironment: entries,
  });
  const paths = targets.map((target) => target.path);
  expect(paths).toEqual(expect.arrayContaining([data, ...entries]));
  expect(paths).not.toContain(chosen);
  const plan = await createUninstallPlan(
    targets.filter((t) => t.path !== data),
    false,
    null,
  );
  expect(plan.paths).toEqual(expect.arrayContaining(entries));
  expect(plan.paths).not.toContain(data);
});

it('protects a shared model cache reached through a data-path alias', async () => {
  const roots = await fixture();
  const alias = join(roots.root, 'Application Support', 'VoiceStudio', 'data-alias');
  await symlink(roots.models, alias, process.platform === 'win32' ? 'junction' : 'dir');
  const targets = await scanUninstallTargets({ ...roots, data: alias });
  const model = targets.find((target) => target.key === 'models');
  expect(model).toMatchObject({ shared: true, exists: true });
  expect(targets.filter((target) => target.path === model?.path)).toHaveLength(1);
  expect((await createUninstallPlan(targets, false, null)).paths).not.toContain(model?.path);
  expect((await createUninstallPlan(targets, true, null)).paths).toContain(model?.path);
});

it('counts overlapping parent and child targets only once in the deletion plan', async () => {
  const roots = await fixture();
  const parent = join(roots.root, 'Application Support', 'VoiceStudio');
  const child = join(parent, 'data');
  await mkdir(child, { recursive: true });
  await writeFile(join(child, 'prefs.json'), '12345');
  const target = (path: string, size_bytes: number) => ({
    key: 'data' as const,
    path,
    size_bytes,
    exists: true,
    shared: false,
  });
  for (const ordered of [
    [target(parent, 5), target(child, 5)],
    [target(child, 5), target(parent, 5)],
  ]) {
    const plan = await createUninstallPlan(ordered, false, null);
    expect(plan.paths).toEqual([parent]);
    expect(plan.size_bytes).toBe(5);
  }
});
