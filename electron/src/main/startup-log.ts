// Dependency-free startup evidence. Imported first by index.ts so even a failure
// while loading the remaining modules, or an exit before userData exists, leaves a
// line somewhere a user can find (see docs/install/macos.md).
import { closeSync, fstatSync, ftruncateSync, mkdirSync, openSync, writeSync } from 'node:fs';
import { homedir, release, tmpdir } from 'node:os';
import { join, posix } from 'node:path';

const MAX_BYTES = 256 * 1024;
const FILE_NAME = 'VoiceStudio-startup.log';

function safeHome(): string {
  try {
    return homedir();
  } catch {
    return '';
  }
}

/** Candidate directories, most discoverable first; the temp dir is the last resort. */
export function startupLogDirs(
  platform: string = process.platform,
  home: string = safeHome(),
  temp: string = tmpdir(),
): string[] {
  const dirs: string[] = [];
  if (platform === 'darwin' && home) dirs.push(posix.join(home, 'Library', 'Logs', 'VoiceStudio'));
  dirs.push(temp);
  return dirs;
}

/** Path of the startup log: the first candidate directory that can be created. */
export function startupLogPath(dirs: string[] = startupLogDirs()): string {
  for (const dir of dirs) {
    try {
      mkdirSync(dir, { recursive: true });
      return join(dir, FILE_NAME);
    } catch {
      /* try the next location */
    }
  }
  return join(tmpdir(), FILE_NAME);
}

let defaultFile: string | null = null;

/** Append one timestamped line. Never throws: logging must not cause or mask an exit. */
export function logStartup(message: string, file?: string): void {
  try {
    const target = file ?? (defaultFile ??= startupLogPath());
    // One descriptor for the size check and the write, so nothing can swap the
    // file in between; an oversized log starts over rather than growing forever.
    const fd = openSync(target, 'a');
    try {
      if (fstatSync(fd).size > MAX_BYTES) ftruncateSync(fd, 0);
      writeSync(fd, `${new Date().toISOString()} pid=${process.pid} ${message}\n`);
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
