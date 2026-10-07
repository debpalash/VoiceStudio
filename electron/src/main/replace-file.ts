import { randomUUID } from 'node:crypto';
import { open, realpath, rename, rm, stat } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';

/** Filesystem calls `replaceFile` makes; injectable so tests can simulate disk failures. */
export interface ReplaceFileSystem {
  open: typeof open;
  rename: typeof rename;
}

const NODE_FILE_SYSTEM: ReplaceFileSystem = { open, rename };

/** Windows antivirus and indexers hold a just-closed file briefly; these codes are transient. */
const TRANSIENT_RENAME_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);
const RENAME_ATTEMPTS = 8;
const RENAME_BACKOFF_MS = 25;
const RENAME_BACKOFF_CAP_MS = 400;

/** Timing and platform knobs for `replaceFile`; injectable so tests need no real waits or OS. */
export interface ReplaceFileOptions {
  platform?: NodeJS.Platform;
  sleep?: (ms: number) => Promise<void>;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Rename over the destination. On Windows a scanner can hold either file for a
 * moment, so transient EPERM/EACCES/EBUSY is retried with capped exponential
 * backoff (~1.5 s total) and the last error is rethrown. Elsewhere it is one try.
 */
async function renameWithRetry(
  fs: ReplaceFileSystem,
  from: string,
  to: string,
  { platform = process.platform, sleep = wait }: ReplaceFileOptions,
): Promise<void> {
  const attempts = platform === 'win32' ? RENAME_ATTEMPTS : 1;
  for (let attempt = 1; ; attempt++) {
    try {
      await fs.rename(from, to);
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (attempt >= attempts || !code || !TRANSIENT_RENAME_CODES.has(code)) throw error;
      await sleep(Math.min(RENAME_BACKOFF_MS * 2 ** (attempt - 1), RENAME_BACKOFF_CAP_MS));
    }
  }
}

/** Follow an existing symlink so the link keeps pointing at the replaced file. */
async function resolveDestination(path: string): Promise<string> {
  try {
    return await realpath(path);
  } catch {
    return path;
  }
}

/**
 * Write `data` to a user-chosen destination without ever leaving it partial.
 *
 * The bytes go to a hidden sibling first and only replace the destination by
 * rename once fully written and flushed, so a failed write (full disk, removed
 * drive, permission change) leaves an existing export exactly as it was. The
 * sibling lives in the same directory so the rename never crosses volumes.
 * POSIX permission bits of a replaced file are kept; Windows files take the
 * directory's inherited ACL, as a freshly saved file would.
 */
export async function replaceFile(
  path: string,
  data: string | Uint8Array,
  fs: ReplaceFileSystem = NODE_FILE_SYSTEM,
  options: ReplaceFileOptions = {},
): Promise<void> {
  const destination = await resolveDestination(path);
  const existing = await stat(destination).catch(() => null);
  const temporary = join(dirname(destination), `.${basename(destination)}.${randomUUID()}.partial`);
  const handle = await fs.open(temporary, 'wx');
  try {
    try {
      await handle.writeFile(data);
      if (existing?.isFile() && process.platform !== 'win32') {
        await handle.chmod(existing.mode & 0o7777);
      }
      await handle.sync();
    } finally {
      await handle.close();
    }
    await renameWithRetry(fs, temporary, destination, options);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => {});
    throw error;
  }
}
