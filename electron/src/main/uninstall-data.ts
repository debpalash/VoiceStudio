import { lstat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { isAbsolute } from 'node:path';
import type { UninstallTarget } from '../preload/index.d';
import {
  directorySize,
  isAppOwnedRoot,
  isValidVoiceStudioRoot,
  modelsAreShared,
  normalized,
  physicalPath,
  within,
} from './reset-data';

export type { UninstallTarget } from '../preload/index.d';

export interface UninstallRoots {
  data: string;
  environment: string;
  /** The runtime Electron created; a list when data/models share its folder and must stay separate targets. */
  runtimeEnvironment?: string | string[] | null;
  logs: string | null;
  userEnvironment: string;
  models: string;
}

export interface UninstallPlan {
  paths: string[];
  targets: Array<{ path: string; kind: 'app' | 'models' }>;
  refused: string[];
  size_bytes: number;
}

async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch {
    return false;
  }
}

async function uniqueRoots<
  T extends { key: UninstallTarget['key']; path: string; shared: boolean },
>(values: T[], home: string | null) {
  const physicalTargets = await Promise.all(
    values.map(async (target) => ({ ...target, physical: await physicalPath(target.path) })),
  );
  const pending = physicalTargets.filter((target) => target.shared && target.physical);
  while (pending.length) {
    const shared = pending.pop()!;
    for (const target of physicalTargets) {
      if (
        !target.shared &&
        target.physical &&
        shared.physical &&
        (within(target.physical, shared.physical) || within(shared.physical, target.physical))
      ) {
        target.shared = true;
        pending.push(target);
      }
    }
  }
  const grouped = new Map<string, (typeof physicalTargets)[number]>();
  for (const target of physicalTargets) {
    const key = normalized(target.physical ?? target.path);
    const previous = grouped.get(key);
    if (!previous || target.key === 'models') grouped.set(key, target);
    else previous.shared ||= target.shared;
  }
  const targets = [...grouped.values()];
  const owned = await Promise.all(
    targets.map(({ path, key }) => isValidVoiceStudioRoot(path, home, key === 'models')),
  );
  return targets.filter((target, index) => {
    if (target.shared && target.key === 'models') return true;
    return !targets.some(
      (parent, other) =>
        other !== index &&
        owned[other] &&
        parent.shared === target.shared &&
        parent.physical &&
        target.physical &&
        within(target.physical, parent.physical),
    );
  });
}

export async function scanUninstallTargets(
  roots: UninstallRoots,
  home: string | null = homedir(),
): Promise<UninstallTarget[]> {
  const modelLexicallyNested = [roots.data, roots.environment].some((root) =>
    within(roots.models, root),
  );
  const candidates: Array<Pick<UninstallTarget, 'key' | 'path' | 'shared'>> = [
    { key: 'data', path: roots.data, shared: false },
    { key: 'env', path: roots.environment, shared: false },
    ...[roots.runtimeEnvironment ?? []].flat().map((path) => ({
      key: 'env' as const,
      path,
      shared: false,
    })),
    ...(roots.logs ? [{ key: 'logs' as const, path: roots.logs, shared: false }] : []),
    { key: 'userenv', path: roots.userEnvironment, shared: false },
    {
      key: 'models' as const,
      path: roots.models,
      shared: await modelsAreShared(roots.models, roots.data, home),
    },
  ];
  const targets: UninstallTarget[] = [];
  for (const candidate of await uniqueRoots(candidates, home)) {
    const present = await exists(candidate.path);
    const path = candidate.physical ?? candidate.path;
    const coveredBy = candidate.physical
      ? candidates.filter((parent) => parent.path !== candidate.path)
      : [];
    const parents = await Promise.all(
      coveredBy.map(async (parent) => ({ ...parent, physical: await physicalPath(parent.path) })),
    );
    const coveredPaths = parents
      .filter(
        (parent) =>
          parent.physical &&
          normalized(parent.physical) !== normalized(path) &&
          within(path, parent.physical),
      )
      .map((parent) => parent.physical!);
    targets.push({
      key: candidate.key,
      shared: candidate.shared,
      path,
      exists: present && isAbsolute(path),
      size_bytes: present ? await directorySize(path) : 0,
      ...(coveredPaths.length ? { covered_by: coveredPaths } : {}),
    });
  }
  const model = targets.find((target) => target.key === 'models');
  if (model && !model.exists && modelLexicallyNested) {
    targets.splice(targets.indexOf(model), 1);
  }
  return targets;
}

export async function createUninstallPlan(
  targets: UninstallTarget[],
  includeModels: boolean,
  home: string | null,
): Promise<UninstallPlan> {
  const protectedTargets = await uniqueRoots(targets, home);
  const eligible = protectedTargets.filter(
    (target) => target.exists && (includeModels || !target.shared || target.key !== 'models'),
  );
  const refused: string[] = [];
  const accepted: Array<UninstallTarget & { physical: string }> = [];
  for (const target of eligible) {
    const physical = target.physical;
    const valid =
      physical &&
      (await (target.key === 'models'
        ? isValidVoiceStudioRoot(physical, home, true)
        : isAppOwnedRoot(physical, home)));
    if (!isAbsolute(target.path) || !physical || !valid) {
      refused.push(target.path);
    } else if (includeModels || !target.shared) {
      accepted.push({ ...target, physical });
    }
  }
  const selected = accepted.filter(
    (target, index) =>
      !accepted.some(
        (parent, other) =>
          other !== index &&
          within(target.physical, parent.physical) &&
          (parent.physical !== target.physical || other < index),
      ),
  );
  return {
    paths: selected.map((target) => target.physical),
    targets: selected.map((target) => ({
      path: target.physical,
      kind: target.key === 'models' ? 'models' : 'app',
    })),
    refused,
    size_bytes: selected.reduce((sum, target) => sum + target.size_bytes, 0),
  };
}
