import { constants } from 'node:fs';
import { lstat, open, stat, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { renameWithRetry } from './rename-retry';

const FILE = '.voicestudio-data-identity.json';
const LIMIT = 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
interface Identity {
  version: 1;
  id: string;
  dev: string;
  ino: string;
}
async function readIdentity(path: string): Promise<Identity | null> {
  let file;
  try {
    file = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0),
    );
    const info = await file.stat({ bigint: true });
    const entry = await lstat(path, { bigint: true });
    if (
      !info.isFile() ||
      !entry.isFile() ||
      info.dev !== entry.dev ||
      info.ino !== entry.ino ||
      info.size > BigInt(LIMIT)
    )
      throw new Error('Unusable identity');
    const buffer = Buffer.alloc(LIMIT + 1);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > LIMIT) throw new Error('Oversized identity');
    const value = JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')) as Identity;
    if (
      value.version !== 1 ||
      typeof value.id !== 'string' ||
      !UUID.test(value.id) ||
      typeof value.dev !== 'string' ||
      typeof value.ino !== 'string' ||
      !/^[1-9]\d*$/.test(value.dev) ||
      !/^[1-9]\d*$/.test(value.ino)
    )
      throw new Error('Invalid identity');
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      // A dangling link is present, not an absent metadata file.
      try {
        await lstat(path);
      } catch (missing) {
        if ((missing as NodeJS.ErrnoException).code === 'ENOENT') return null;
      }
    }
    throw error;
  } finally {
    await file?.close();
  }
}

/** Opaque identity for a caller-proven local data root; never creates the root.
 * The directory inode keeps aliases/renames stable and makes copied metadata
 * acquire a different identity. Failure is unknown, never a path-derived ID.
 */
export async function localDataIdentity(directory: string): Promise<string | undefined> {
  const path = join(directory, FILE);
  const lockPath = `${path}.lock`;
  let lock;
  let temporary: string | undefined;
  try {
    const root = await stat(directory, { bigint: true });
    if (!root.isDirectory() || root.dev <= 0n || root.ino <= 0n) return;
    const dev = String(root.dev);
    const ino = String(root.ino);
    const sameRoot = async () => {
      const current = await stat(directory, { bigint: true });
      return current.isDirectory() && current.dev === root.dev && current.ino === root.ino;
    };
    const matches = (record: Identity | null) => record?.dev === dev && record.ino === ino;
    const previous = await readIdentity(path);
    if (matches(previous)) return (await sameRoot()) ? previous!.id : undefined;
    // One creator/rotation at a time. Other instances may read the completed
    // atomic publication; a stale or inaccessible lock simply leaves it unknown.
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        lock = await open(lockPath, 'wx', 0o600);
        break;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        await new Promise((resolve) => setTimeout(resolve, 10));
        const published = await readIdentity(path);
        if (matches(published)) return (await sameRoot()) ? published!.id : undefined;
      }
    }
    if (!lock) return;
    const published = await readIdentity(path);
    if (matches(published)) return (await sameRoot()) ? published!.id : undefined;
    const value: Identity = { version: 1, id: randomUUID(), dev, ino };
    temporary = `${path}.${randomUUID()}.tmp`;
    const output = await open(temporary, 'wx', 0o600);
    try {
      await output.writeFile(JSON.stringify(value), 'utf8');
      await output.sync();
    } finally {
      await output.close();
    }
    if (!(await sameRoot())) return;
    await renameWithRetry(temporary, path);
    temporary = undefined;
    return (await sameRoot()) ? value.id : undefined;
  } catch {
    return;
  } finally {
    if (temporary) await unlink(temporary).catch(() => {});
    if (lock) {
      await lock.close().catch(() => {});
      await unlink(lockPath).catch(() => {});
    }
  }
}
