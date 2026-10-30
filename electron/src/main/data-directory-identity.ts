import { randomUUID } from 'node:crypto';
import { type BigIntStats } from 'node:fs';
import { lstat, open, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

export function fileFingerprint(info: BigIntStats, digest?: string) {
  return {
    dev: info.dev,
    ino: info.ino,
    birthtimeNs: info.birthtimeNs,
    ctimeNs: info.ctimeNs,
    size: info.size,
    fileMode: info.mode,
    digest,
  };
}

export function sameFileGeneration(
  info: BigIntStats,
  entry: ReturnType<typeof fileFingerprint>,
): boolean {
  return (
    info.dev === entry.dev &&
    info.ino === entry.ino &&
    info.birthtimeNs === entry.birthtimeNs &&
    info.ctimeNs === entry.ctimeNs &&
    info.size === entry.size &&
    info.mode === entry.fileMode
  );
}

export class RollbackIncompleteError extends Error {
  constructor(cause: unknown) {
    super('rollback_incomplete', { cause });
  }
}

export class TargetChangedError extends Error {
  constructor() {
    super('target_changed');
  }
}

/** Verify ambiguous creation times without trusting an unchanged clock tick. */
export async function stableDirectoryIdentity(
  target: string,
  selected: BigIntStats,
): Promise<BigIntStats> {
  if (selected.birthtimeNs === 0n) throw new Error('identity_unavailable');
  if (selected.birthtimeNs !== selected.ctimeNs) return selected;
  // Fast marker writes can share a coarse filesystem timestamp with mkdir.
  // Retry briefly until ctime advances; never accept an unverifiable identity.
  for (let attempt = 0; attempt < 5; attempt++) {
    if (attempt) await delay(10);
    const marker = join(target, `.voicestudio-identity-${randomUUID()}`);
    const handle = await open(marker, 'wx');
    try {
      const owned = await handle.stat({ bigint: true });
      const present = await lstat(marker, { bigint: true });
      if (
        !present.isFile() ||
        present.size !== 0n ||
        !sameFileGeneration(present, fileFingerprint(owned))
      )
        throw new TargetChangedError();
      await rm(marker);
    } catch (error) {
      throw new RollbackIncompleteError(error);
    } finally {
      await handle.close();
    }
    const after = await lstat(target, { bigint: true });
    if (
      !after.isDirectory() ||
      after.isSymbolicLink() ||
      after.dev !== selected.dev ||
      after.ino !== selected.ino
    )
      throw new TargetChangedError();
    if (after.birthtimeNs !== selected.birthtimeNs) throw new Error('identity_unavailable');
    if (after.ctimeNs !== selected.ctimeNs) return after;
  }
  throw new Error('identity_unavailable');
}
