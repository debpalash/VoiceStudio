import {
  cp,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  readlink,
  realpath,
  rename,
  rm,
  statfs,
  writeFile,
} from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, parse, relative, resolve } from 'node:path';

const DATA_KEY = 'OMNIVOICE_DATA_DIR';
const MOVE_RESERVE_BYTES = 64 * 1024 * 1024;

interface ManifestEntry {
  path: string;
  kind: 'file' | 'directory' | 'link';
  size: number;
  link?: string;
}

export interface DataRelocationPlan {
  source: string;
  target: string;
  size_bytes: number;
  file_count: number;
}

export interface PreparedDataRelocation extends DataRelocationPlan {
  removeSource(): Promise<void>;
  rollback(): Promise<void>;
}

export interface DataRelocationResult {
  path: string;
  size_bytes: number;
  file_count: number;
  removed_source: boolean;
}

export interface DataRelocationRuntime {
  stop(): Promise<void>;
  start(): Promise<void>;
  verify(target: string): Promise<boolean>;
  progress(stage: string): void;
  environmentPath?: string;
}

function normalized(path: string): string {
  const value = resolve(path);
  return process.platform === 'win32' ? value.toLocaleLowerCase('en-US') : value;
}

function contains(parent: string, child: string): boolean {
  const rel = relative(normalized(parent), normalized(child));
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
}

function safeDestination(path: string): boolean {
  if (!isAbsolute(path) || dirname(path) === path || /[\0\r\n]/.test(path)) return false;
  if (normalized(path) === normalized(homedir())) return false;
  const root = parse(path).root;
  return (
    path
      .slice(root.length)
      .split(/[\\/]+/)
      .filter(Boolean).length >= 2
  );
}

