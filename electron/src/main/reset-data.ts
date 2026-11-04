import { lstat, readdir, realpath, rm } from 'node:fs/promises';
import { lstatSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path';

export const FRONTEND_RESET_SCOPES = ['ui_prefs', 'history'] as const;
export const DISK_RESET_SCOPES = [
  'settings',
  'content',
  'engines',
  'tools',
  'models',
  'caches',
  'logs',
] as const;
export type DiskResetScope = (typeof DISK_RESET_SCOPES)[number];

export interface ResetRoots {
  data: string;
  models: string;
  logs: string | null;
  temp: string;
}

export interface ResetScope {
  key: string;
  paths: string[];
  size_bytes: number;
  exists: boolean;
  shared: boolean;
  needs_restart: boolean;
}

export interface ResetReport {
  removed: string[];
  failed: string[];
  refused: string[];
  freed_bytes: number;
  restarted: boolean;
}

async function stat(path: string) {
  try {
    return await lstat(path);
  } catch {
    return null;
  }
}

export async function directorySize(path: string): Promise<number> {
  let physical: string;
  try {
    physical = await realpath(path);
  } catch {
    return 0;
  }
  return directorySizePhysical(physical);
}

async function directorySizePhysical(path: string): Promise<number> {
  const info = await stat(path);
  if (!info || info.isSymbolicLink()) return 0;
  if (info.isFile()) return info.size;
  if (!info.isDirectory()) return 0;
  let children;
  try {
    children = await readdir(path, { withFileTypes: true });
  } catch {
    return 0;
  }
  const sizes = await Promise.all(
    children.map((entry) =>
      entry.isSymbolicLink() ? 0 : directorySizePhysical(join(path, entry.name)),
    ),
  );
  return sizes.reduce((sum, size) => sum + size, 0);
}

async function prefixedChildren(path: string, prefix: string): Promise<string[]> {
  try {
    return (await readdir(path, { withFileTypes: true }))
      .filter((entry) => entry.name.startsWith(prefix))
      .map((entry) => join(path, entry.name))
      .sort();
  } catch {
    return [];
  }
}

function components(path: string): string[] {
  const root = parse(path).root;
  return path
    .slice(root.length)
    .split(/[\\/]+/)
    .filter(Boolean);
}

export function normalized(path: string): string {
  const value = resolve(path);
  return process.platform === 'win32' ? value.toLocaleLowerCase('en-US') : value;
}

export function within(path: string, root: string): boolean {
  const rel = relative(normalized(root), normalized(path));
  return rel === '' || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

async function hasAppSignature(path: string, allowModelCache = false): Promise<boolean> {
  if (
    hasRuntimeProjectSignature(path) ||
    hasRuntimeProjectSignature(join(path, 'project')) ||
    hasRuntimeProjectSignature(join(path, 'runtime', 'project'))
  )
    return true;
  for (const marker of ['omnivoice.db', 'prefs.json', 'voices', 'outputs', 'engines']) {
    if (await stat(join(path, marker))) return true;
  }
  if (!allowModelCache) return false;
  const hub = await stat(join(path, 'hub'));
  if (hub?.isDirectory()) return true;
  try {
    return (await readdir(path)).some((name) => name.startsWith('models--'));
  } catch {
    return false;
  }
}

async function canonical(path: string): Promise<string | null> {
  try {
    return await realpath(path);
  } catch {
    return null;
  }
}

export async function physicalPath(path: string): Promise<string | null> {
  return isAbsolute(path) ? canonical(path) : null;
}

function hasRuntimeProjectSignature(project: string): boolean {
  try {
    if (
      !lstatSync(project).isDirectory() ||
      !lstatSync(join(project, 'backend')).isDirectory() ||
      !lstatSync(join(project, 'backend', 'main.py')).isFile() ||
      !lstatSync(join(project, 'uv.lock')).isFile()
    )
      return false;
    const ready = join(project, '.runtime-ready');
    const metadata = join(project, 'pyproject.toml');
    const readyInfo = lstatSync(ready);
    const metadataInfo = lstatSync(metadata);
    if (
      !readyInfo.isFile() ||
      readyInfo.size > 128 ||
      !metadataInfo.isFile() ||
      metadataInfo.size > 65536
    )
      return false;
    if (!/^[a-f0-9]{64}$/.test(readFileSync(ready, 'utf8').trim())) return false;
    let inProject = false;
    for (const raw of readFileSync(metadata, 'utf8').split(/\r?\n/)) {
      const line = raw.split('#', 1)[0].trim();
      if (line.startsWith('[')) inProject = line === '[project]';
      else if (inProject && /^name\s*=\s*(["'])omnivoice\1$/.test(line)) return true;
    }
  } catch {
    return false;
  }
  return false;
}

const APP_NAMES = new Set([
  'omnivoice',
  '.omnivoice',
  'voicestudio',
  'com.debpalash.omnivoice-studio',
]);
const MANAGED_CHILDREN = new Set([
  'data',
  'logs',
  'runtime',
  'shell',
  'project',
  '.uv-cache',
  '.python',
  '.tmp',
  'tmp',
]);

// Folders the OS (or the installer) keeps per-app directories in. A bare app-named
// directory anywhere else, such as ~/Documents/VoiceStudio, is the user's own unless it
// carries the app's signature.
const APP_PARENTS = new Set([
  'application support',
  'caches',
  'logs',
  'roaming',
  'local',
  'programs',
  '.config',
  '.cache',
  'share',
  'state',
]);

function appParent(appDir: string, home: string | null): boolean {
  const parent = dirname(appDir);
  if (home && normalized(parent) === normalized(home)) return true;
  const parts = components(parent).map((part) => part.toLocaleLowerCase('en-US'));
  const name = parts.at(-1);
  if (!name || !APP_PARENTS.has(name)) return false;
  // `share` and `state` only count as ~/.local/share and ~/.local/state.
  return (name !== 'share' && name !== 'state') || parts.at(-2) === '.local';
}

function exactManagedLayout(path: string, home: string | null): boolean {
  const parts = components(path).map((part) => part.toLocaleLowerCase('en-US'));
  const leaf = parts.at(-1);
  const parent = parts.at(-2);
  if (!leaf) return false;
  if (MANAGED_CHILDREN.has(leaf) && hasRuntimeProjectSignature(join(dirname(path), 'project')))
    return true;
  if (APP_NAMES.has(leaf)) return appParent(path, home);
  // A known runtime child is specific enough on its own: setup can place the whole
  // app folder on any drive (#2971).
  if (parent && APP_NAMES.has(parent) && MANAGED_CHILDREN.has(leaf)) return true;
  return (
    parts.length >= 3 &&
    parts.at(-3) !== undefined &&
    APP_NAMES.has(parts.at(-3)!) &&
    parts.at(-2) === 'runtime' &&
    MANAGED_CHILDREN.has(leaf)
  );
}

function exactModelLayout(path: string): boolean {
  const parts = components(path).map((part) => part.toLocaleLowerCase('en-US'));
  return (
    (parts.at(-1) === 'huggingface' && parts.at(-2) === '.cache') ||
    (parts.at(-1) === 'hub' && parts.at(-2) === 'huggingface')
  );
}

function privateModelLayout(path: string): boolean {
  const root = basename(path).toLocaleLowerCase('en-US') === 'hub' ? dirname(path) : path;
  return (
    ['models', 'hf_cache'].includes(basename(root).toLocaleLowerCase('en-US')) &&
    (APP_NAMES.has(basename(dirname(root)).toLocaleLowerCase('en-US')) ||
      exactManagedLayout(dirname(root), null) ||
      hasRuntimeProjectSignature(join(dirname(root), 'project')))
  );
}

export async function isValidVoiceStudioRoot(
  path: string,
  home: string | null = homedir(),
  allowModelCache = false,
): Promise<boolean> {
  if (!isAbsolute(path)) return false;
  const physical = await canonical(path);
  if (!physical || dirname(physical) === physical || components(physical).length < 2) return false;
  if (!(await stat(physical))?.isDirectory()) return false;
  const physicalHome = await canonical(home ?? homedir());
  if (physicalHome && normalized(physical) === normalized(physicalHome)) return false;
  return (
    exactManagedLayout(physical, physicalHome) ||
    (allowModelCache && (exactModelLayout(physical) || privateModelLayout(physical))) ||
    hasAppSignature(physical, allowModelCache)
  );
}

export async function isAppOwnedRoot(path: string, home: string | null): Promise<boolean> {
  return isValidVoiceStudioRoot(path, home, false);
}

export async function modelsAreShared(
  models: string,
  data: string,
  home: string | null = homedir(),
): Promise<boolean> {
  const [physicalModels, physicalData] = await Promise.all([canonical(models), canonical(data)]);
  if (!physicalModels || !(await stat(physicalModels))?.isDirectory()) return true;
  const physicalHome = await canonical(home ?? homedir());
  if (physicalHome && normalized(physicalModels) === normalized(physicalHome)) return true;
  if (physicalData && within(physicalModels, physicalData) && (await isAppOwnedRoot(physicalData, home)))
    return false;
  return !(await isValidVoiceStudioRoot(physicalModels, home, true)) || !privateModelLayout(physicalModels);
}

async function scopeTargets(key: DiskResetScope, roots: ResetRoots): Promise<string[]> {
  switch (key) {
    case 'settings':
      return [join(roots.data, 'prefs.json')];
    case 'content':
      return [
        join(roots.data, 'voices'),
        join(roots.data, 'outputs'),
        join(roots.data, 'dub_jobs'),
        join(roots.data, 'batch'),
        join(roots.data, 'preview'),
        ...(await prefixedChildren(roots.data, 'omnivoice.db')),
      ];
    case 'engines':
      return [join(roots.data, 'engines')];
    case 'tools':
      return [join(roots.data, 'media_tools')];
    case 'models':
      return [roots.models];
    case 'caches':
      return [
        join(roots.data, 'gallery_cache'),
        join(roots.data, 'gallery_sources.json'),
        ...(await prefixedChildren(roots.temp, 'omnivoice')),
      ];
    case 'logs':
      return [
        join(roots.data, 'crash_log.txt'),
        join(roots.data, 'error_journal.jsonl'),
        ...(await prefixedChildren(roots.data, 'omnivoice.log')),
        ...(roots.logs ? [roots.logs] : []),
      ];
  }
}

async function authorizedTarget(
  key: DiskResetScope,
  path: string,
  roots: ResetRoots,
  home: string | null,
): Promise<string | null> {
  const physicalTarget = await canonical(path);
  if (!physicalTarget) return null;
  const physicalTemp = await canonical(roots.temp);
  if (
    key === 'caches' &&
    physicalTemp &&
    normalized(dirname(physicalTarget)) === normalized(physicalTemp) &&
    basename(path).toLocaleLowerCase('en-US').startsWith('omnivoice')
  ) {
    return physicalTarget;
  }
  const root =
    key === 'models'
      ? roots.models
      : key === 'logs' && roots.logs && within(physicalTarget, (await canonical(roots.logs)) ?? '')
        ? roots.logs
        : roots.data;
  const physicalRoot = await canonical(root);
  const allowed = Boolean(
    physicalRoot &&
    within(physicalTarget, physicalRoot) &&
    (await isValidVoiceStudioRoot(physicalRoot, home, key === 'models')),
  );
  return allowed ? physicalTarget : null;
}

async function overlapsSharedModels(path: string, models: string | null): Promise<boolean> {
  if (!models) return false;
  const candidate = await physicalPath(path);
  return Boolean(candidate && (within(candidate, models) || within(models, candidate)));
}

export async function scanResetScopes(
  roots: ResetRoots,
  home: string | null = homedir(),
): Promise<ResetScope[]> {
  const scopes: ResetScope[] = FRONTEND_RESET_SCOPES.map((key) => ({
    key,
    paths: [],
    size_bytes: 0,
    exists: true,
    shared: false,
    needs_restart: false,
  }));
  const sharedModels = await modelsAreShared(roots.models, roots.data, home);
  const sharedModelRoot = sharedModels ? await physicalPath(roots.models) : null;
  for (const key of DISK_RESET_SCOPES) {
    const targets = await scopeTargets(key, roots);
    const present: string[] = [];
    for (const target of targets) if (await stat(target)) present.push(target);
    const sizes = await Promise.all(present.map(directorySize));
    scopes.push({
      key,
      paths: present,
      size_bytes: sizes.reduce((sum, size) => sum + size, 0),
      exists: present.length > 0,
      shared:
        key === 'models'
          ? sharedModels
          : (
              await Promise.all(present.map((path) => overlapsSharedModels(path, sharedModelRoot)))
            ).some(Boolean),
      needs_restart: true,
    });
  }
  return scopes;
}

export async function purgeResetScopes(
  roots: ResetRoots,
  requested: string[],
  home: string | null,
): Promise<ResetReport> {
  const report: ResetReport = {
    removed: [],
    failed: [],
    refused: [],
    freed_bytes: 0,
    restarted: false,
  };
  const wanted = DISK_RESET_SCOPES.filter((key) => requested.includes(key));
  const modelsSelected = wanted.includes('models');
  const sharedModelRoot = (await modelsAreShared(roots.models, roots.data, home))
    ? await physicalPath(roots.models)
    : null;
  const targets: string[] = [];
  for (const key of wanted) {
    for (const target of await scopeTargets(key, roots)) {
      if (!(await stat(target))) continue;
      const physicalTarget = await authorizedTarget(key, target, roots, home);
      if (
        !physicalTarget ||
        (!modelsSelected && (await overlapsSharedModels(physicalTarget, sharedModelRoot)))
      ) {
        report.refused.push(target);
        continue;
      }
      targets.push(physicalTarget);
    }
  }
  const unique = [...new Map(targets.map((path) => [normalized(path), path])).values()];
  for (const target of unique.filter(
    (path, index) => !unique.some((parent, other) => other !== index && within(path, parent)),
  )) {
    const size = await directorySize(target);
    try {
      await rm(target, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 });
      report.removed.push(target);
      report.freed_bytes += size;
    } catch {
      report.failed.push(target);
    }
  }
  return report;
}
