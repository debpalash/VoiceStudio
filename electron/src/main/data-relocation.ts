import {
  cp,
  chmod,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  readlink,
  realpath,
  rmdir,
  rm,
  statfs,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { createReadStream, type BigIntStats } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path';
import { DIRECTORY_RENAME, renameWithRetry } from './rename-retry';

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
  rollback(): Promise<boolean>;
  /** Recognize narrow startup writes after the failed backend has stopped. */
  captureStartupWrites?(): Promise<void>;
  validateTarget?(): Promise<void>;
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
  // A name such as '..models' is a child; only a complete '..' component escapes.
  return rel === '' || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

function safeDestination(path: string): boolean {
  if (!isAbsolute(path) || dirname(path) === path || /[\0\r\n]/.test(path)) return false;
  if (normalized(path) === normalized(homedir())) return false;
  const root = parse(path).root;
  // Any single directory under the volume root is a valid destination;
  // dirname(path) === path above already refuses the root itself.
  return (
    path
      .slice(root.length)
      .split(/[\\/]+/)
      .filter(Boolean).length >= 1
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

async function manifest(root: string, ignoredRootEntry?: string): Promise<ManifestEntry[]> {
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
      for (const child of children) {
        if (path === '' && child === ignoredRootEntry) continue;
        await visit(join(absolute, child), join(path, child));
      }
      return;
    }
    if (!info.isFile()) throw new Error('unsupported_entry');
    output.push({ path, kind: 'file', size: info.size });
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
  const capacityPath = await lstat(canonicalTarget).then(
    () => canonicalTarget,
    (error: NodeJS.ErrnoException) => {
      if (error.code !== 'ENOENT') throw error;
      return dirname(canonicalTarget);
    },
  );
  const capacity = await statfs(capacityPath);
  const available = Number(capacity.bavail) * Number(capacity.bsize);
  if (available < size + MOVE_RESERVE_BYTES) throw new Error('insufficient_space');
  return {
    source: canonicalSource,
    target: canonicalTarget,
    size_bytes: size,
    file_count: entries.filter((entry) => entry.kind !== 'directory').length,
  };
}

interface OwnedEntry {
  path: string;
  kind: ManifestEntry['kind'];
  dev: bigint;
  ino: bigint;
  birthtimeNs: bigint;
  ctimeNs?: bigint;
  size?: bigint;
  fileMode?: bigint;
  digest?: string;
  link?: string;
  mode?: number;
}

function fileFingerprint(info: BigIntStats, digest?: string) {
  return {
    dev: info.dev,
    ino: info.ino,
    birthtimeNs: info.birthtimeNs,
    ctimeNs: info.ctimeNs,
    size: info.size,
    fileMode: info.mode,
    digest,
  };
}

function sameFileGeneration(info: BigIntStats, entry: ReturnType<typeof fileFingerprint>): boolean {
  return (
    info.dev === entry.dev &&
    info.ino === entry.ino &&
    info.birthtimeNs === entry.birthtimeNs &&
    info.ctimeNs === entry.ctimeNs &&
    info.size === entry.size &&
    info.mode === entry.fileMode
  );
}

async function matchesFile(
  path: string,
  info: BigIntStats,
  entry: ReturnType<typeof fileFingerprint>,
): Promise<boolean> {
  if (!info.isFile() || !sameFileGeneration(info, entry)) return false;
  // Creation and change timestamps can both be coarse, so even a nonzero
  // matching generation is not proof after unlink/recreate. Check copied bytes
  // before destructive rollback; successful moves do not reread model files.
  if (!entry.digest) return false;
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  if (hash.digest('hex') !== entry.digest) return false;
  return sameFileGeneration(await lstat(path, { bigint: true }), entry);
}

class RollbackIncompleteError extends Error {
  constructor(cause: unknown) {
    super('rollback_incomplete', { cause });
  }
}

class TargetChangedError extends Error {
  constructor() {
    super('target_changed');
  }
}

// Inode timestamps come from a coarse clock (one kernel tick on Linux, up to
// 1 s on HFS+), so a probe in the same tick as mkdir leaves ctime unchanged.
// That proves nothing either way: wait and probe again before giving up.
const IDENTITY_PROBE_RETRY_MS = [10, 100, 1000];
const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// A nonzero creation timestamp may be a mutable ctime fallback. Only probe
// ambiguous roots; a fallback moves with ctime, a real creation time stays.
async function stableDirectoryIdentity(
  target: string,
  selected: BigIntStats,
): Promise<BigIntStats> {
  if (selected.birthtimeNs === 0n) throw new Error('identity_unavailable');
  if (selected.birthtimeNs !== selected.ctimeNs) return selected;
  for (const delay of [0, ...IDENTITY_PROBE_RETRY_MS]) {
    if (delay) await wait(delay);
    const after = await probeDirectoryChange(target, selected);
    if (after.birthtimeNs !== selected.birthtimeNs) throw new Error('identity_unavailable');
    if (after.birthtimeNs !== after.ctimeNs) return after;
  }
  throw new Error('identity_unavailable');
}

// Keep the exclusive marker open so inode reuse cannot make cleanup adopt a
// replacement. No model files are open during this check.
async function probeDirectoryChange(target: string, selected: BigIntStats): Promise<BigIntStats> {
  const marker = join(target, `.voicestudio-identity-${randomUUID()}`);
  const handle = await open(marker, 'wx');
  try {
    const owned = await handle.stat({ bigint: true });
    const present = await lstat(marker, { bigint: true });
    if (
      !present.isFile() ||
      present.size !== 0n ||
      !sameFileGeneration(present, fileFingerprint(owned))
    )
      throw new TargetChangedError();
    await rm(marker);
  } catch (error) {
    throw new RollbackIncompleteError(error);
  } finally {
    await handle.close();
  }
  const after = await lstat(target, { bigint: true });
  if (
    !after.isDirectory() ||
    after.isSymbolicLink() ||
    after.dev !== selected.dev ||
    after.ino !== selected.ino
  )
    throw new TargetChangedError();
  return after;
}

async function bindTargetDirectory(
  target: string,
  requestedTarget: string,
  identity?: BigIntStats,
) {
  const initial = identity ?? (await lstat(target, { bigint: true }));
  if (!initial.isDirectory() || initial.isSymbolicLink()) throw new TargetChangedError();
  const selected = await stableDirectoryIdentity(target, initial);
  const selectedStillPresent = async (): Promise<boolean> => {
    try {
      const info = await lstat(target, { bigint: true });
      const requested = await stat(requestedTarget, { bigint: true });
      return (
        requested.isDirectory() &&
        requested.dev === selected.dev &&
        requested.ino === selected.ino &&
        requested.birthtimeNs === selected.birthtimeNs &&
        info.isDirectory() &&
        !info.isSymbolicLink() &&
        info.dev === selected.dev &&
        info.ino === selected.ino &&
        info.birthtimeNs === selected.birthtimeNs
      );
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
      throw error;
    }
  };
  const validateTarget = async (): Promise<void> => {
    if (!(await selectedStillPresent())) throw new TargetChangedError();
  };
  return { validateTarget, selectedStillPresent };
}

// Existing folders may be mount points, or have an unwritable parent. Keep the
// folder itself and reserve each child exclusively: a POSIX rename can overwrite a
// concurrent file on POSIX. Copy once, then verify before starting the backend.
async function prepareExistingDestination(
  plan: DataRelocationPlan,
  sourceManifest: ManifestEntry[],
  requestedTarget: string,
): Promise<PreparedDataRelocation> {
  const { validateTarget, selectedStillPresent } = await bindTargetDirectory(
    plan.target,
    requestedTarget,
  );
  const staging = join(plan.target, `.voicestudio-moving-${randomUUID()}`);
  await mkdir(staging, { recursive: false });
  let stagingInfo: BigIntStats;
  try {
    await validateTarget();
    stagingInfo = await lstat(staging, { bigint: true });
  } catch (error) {
    throw new RollbackIncompleteError(error);
  }
  let recordInfo: ReturnType<typeof fileFingerprint> | undefined;
  const cleanupStaging = async (): Promise<void> => {
    if (!(await selectedStillPresent())) return;
    let info;
    try {
      info = await lstat(staging, { bigint: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
    if (
      !info.isDirectory() ||
      info.isSymbolicLink() ||
      info.dev !== stagingInfo.dev ||
      info.ino !== stagingInfo.ino ||
      info.birthtimeNs !== stagingInfo.birthtimeNs ||
      info.birthtimeNs === 0n
    )
      return;
    if (recordInfo) {
      const record = join(staging, 'manifest.json');
      const present = await lstat(record, { bigint: true }).catch(
        (error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOENT') throw error;
          return null;
        },
      );
      if (present && (await matchesFile(record, present, recordInfo)))
        await rm(record, { force: true });
    }
    await rmdir(staging).catch((error: NodeJS.ErrnoException) => {
      if (!['ENOENT', 'ENOTEMPTY', 'EEXIST'].includes(error.code || '')) throw error;
    });
  };
  const owned: OwnedEntry[] = [];
  const ownedDirectories = new Map<string, OwnedEntry>();
  const remember = async (
    path: string,
    kind: ManifestEntry['kind'],
    mode?: number,
  ): Promise<void> => {
    const info = await lstat(join(plan.target, path), { bigint: true });
    const entry: OwnedEntry = { path, kind, ...fileFingerprint(info), mode };
    if (kind === 'link') entry.link = await readlink(join(plan.target, path));
    owned.push(entry);
    if (kind === 'directory') ownedDirectories.set(path, entry);
  };
  const stillOwned = async (entry: OwnedEntry): Promise<boolean> => {
    try {
      const info = await lstat(join(plan.target, entry.path), { bigint: true });
      if (
        info.dev !== entry.dev ||
        info.ino !== entry.ino ||
        info.birthtimeNs !== entry.birthtimeNs
      )
        return false;
      if (entry.kind === 'directory')
        return info.isDirectory() && !info.isSymbolicLink() && info.birthtimeNs !== 0n;
      if (entry.kind === 'link')
        return (
          info.isSymbolicLink() &&
          info.ctimeNs === entry.ctimeNs &&
          (await readlink(join(plan.target, entry.path))) === entry.link
        );
      return matchesFile(
        join(plan.target, entry.path),
        info,
        entry as ReturnType<typeof fileFingerprint>,
      );
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
      throw error;
    }
  };
  const ownsPath = async (entry: OwnedEntry): Promise<boolean> => {
    if (!(await selectedStillPresent())) return false;
    for (let parent = dirname(entry.path); parent !== '.'; parent = dirname(parent)) {
      const directory = ownedDirectories.get(parent);
      if (directory && !(await stillOwned(directory))) return false;
    }
    return stillOwned(entry);
  };
  const directoryModes = async (writable: boolean): Promise<void> => {
    const directories = [...ownedDirectories.values()];
    // Open parents before their children, restore children before parents.
    if (!writable) directories.reverse();
    for (const entry of directories) {
      if (await ownsPath(entry)) {
        await chmod(join(plan.target, entry.path), writable ? entry.mode! | 0o700 : entry.mode!);
      }
    }
  };
  const destinationClean = async (): Promise<boolean> =>
    (await selectedStillPresent()) && (await readdir(plan.target)).length === 0;
  const rollback = async (): Promise<boolean> => {
    // Never follow a replaced parent, delete recursively, or remove unrelated
    // files added while copying / verifying the new backend.
    try {
      await directoryModes(true);
      for (const entry of [...owned].reverse()) {
        if (!(await ownsPath(entry))) continue;
        if (entry.kind === 'directory') {
          await rmdir(join(plan.target, entry.path)).catch((error: NodeJS.ErrnoException) => {
            if (!['ENOENT', 'ENOTEMPTY', 'EEXIST'].includes(error.code || '')) throw error;
          });
        } else {
          await rm(join(plan.target, entry.path), { force: true });
        }
      }
    } finally {
      // A concurrent addition keeps its parent alive; do not leave that folder
      // with the temporary write permission used for cleanup.
      await directoryModes(false);
    }
    return destinationClean();
  };
  try {
    // A small in-target transaction record; model weights are never copied a
    // second time, even on filesystems without hard links (such as exFAT).
    const record = await open(join(staging, 'manifest.json'), 'wx');
    try {
      const content = JSON.stringify(sourceManifest);
      await record.writeFile(content);
      recordInfo = fileFingerprint(
        await record.stat({ bigint: true }),
        createHash('sha256').update(content).digest('hex'),
      );
    } finally {
      await record.close();
    }
    for (const entry of sourceManifest) {
      if (!entry.path) continue;
      await validateTarget();
      const destination = join(plan.target, entry.path);
      const original = join(plan.source, entry.path);
      if (entry.kind === 'directory') {
        const mode = (await lstat(original)).mode & 0o7777;
        await mkdir(destination, { recursive: false, mode: mode | 0o700 });
        await remember(entry.path, entry.kind, mode);
      } else if (entry.kind === 'link') {
        const directoryLink = await stat(original).then(
          (info) => info.isDirectory(),
          () => false,
        );
        await symlink(entry.link!, destination, directoryLink ? 'dir' : 'file');
        await remember(entry.path, entry.kind);
      } else {
        const info = await lstat(original);
        const output = await open(destination, 'wx', info.mode);
        const hash = createHash('sha256');
        const copied: OwnedEntry = {
          path: entry.path,
          kind: entry.kind,
          ...fileFingerprint(await output.stat({ bigint: true })),
        };
        owned.push(copied);
        try {
          await output.chmod(info.mode);
          async function* chunks() {
            for await (const chunk of createReadStream(original)) {
              hash.update(chunk);
              yield chunk;
            }
          }
          await output.writeFile(chunks());
        } finally {
          // Seal expected copy writes through the same open inode, not a path
          // that may already have been replaced. Only this one file stays open.
          try {
            Object.assign(
              copied,
              fileFingerprint(await output.stat({ bigint: true }), hash.digest('hex')),
            );
          } finally {
            await output.close();
          }
        }
      }
    }
    if (
      JSON.stringify(await manifest(plan.target, basename(staging))) !==
      JSON.stringify(sourceManifest)
    ) {
      throw new Error('verification_failed');
    }
    await validateTarget();
    await directoryModes(false);
    await cleanupStaging();
    await validateTarget();
  } catch (error) {
    await rollback().catch(() => undefined);
    await cleanupStaging().catch(() => undefined);
    if (!(await destinationClean().catch(() => false))) throw new RollbackIncompleteError(error);
    throw error;
  }
  // Startup replaces this one advisory index. Seed its expected contents only
  // while the copied cache/index still belongs to this transaction; unrelated
  // replacements are never made ours just because they occupy the same path.
  const startupHandoff = async (): Promise<PreparedDataRelocation['captureStartupWrites']> => {
    const cachePath = join('outputs', 'longform_cache');
    const cache = ownedDirectories.get(cachePath);
    if (!cache || !(await ownsPath(cache))) return;
    const indexPath = join(cachePath, 'voices_roots.json');
    const index = owned.find((entry) => entry.path === indexPath);
    if (index && (index.kind === 'directory' || !(await ownsPath(index)))) return;
    if (!index) {
      try {
        await lstat(join(plan.target, indexPath));
        return;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') return;
      }
    }
    let seen: string[] = [];
    try {
      const parsed: unknown = index
        ? JSON.parse(await readFile(join(plan.target, indexPath), 'utf8'))
        : [];
      if (Array.isArray(parsed))
        seen = parsed.filter((root): root is string => typeof root === 'string' && root.length > 0);
    } catch {
      /* Backend startup treats an absent/invalid index as empty. */
    }
    if (index && !(await ownsPath(index))) return;
    // core.config preserves the env spelling, and Python os.path.join does not
    // normalize it. In particular C:/data becomes C:/data\voices on Windows.
    const trailingSeparator =
      requestedTarget.endsWith(sep) || (sep === '\\' && requestedTarget.endsWith('/'));
    const current = requestedTarget + (trailingSeparator ? '' : sep) + 'voices';
    // Match services.longform_render.remember_voices_root's filtering/order/cap.
    const expected = JSON.stringify(
      [current, ...seen.filter((root) => root !== current)].slice(0, 8),
    );
    return async () => {
      if (!(await ownsPath(cache))) return;
      const file = join(plan.target, indexPath);
      const handle = await open(file, 'r');
      try {
        const info = await handle.stat({ bigint: true });
        if (!info.isFile()) return;
        const content = await handle.readFile('utf8');
        const parsed: unknown = JSON.parse(content);
        if (JSON.stringify(parsed) !== expected) return;
        const pathInfo = await lstat(file, { bigint: true });
        if (pathInfo.dev !== info.dev || pathInfo.ino !== info.ino) return;
        if (!(await ownsPath(cache))) return;
        const entry: OwnedEntry = {
          path: indexPath,
          kind: 'file',
          ...fileFingerprint(info, createHash('sha256').update(content).digest('hex')),
        };
        if (index) Object.assign(index, entry);
        else owned.push(entry);
      } finally {
        await handle.close();
      }
    };
  };
  return {
    ...plan,
    removeSource: async () => {
      await validateTarget();
      await rm(plan.source, { recursive: true, force: false });
    },
    rollback,
    validateTarget,
    captureStartupWrites: await startupHandoff().catch(() => undefined),
  };
}

export async function prepareDataRelocation(
  source: string,
  target: string,
): Promise<PreparedDataRelocation> {
  const plan = await inspectDataRelocation(source, target);
  const sourceManifest = await manifest(plan.source);
  const existing = await lstat(plan.target).then(
    () => true,
    (error: NodeJS.ErrnoException) => {
      if (error.code !== 'ENOENT') throw error;
      return false;
    },
  );
  if (existing) return prepareExistingDestination(plan, sourceManifest, target);
  const parent = dirname(plan.target);
  const staging = join(parent, `.${basename(plan.target)}.voicestudio-moving-${randomUUID()}`);
  await mkdir(staging, { recursive: false });
  const originalStagedIdentity = await lstat(staging, { bigint: true });
  let stagedIdentity = originalStagedIdentity;
  let identityVerified = false;
  try {
    stagedIdentity = await stableDirectoryIdentity(staging, stagedIdentity);
    identityVerified = true;
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
      // rm() rejects directories without `recursive`; rmdir removes only an
      // empty one and refuses (ENOTEMPTY) if content appeared since inspection.
      await rmdir(plan.target);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    await renameWithRetry(staging, plan.target, DIRECTORY_RENAME);
  } catch (error) {
    try {
      const present = await lstat(staging, { bigint: true }).catch(
        (missing: NodeJS.ErrnoException) => {
          if (missing.code === 'ENOENT') return null;
          throw missing;
        },
      );
      if (present) {
        if (
          !present.isDirectory() ||
          present.isSymbolicLink() ||
          present.dev !== originalStagedIdentity.dev ||
          present.ino !== originalStagedIdentity.ino ||
          (identityVerified && present.birthtimeNs !== stagedIdentity.birthtimeNs)
        )
          throw new TargetChangedError();
        // Unverified preflight has copied no data: remove only an empty folder.
        // Once verified, recursive cleanup still requires the original generation.
        if (identityVerified) await rm(staging, { recursive: true, force: false });
        else await rmdir(staging);
      }
    } catch {
      throw new RollbackIncompleteError(error);
    }
    throw error;
  }
  let binding: Awaited<ReturnType<typeof bindTargetDirectory>>;
  try {
    binding = await bindTargetDirectory(plan.target, target, stagedIdentity);
    await binding.validateTarget();
  } catch (error) {
    throw new RollbackIncompleteError(error);
  }
  return {
    ...plan,
    removeSource: async () => {
      await binding.validateTarget();
      await rm(plan.source, { recursive: true, force: false });
    },
    rollback: async () => {
      if (!(await binding.selectedStillPresent())) return false;
      await rm(plan.target, { recursive: true, force: true });
      return true;
    },
    validateTarget: binding.validateTarget,
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
  await renameWithRetry(temporary, file);
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
    await renameWithRetry(temporary, path);
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
    previousSetting = await readDataDirectorySetting(runtime.environmentPath);
    runtime.progress('switching');
    await writeDataDirectorySetting(target, runtime.environmentPath);
    settingChanged = true;
    runtime.progress('restarting');
    await prepared.validateTarget?.();
    // A rejected start may already have written startup metadata.
    started = true;
    await runtime.start();
    if (!(await runtime.verify(target))) throw new Error('backend_verification_failed');
    await prepared.validateTarget?.();
    // Advisory cache writes replace their inode; defer them until activation so
    // a failed move can identify every copied entry during rollback.
    await recordPreviousVoicesRoot(prepared.source, prepared.target).catch(() => undefined);
    runtime.progress('cleaning');
    let removedSource = true;
    try {
      await prepared.removeSource();
    } catch (error) {
      if (error instanceof TargetChangedError) throw error;
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
    let backendStopped = !started;
    if (started) {
      try {
        await runtime.stop();
        backendStopped = true;
      } catch {
        /* Keep a potentially live copy intact. */
      }
    }
    let incomplete = error instanceof RollbackIncompleteError || !backendStopped;
    let settingRestored = !settingChanged;
    if (settingChanged) {
      try {
        await writeDataDirectorySetting(previousSetting, runtime.environmentPath);
        settingRestored = true;
      } catch {
        incomplete = true;
      }
    }
    if (prepared && backendStopped && settingRestored) {
      await prepared.captureStartupWrites?.().catch(() => undefined);
      try {
        if (!(await prepared.rollback())) incomplete = true;
      } catch {
        incomplete = true;
      }
    }
    if (stopped && backendStopped && settingRestored) {
      try {
        await runtime.start();
      } catch {
        incomplete = true;
      }
    }
    if (incomplete && !(error instanceof RollbackIncompleteError))
      throw new RollbackIncompleteError(error);
    throw error;
  }
}
