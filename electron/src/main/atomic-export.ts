import { randomUUID } from 'node:crypto';
import { lstat, open, realpath, rename, rm, stat } from 'node:fs/promises';
import { dirname, join } from 'node:path';

/** Keep a selected existing export intact until all replacement bytes are written. */
export async function writeExportAtomically(path: string, data: Uint8Array): Promise<void> {
  const existing = await lstat(path).catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  // Preserve the normal save-through-symlink behavior. A dangling link fails
  // without replacing the link itself or inventing a different destination.
  const destination = existing?.isSymbolicLink() ? await realpath(path) : path;
  const mode = existing ? (await stat(destination)).mode & 0o777 : 0o666;
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
    await rename(temporary, destination);
  } finally {
    await rm(temporary, { force: true });
  }
}
