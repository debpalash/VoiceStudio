import { arch, cpus, release, totalmem, version } from 'node:os';
import { stat, statfs } from 'node:fs/promises';
import { dirname } from 'node:path';

export interface HostSnapshot {
  os: string;
  arch: string;
  cpu: string | null;
  ramGb: number;
  diskFreeGb: number | null;
  gpu: string | null;
  runtime: {
    pythonPresent: boolean | null;
    uvPresent: boolean | null;
    pathFlags: string[];
  };
}

export interface HostSnapshotInput {
  platform?: NodeJS.Platform;
  /** Runtime project directory (holds `.venv`); null when it cannot be resolved. */
  runtimeProject: string | null;
  runtimePython: string | null;
  /** A path, or an async search: a completed search with no result means "not installed". */
  uv: string | null | (() => Promise<string | null>);
  gpu?: () => Promise<string | null>;
  /** Test seam for the filesystem stats call. */
  statfs?: typeof statfs;
}

/**
 * Path traits that explain launch failures (`spawn UNKNOWN`, access
 * violations, EPERM renames) without printing the path itself, which is
 * private. Stable tokens so triage can grep them.
 */
export function pathRiskFlags(
  path: string,
  platform: NodeJS.Platform = process.platform,
): string[] {
  const flags: string[] = [];
  if (/onedrive/i.test(path)) flags.push('onedrive');
  if (/[^\x00-\x7f]/.test(path)) flags.push('non-ascii');
  if (/\s/.test(path)) flags.push('spaces');
  if (platform === 'win32') {
    if (/^\\\\/.test(path)) flags.push('network-share');
    if (/[\\/]windows[\\/]system32/i.test(path)) flags.push('system32');
    else if (/[\\/]program files/i.test(path)) flags.push('program-files');
    if (path.length > 180) flags.push('long-path');
  }
  return flags;
}

async function exists(path: string | null): Promise<boolean | null> {
  if (!path) return null;
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

export async function freeGb(path: string | null, statfsFn: typeof statfs = statfs): Promise<number | null> {
  if (!path) return null;
  // First run: the project dir does not exist yet, but its drive does.
  let probe = path;
  for (;;) {
    try {
      const fs = await statfsFn(probe);
      return Math.round(((fs.bavail * fs.bsize) / 1024 ** 3) * 10) / 10;
    } catch (error) {
      // Only a missing path borrows its parent; an unreadable one stays unknown.
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') return null;
      const parent = dirname(probe);
      if (parent === probe) return null;
      probe = parent;
    }
  }
}

async function withTimeout<T>(task: Promise<T>, ms: number): Promise<T | null> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      task,
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), ms);
      }),
    ]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const TIMED_OUT = Symbol('timed-out');

async function uvPresence(uv: HostSnapshotInput['uv']): Promise<boolean | null> {
  if (typeof uv !== 'function') return withTimeout(exists(uv), 1500);
  let timer: NodeJS.Timeout | undefined;
  try {
    const found = await Promise.race([
      uv(),
      new Promise<typeof TIMED_OUT>((resolve) => {
        timer = setTimeout(() => resolve(TIMED_OUT), 1500);
      }),
    ]);
    return found === TIMED_OUT ? null : found !== null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Machine and runtime facts read in the main process, so a bug report is
 * useful even when the backend never started and `/system/info` is unreachable. */
export async function collectHostSnapshot(input: HostSnapshotInput): Promise<HostSnapshot> {
  const platform = input.platform ?? process.platform;
  const [pythonPresent, uvPresent, diskFreeGb, gpu] = await Promise.all([
    withTimeout(exists(input.runtimePython), 1500),
    uvPresence(input.uv),
    withTimeout(freeGb(input.runtimeProject, input.statfs), 1500),
    input.gpu ? withTimeout(input.gpu(), 1500) : Promise.resolve(null),
  ]);
  return {
    os: `${platform} ${version()} (${release()})`,
    arch: arch(),
    cpu: cpus()[0]?.model?.trim() || null,
    ramGb: Math.round((totalmem() / 1024 ** 3) * 10) / 10,
    diskFreeGb,
    gpu,
    runtime: {
      pythonPresent,
      uvPresent,
      pathFlags: input.runtimeProject ? pathRiskFlags(input.runtimeProject, platform) : [],
    },
  };
}
