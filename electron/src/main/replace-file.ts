import { randomUUID } from 'node:crypto';
import { lstat, open, readlink, realpath, rename, rm, stat } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, sep } from 'node:path';
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

const MAX_LINK_HOPS = 40;

/**
 * Follow symlinks so a link keeps pointing at the replaced file, including
 * links to a file that does not exist yet.
 *
 * The directory is resolved by the filesystem first (`realpath`), so `..`
 * after a symlinked directory means the link target's parent, exactly as the
 * OS would open the path. Lexically collapsing `alias/../x` could pick a
 * different directory and overwrite an unrelated file. A missing intermediate
 * directory is an error rather than a silent fallback.
 */
async function resolveDestination(path: string, hops = 0): Promise<string> {
  if (hops > MAX_LINK_HOPS) {
    throw Object.assign(new Error(`Too many symbolic links: ${path}`), { code: 'ELOOP' });
  }
  // A trailing separator requires a directory; basename would erase that
  // requirement and turn a dangling directory link into a successful file save.
  if (path.endsWith(sep) || (process.platform === 'win32' && path.endsWith('/'))) {
    throw Object.assign(new Error(`Destination requires a directory: ${path}`), { code: 'EISDIR' });
  }
  const directory = await realpath(dirname(path));
  const candidate = join(directory, basename(path));
  try {
    return await realpath(candidate);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  // The final component is missing or a dangling link. A dangling link still
  // belongs to its symlink: replacing it would detach the export location
  // from the user's intended destination.
  const entry = await lstat(candidate).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
    return null;
  });
  if (!entry?.isSymbolicLink()) return candidate;
  const link = await readlink(candidate);
  // Keep any `..` in the link text: the next hop resolves directories first.
  return resolveDestination(isAbsolute(link) ? link : `${directory}${sep}${link}`, hops + 1);
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
