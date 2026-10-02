import { randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, open, realpath, rename, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';

/** Keep a selected existing export intact until all replacement bytes are written. */
export async function writeExportAtomically(path: string, data: Uint8Array): Promise<void> {
  // Authorize the actual opened file before inspecting metadata. A pathname
  // check followed by open can observe different files after a rename.
  let missing: NodeJS.ErrnoException | undefined;
  const existing = await open(path, constants.O_WRONLY).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
    missing = error;
    return null;
  });
  let destination = path;
  let mode = 0o666;
  if (existing) {
    try {
      mode = (await existing.stat()).mode & 0o777;
      // Resolve a working symlink without replacing the link itself.
      destination = await realpath(path);
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
    await rename(temporary, destination);
  } finally {
    await rm(temporary, { force: true });
  }
}
