import { lstat, readdir, rm } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, parse, relative, resolve } from 'node:path';

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
    children.map((entry) => (entry.isSymbolicLink() ? 0 : directorySize(join(path, entry.name)))),
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
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
}

async function hasAppSignature(path: string): Promise<boolean> {
  for (const marker of ['omnivoice.db', 'prefs.json', 'voices', 'outputs', 'engines']) {
    if (await stat(join(path, marker))) return true;
  }
  const hub = await stat(join(path, 'hub'));
  if (hub?.isDirectory()) return true;
  try {
    return (await readdir(path)).some((name) => name.startsWith('models--'));
  } catch {
    return false;
  }
}

export async function isValidVoiceStudioRoot(path: string, home: string | null): Promise<boolean> {
  if (!isAbsolute(path) || dirname(path) === path || components(path).length < 2) return false;
  if (home && normalized(path) === normalized(home)) return false;
  const owned = new Set([
    'omnivoice',
    '.omnivoice',
    'voicestudio',
    'com.debpalash.omnivoice-studio',
    'huggingface',
  ]);
  if (components(path).some((part) => owned.has(part.toLocaleLowerCase('en-US')))) return true;
  return hasAppSignature(path);
}

export function modelsAreShared(models: string, data: string): boolean {
  if (within(models, data)) return false;
  const appPrivate = new Set(['omnivoice', '.omnivoice', 'voicestudio']);
  return !components(models).some((part) => appPrivate.has(part.toLocaleLowerCase('en-US')));
}

/** Whether a folder contains the data or model folder another scope owns. */
function holdsOtherScopes(path: string, roots: ResetRoots): boolean {
  return within(roots.data, path) || within(roots.models, path);
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
        // The backend reports its crash log's folder as the log root, and that
        // file lives directly in the data folder. Removing such a root would
        // delete every voice, project and setting, not just the logs above.
        ...(roots.logs && !holdsOtherScopes(roots.logs, roots) ? [roots.logs] : []),
      ];
  }
}

async function allowedTarget(
  path: string,
  roots: ResetRoots,
  home: string | null,
): Promise<boolean> {
  if (
    normalized(dirname(path)) === normalized(roots.temp) &&
    basename(path).toLocaleLowerCase('en-US').startsWith('omnivoice')
  ) {
    return true;
  }
  for (const root of [roots.data, roots.models, roots.logs].filter((value): value is string =>
    Boolean(value),
  )) {
    if (within(path, root) && (await isValidVoiceStudioRoot(root, home))) return true;
  }
  return false;
}

export async function scanResetScopes(roots: ResetRoots): Promise<ResetScope[]> {
  const scopes: ResetScope[] = FRONTEND_RESET_SCOPES.map((key) => ({
    key,
    paths: [],
    size_bytes: 0,
    exists: true,
    shared: false,
    needs_restart: false,
  }));
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
      shared: key === 'models' && modelsAreShared(roots.models, roots.data),
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
  for (const key of wanted) {
    for (const target of await scopeTargets(key, roots)) {
      if (!(await stat(target))) continue;
      if (!(await allowedTarget(target, roots, home))) {
        report.refused.push(target);
        continue;
      }
      const size = await directorySize(target);
      try {
        await rm(target, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 });
        report.removed.push(target);
        report.freed_bytes += size;
      } catch {
        report.failed.push(target);
      }
    }
  }
  return report;
}