async function canonicalDestination(path: string): Promise<string> {
  try {
    return await realpath(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return join(await realpath(dirname(path)), basename(path));
  }
}

async function manifest(root: string): Promise<ManifestEntry[]> {
  const output: ManifestEntry[] = [];
  const visit = async (absolute: string, path: string): Promise<void> => {
    const info = await lstat(absolute);
    if (info.isSymbolicLink()) {
      output.push({ path, kind: 'link', size: 0, link: await readlink(absolute) });
      return;
    }
    if (info.isDirectory()) {
      output.push({ path, kind: 'directory', size: 0 });
      const children = await readdir(absolute);
      children.sort();
      for (const child of children) await visit(join(absolute, child), join(path, child));
      return;
    }
    if (info.isFile()) output.push({ path, kind: 'file', size: info.size });
  };
  await visit(root, '');
  return output;
}

function manifestBytes(entries: ManifestEntry[]): number {
  return entries.reduce((total, entry) => total + entry.size, 0);
}

async function targetIsEmpty(path: string): Promise<boolean> {
  try {
    const info = await lstat(path);
    return info.isDirectory() && !info.isSymbolicLink() && (await readdir(path)).length === 0;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
    throw error;
  }
}

export async function inspectDataRelocation(
  source: string,
  target: string,
): Promise<DataRelocationPlan> {
  if (!isAbsolute(source) || !safeDestination(target)) throw new Error('unsafe_path');
  const canonicalSource = await realpath(source);
  const canonicalTarget = await canonicalDestination(target);
  if (contains(canonicalSource, canonicalTarget) || contains(canonicalTarget, canonicalSource)) {
    throw new Error('nested_path');
  }
  const sourceInfo = await lstat(canonicalSource);
  if (!sourceInfo.isDirectory() || sourceInfo.isSymbolicLink()) throw new Error('invalid_source');
  if (!(await targetIsEmpty(canonicalTarget))) throw new Error('target_not_empty');
  const entries = await manifest(canonicalSource);
  const size = manifestBytes(entries);
  const capacity = await statfs(dirname(canonicalTarget));
  const available = Number(capacity.bavail) * Number(capacity.bsize);
  if (available < size + MOVE_RESERVE_BYTES) throw new Error('insufficient_space');
  return {
    source: canonicalSource,
    target: canonicalTarget,
    size_bytes: size,
    file_count: entries.filter((entry) => entry.kind !== 'directory').length,
  };
}

export async function prepareDataRelocation(
  source: string,
  target: string,
): Promise<PreparedDataRelocation> {
  const plan = await inspectDataRelocation(source, target);
  const sourceManifest = await manifest(plan.source);
  const parent = dirname(plan.target);
  const staging = join(parent, `.${basename(plan.target)}.voicestudio-moving-${randomUUID()}`);
  await mkdir(staging, { recursive: false });
  try {
    for (const entry of await readdir(plan.source)) {
      await cp(join(plan.source, entry), join(staging, entry), {
        recursive: true,
        errorOnExist: true,
        force: false,
        verbatimSymlinks: true,
      });
    }
    const copiedManifest = await manifest(staging);
    if (JSON.stringify(copiedManifest) !== JSON.stringify(sourceManifest)) {
      throw new Error('verification_failed');
    }
    try {
      await rm(plan.target, { recursive: false });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    await rename(staging, plan.target);
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => undefined);
    throw error;
  }
  return {
    ...plan,
    removeSource: () => rm(plan.source, { recursive: true, force: false }),
    rollback: () => rm(plan.target, { recursive: true, force: true }),
  };
}

/**
 * Leave the old voices root on record in the moved longform cache (#2279).
 * Chapters rendered by builds before the portable cache key are keyed by the
 * reference audio's absolute path under the OLD data dir; the backend probes
 * every root listed in `voices_roots.json` for them. It records its own root
 * at startup, and this covers the canonical spelling of the source the move
 * came from. Best-effort: no cache means nothing legacy to find, and a failed
 * write never fails the move.
 */
export async function recordPreviousVoicesRoot(source: string, target: string): Promise<void> {
  const cacheDir = join(target, 'outputs', 'longform_cache');
  try {
    if (!(await lstat(cacheDir)).isDirectory()) return;
  } catch {
    return;
  }
  const file = join(cacheDir, 'voices_roots.json');
  let seen: string[] = [];
  try {
    const parsed: unknown = JSON.parse(await readFile(file, 'utf8'));
    if (Array.isArray(parsed)) {
      seen = parsed.filter((root): root is string => typeof root === 'string' && root.length > 0);
    }
  } catch {
    seen = [];
  }
  const previous = join(source, 'voices');
  if (seen.includes(previous)) return;
  const temporary = `${file}.${randomUUID()}.tmp`;
  const handle = await open(temporary, 'w');
  try {
    await handle.writeFile(JSON.stringify([previous, ...seen].slice(0, 8)));
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporary, file);
  await syncDirectory(cacheDir);
}

// Persist a rename into `dir` (POSIX). Windows has no directory handle to
// fsync and journals renames itself; failure is best-effort, like the move.
async function syncDirectory(dir: string): Promise<void> {
  if (process.platform === 'win32') return;
  try {
    const handle = await open(dir, 'r');
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  } catch {
    // The index is advisory; a lost entry only costs one re-render.
  }
}

export function userEnvironmentPath(): string {
  return process.env.OMNIVOICE_ENV_FILE || join(homedir(), '.config', 'omnivoice', 'env');
}

export async function readDataDirectorySetting(
  path = userEnvironmentPath(),
): Promise<string | null> {
  let text = '';
  try {
    text = await readFile(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const prefix = `${DATA_KEY}=`;
  const line = text.split(/\r?\n/).find((value) => value.startsWith(prefix));
  if (!line) return null;
  const value = line.slice(prefix.length);
  if (value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replace(/\\(['\\])/g, '$1');
  }
  return value;
}

export async function writeDataDirectorySetting(
  value: string | null,
  path = userEnvironmentPath(),
): Promise<void> {
  let text = '';
  try {
    text = await readFile(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const prefix = `${DATA_KEY}=`;
  const lines = text.split(/\r?\n/).filter((line) => line && !line.startsWith(prefix));
  if (value) {
    if (/[\0\r\n]/.test(value)) throw new Error('unsafe_path');
    const encoded = value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    lines.push(`${DATA_KEY}='${encoded}'`);
  }
  const body = lines.length ? `${lines.join('\n')}\n` : '';
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, body, { encoding: 'utf8', mode: 0o600 });
  try {
    await rename(temporary, path);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

export async function relocateDataDirectory(
  source: string,
  target: string,
  runtime: DataRelocationRuntime,
): Promise<DataRelocationResult> {
  let prepared: PreparedDataRelocation | null = null;
  let previousSetting: string | null = null;
  let settingChanged = false;
  let stopped = false;
  let started = false;
  try {
    runtime.progress('stopping');
    await runtime.stop();
    stopped = true;
    runtime.progress('copying');
    prepared = await prepareDataRelocation(source, target);
    await recordPreviousVoicesRoot(prepared.source, prepared.target).catch(() => undefined);
    previousSetting = await readDataDirectorySetting(runtime.environmentPath);
    runtime.progress('switching');
    await writeDataDirectorySetting(target, runtime.environmentPath);
    settingChanged = true;
    runtime.progress('restarting');
    await runtime.start();
    started = true;
    if (!(await runtime.verify(target))) throw new Error('backend_verification_failed');
    runtime.progress('cleaning');
    let removedSource = true;
    try {
      await prepared.removeSource();
    } catch {
      removedSource = false;
    }
    runtime.progress('done');
    return {
      path: target,
      size_bytes: prepared.size_bytes,
      file_count: prepared.file_count,
      removed_source: removedSource,
    };
  } catch (error) {
    runtime.progress('rolling_back');
    if (started) await runtime.stop().catch(() => undefined);
    if (settingChanged) {
      await writeDataDirectorySetting(previousSetting, runtime.environmentPath).catch(
        () => undefined,
      );
    }
    if (prepared) await prepared.rollback().catch(() => undefined);
    if (stopped) await runtime.start().catch(() => undefined);
    throw error;
  }
}
