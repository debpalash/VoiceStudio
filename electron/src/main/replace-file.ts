import { randomUUID } from 'node:crypto';
import { open, realpath, rename, rm, stat } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { FILE_RENAME, renameWithRetry } from './rename-retry';

/** Filesystem calls `replaceFile` makes; injectable so tests can simulate disk failures. */
export interface ReplaceFileSystem {
  open: typeof open;
  rename: typeof rename;
}

const NODE_FILE_SYSTEM: ReplaceFileSystem = { open, rename };

/** Timing and platform knobs for `replaceFile`; injectable so tests need no real waits or OS. */
export interface ReplaceFileOptions {
  platform?: NodeJS.Platform;
  sleep?: (ms: number) => Promise<void>;
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
    await renameWithRetry(temporary, destination, {
      ...FILE_RENAME,
      ...options,
      rename: (from, to) => fs.rename(from, to),
    });
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => {});
    throw error;
  }
}
