import { lstat, realpath } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import {
  directorySize,
  isValidVoiceStudioRoot,
  modelsAreShared,
  normalized,
  strictlyWithin,
  within,
} from './reset-data';

export interface UninstallRoots {
  data: string;
  environment: string;
  /** The runtime Electron created; a list when data/models share its folder and must stay separate targets. */
  runtimeEnvironment?: string | string[] | null;
  logs: string | null;
  userEnvironment: string;
  models: string;
}

export interface UninstallTarget {
  key: 'data' | 'env' | 'logs' | 'userenv' | 'models';
  path: string;
  size_bytes: number;
  exists: boolean;
  shared: boolean;
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

function uniqueRoots<T extends { key: UninstallTarget['key']; path: string; shared: boolean }>(
  values: T[],
): T[] {
  const seen = new Set<string>();
  return values.filter(({ path }, index) => {
    const key = normalized(path);
    if (seen.has(key)) return false;
    if (
      !values[index].shared &&
      values.some((parent, other) => other < index && !parent.shared && within(path, parent.path))
    )
      return false;
    seen.add(key);
    return true;
  });
}

export async function scanUninstallTargets(roots: UninstallRoots): Promise<UninstallTarget[]> {
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
      shared: await modelsAreShared(roots.models, roots.data),
    },
  ];
  const targets: UninstallTarget[] = [];
  for (const candidate of candidates) {
    const present = await exists(candidate.path);
    let path = candidate.path;
    if (present) {
      try {
        path = await realpath(candidate.path);
      } catch {
        /* reported as missing below */
      }
    }
    targets.push({
      ...candidate,
      path,
      exists: present && isAbsolute(path),
      size_bytes: present ? await directorySize(path) : 0,
    });
  }
  const model = targets.find((target) => target.key === 'models');
  // Owned only when strictly inside a runtime folder the app created; whichever runtime
  // entry holds it, and never because the cache and the runtime are one folder (#3512).
  if (
    model?.exists &&
    targets.some(
      (target) => target.key === 'env' && target.exists && strictlyWithin(model.path, target.path),
    )
  )
    model.shared = false;
  if (model?.exists && model.shared) {
    for (const target of targets) {
      if (
        target !== model &&
        target.exists &&
        (within(model.path, target.path) || within(target.path, model.path))
      ) {
        target.shared = true;
      }
    }
    for (let index = targets.length - 1; index >= 0; index -= 1) {
      if (targets[index] !== model && normalized(targets[index].path) === normalized(model.path)) {
        targets.splice(index, 1);
      }
    }
  }
  if (
    model &&
    !model.shared &&
    targets.some(
      (target) =>
        target !== model && ['data', 'env'].includes(target.key) && within(model.path, target.path),
    )
  ) {
    targets.splice(targets.indexOf(model), 1);
  } else if (model && !model.exists && modelLexicallyNested) {
    targets.splice(targets.indexOf(model), 1);
  }
  return uniqueRoots(targets);
}

export async function createUninstallPlan(
  targets: UninstallTarget[],
  includeModels: boolean,
  home: string | null,
): Promise<UninstallPlan> {
  const eligible = targets.filter((target) => target.exists && (includeModels || !target.shared));
  const refused: string[] = [];
  const accepted: UninstallTarget[] = [];
  for (const target of eligible) {
    if (
      !isAbsolute(target.path) ||
      !(await isValidVoiceStudioRoot(target.path, home, target.key === 'models'))
    ) {
      refused.push(target.path);
    } else {
      accepted.push(target);
    }
  }
  const paths = accepted
    .filter(
      (target, index) =>
        !accepted.some((parent, other) => other !== index && within(target.path, parent.path)),
    )
    .map((target) => target.path);
  return {
    paths,
    targets: accepted
      .filter(
        (target, index) =>
          !accepted.some((parent, other) => other !== index && within(target.path, parent.path)),
      )
      .map((target) => ({ path: target.path, kind: target.key === 'models' ? 'models' : 'app' })),
    refused,
    size_bytes: accepted
      .filter(
        (target, index) =>
          !accepted.some((parent, other) => other !== index && within(target.path, parent.path)),
      )
      .reduce((sum, target) => sum + target.size_bytes, 0),
  };
}
