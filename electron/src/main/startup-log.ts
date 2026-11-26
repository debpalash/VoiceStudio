// Dependency-free startup evidence. Imported first by index.ts so even a failure
// while loading the remaining modules, or an exit before userData exists, leaves a
// line somewhere a user can find (see docs/install/macos.md).
import { closeSync, constants, fstatSync, ftruncateSync, mkdirSync, openSync, writeSync } from 'node:fs';
import { homedir, release } from 'node:os';
import { join, posix, win32 } from 'node:path';

const MAX_BYTES = 256 * 1024;
const FILE_NAME = 'VoiceStudio-startup.log';

function safeHome(): string {
  try {
    return homedir();
  } catch {
    return '';
  }
}

/**
 * Per-user log directories, most discoverable first. Never the shared temp
 * folder: a predictable name there could be pre-created or linked by another
 * local user.
 */
export function startupLogDirs(
  platform: string = process.platform,
  home: string = safeHome(),
  env: NodeJS.ProcessEnv = process.env,
): string[] {
  if (platform === 'darwin') return home ? [posix.join(home, 'Library', 'Logs', 'VoiceStudio')] : [];
  if (platform === 'win32') {
    const base = env.LOCALAPPDATA || (home ? win32.join(home, 'AppData', 'Local') : '');
    return base ? [win32.join(base, 'VoiceStudio', 'Logs')] : [];
  }
  const state = env.XDG_STATE_HOME || (home ? posix.join(home, '.local', 'state') : '');
  return state ? [posix.join(state, 'VoiceStudio')] : [];
}

/** Path of the startup log in the first directory that can be created, or null. */
export function startupLogPath(dirs: string[] = startupLogDirs()): string | null {
  for (const dir of dirs) {
    try {
      mkdirSync(dir, { recursive: true, mode: 0o700 });
      return join(dir, FILE_NAME);
    } catch {
      /* try the next location */
    }
  }
  return null;
}

// Read-write and created if missing, in one call; never follows a symlink where
// the platform supports refusing one. Not append mode: Windows refuses to
// truncate an append-only handle.
const OPEN_FLAGS = constants.O_RDWR | constants.O_CREAT | (constants.O_NOFOLLOW ?? 0);

let defaultFile: string | null | undefined;

/** Append one timestamped line. Never throws: logging must not cause or mask an exit. */
export function logStartup(message: string, file?: string): void {
  try {
    const target = file ?? (defaultFile ??= startupLogPath());
    if (!target) return;
    const fd = openSync(target, OPEN_FLAGS, 0o600);
    try {
      let end = fstatSync(fd).size;
      if (end > MAX_BYTES) {
        ftruncateSync(fd, 0);
        end = 0;
      }
      writeSync(fd, `${new Date().toISOString()} pid=${process.pid} ${message}\n`, end);
    } finally {
      closeSync(fd);
    }
  } catch {
    /* nowhere to write */
  }
}

function describe(error: unknown): string {
  if (error instanceof Error)
    return (error.stack || `${error.name}: ${error.message}`).slice(0, 4000);
  try {
    return String(error).slice(0, 4000);
  } catch {
    return '[unprintable]';
  }
}

interface ProcessLike {
  on(event: string, listener: (...args: any[]) => void): unknown;
}

/** Log fatal errors and every process exit, before anything else can fail. */
export function installStartupLogging(proc: ProcessLike = process, file?: string): void {
  proc.on('uncaughtExceptionMonitor', (error: unknown, origin: string) =>
    logStartup(`${origin}: ${describe(error)}`, file),
  );
  proc.on('unhandledRejection', (reason: unknown) =>
    logStartup(`unhandledRejection: ${describe(reason)}`, file),
  );
  proc.on('exit', (code: number) => logStartup(`process exit code=${code}`, file));
}

installStartupLogging();
logStartup(
  `launch platform=${process.platform} arch=${process.arch} os=${release()} ` +
    `electron=${process.versions.electron ?? 'n/a'} ` +
    `flags=${process.argv.filter((a) => a.startsWith('--')).join(' ')}`,
);
