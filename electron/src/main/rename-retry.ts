import { renameSync as fsRenameSync } from 'node:fs';
import { rename as fsRename } from 'node:fs/promises';

/** Windows antivirus and indexers hold just-written files briefly; these codes are transient. */
const TRANSIENT_RENAME_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);
const BACKOFF_MS = 25;

export interface RenameRetryOptions {
  /** Attempts on Windows; every other platform tries once. */
  attempts?: number;
  /** Longest single wait between attempts. */
  capMs?: number;
  platform?: NodeJS.Platform;
  sleep?: (ms: number) => Promise<void>;
  rename?: (from: string, to: string) => Promise<void>;
}

/** A single file: about 1.5 s in total. */
export const FILE_RENAME: RenameRetryOptions = { attempts: 8, capMs: 400 };
/** A freshly copied directory tree takes a scanner longer: about 10 s in total. */
export const DIRECTORY_RENAME: RenameRetryOptions = { attempts: 15, capMs: 1000 };

/** Synchronous callers (journals, settings files) cannot await a backoff. */
export interface RenameSyncRetryOptions extends Omit<RenameRetryOptions, 'sleep' | 'rename'> {
  sleep?: (ms: number) => void;
  rename?: (from: string, to: string) => void;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const waitSync = (ms: number) => {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
};

/** The backoff before retrying `error`, or undefined when it must be rethrown. */
function transientDelay(
  error: unknown,
  attempt: number,
  { attempts = FILE_RENAME.attempts, capMs = FILE_RENAME.capMs, platform = process.platform },
): number | undefined {
  const limit = platform === 'win32' ? (attempts ?? 1) : 1;
  const code = (error as NodeJS.ErrnoException).code;
  if (attempt >= limit || !code || !TRANSIENT_RENAME_CODES.has(code)) return undefined;
  return Math.min(BACKOFF_MS * 2 ** (attempt - 1), capMs ?? 400);
}

/**
 * Run `action`, retrying transient EPERM/EACCES/EBUSY on Windows with capped
 * exponential backoff. Covers every filesystem step a Windows lock can block
 * (rename, copy over, remove), not only rename. The last error is rethrown;
 * other errors and other platforms fail on the first try.
 */
export async function retryTransient<T>(
  action: () => Promise<T>,
  { sleep = wait, ...options }: Omit<RenameRetryOptions, 'rename'> = {},
): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await action();
    } catch (error) {
      const delay = transientDelay(error, attempt, options);
      if (delay === undefined) throw error;
      await sleep(delay);
    }
  }
}

/** {@link retryTransient} for code that must stay synchronous; blocks only while retrying. */
export function retryTransientSync<T>(
  action: () => T,
  { sleep = waitSync, ...options }: Omit<RenameSyncRetryOptions, 'rename'> = {},
): T {
  for (let attempt = 1; ; attempt++) {
    try {
      return action();
    } catch (error) {
      const delay = transientDelay(error, attempt, options);
      if (delay === undefined) throw error;
      sleep(delay);
    }
  }
}

/** `rename` with {@link retryTransient}. */
export async function renameWithRetry(
  from: string,
  to: string,
  { rename = fsRename, ...options }: RenameRetryOptions = {},
): Promise<void> {
  await retryTransient(() => rename(from, to), options);
}

/**
 * `renameSync` with {@link retryTransientSync}. Windows Defender holds a large
 * just-written temp file long enough that most immediate replaces fail EPERM.
 */
export function renameSyncWithRetry(
  from: string,
  to: string,
  { rename = fsRenameSync, ...options }: RenameSyncRetryOptions = {},
): void {
  retryTransientSync(() => rename(from, to), options);
}
