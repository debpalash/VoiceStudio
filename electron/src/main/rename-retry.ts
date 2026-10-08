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

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * `rename`, retrying transient EPERM/EACCES/EBUSY on Windows with capped
 * exponential backoff. The last error is rethrown; other errors and other
 * platforms fail on the first try.
 */
export async function renameWithRetry(
  from: string,
  to: string,
  {
    attempts = FILE_RENAME.attempts,
    capMs = FILE_RENAME.capMs,
    platform = process.platform,
    sleep = wait,
    rename = fsRename,
  }: RenameRetryOptions = {},
): Promise<void> {
  const limit = platform === 'win32' ? (attempts ?? 1) : 1;
  for (let attempt = 1; ; attempt++) {
    try {
      await rename(from, to);
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (attempt >= limit || !code || !TRANSIENT_RENAME_CODES.has(code)) throw error;
      await sleep(Math.min(BACKOFF_MS * 2 ** (attempt - 1), capMs ?? 400));
    }
  }
}
