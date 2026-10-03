import { randomUUID } from 'node:crypto';
import { constants, type BigIntStats } from 'node:fs';
import { lstat, open, realpath, rename, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

/** Keep a selected existing export intact until all replacement bytes are written. */
export async function writeExportAtomically(path: string, data: Uint8Array): Promise<void> {
  // Bind symlink resolution before authorizing the destination. Resolving the
  // selected path again after open could follow a newly substituted symlink.
  const destination = await realpath(path).catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return path;
    throw error;
  });
  let missing: NodeJS.ErrnoException | undefined;
  const existing = await open(destination, constants.O_WRONLY).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
    missing = error;
    return null;
  });
  let authorized: BigIntStats | undefined;
  let mode = 0o666;
  if (existing) {
    try {
      authorized = await existing.stat({ bigint: true });
      mode = Number(authorized.mode & 0o777n);
    } finally {
      await existing.close();
    }
  } else {
    const present = await lstat(path).catch((error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    // A dangling symlink (or a newly appeared path) must not be overwritten
    // after the failed authorization open.
    if (present) throw missing;
  }
  const temporary = join(dirname(destination), `.voicestudio-${randomUUID()}.tmp`);
  // Exclusive creation owns this temporary file, even if a later write fails.
  const file = await open(temporary, 'wx', existing ? 0o600 : 0o666);
  try {
    try {
      await file.writeFile(data);
      // open() applies umask; chmod restores the existing mode exactly.
      if (existing) await file.chmod(mode);
      await file.sync();
    } finally {
      await file.close();
    }
    for (let attempt = 0; ; attempt += 1) {
      const current = await lstat(destination, { bigint: true }).catch((error: NodeJS.ErrnoException) => {
        if (error.code === 'ENOENT') return null;
        throw error;
      });
      if (authorized) {
        // Recheck after every wait: another exporter may replace the destination
        // while a transient Windows sharing violation prevents this rename.
        if (!current || current.isSymbolicLink() || current.dev !== authorized.dev || current.ino !== authorized.ino) {
          throw Object.assign(new Error('ESTALE'), { code: 'ESTALE' });
        }
      } else if (current) {
        throw Object.assign(new Error('EEXIST'), { code: 'EEXIST' });
      }
      // The identity check is not an atomic compare-and-swap with rename. A
      // concurrently modified hostile directory needs native OS protection.
      try {
        await rename(temporary, destination);
        break;
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (attempt >= 3 || (code !== 'EPERM' && code !== 'EBUSY')) throw error;
        await delay(50 * (attempt + 1));
      }
    }
  } finally {
    await rm(temporary, { force: true });
  }
}
