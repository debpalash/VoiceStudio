import { join } from 'node:path';

/** What a user-chosen setup location holds, beyond the Python runtime itself. */
export interface ScopedStorage {
  data: string;
  models: string;
  temp: string;
}

export function scopedStorage(root: string): ScopedStorage {
  return { data: join(root, 'data'), models: join(root, 'models'), temp: join(root, 'tmp') };
}

/**
 * Child-process defaults for a location chosen during first-run setup (#2971).
 * One folder owns the user's data, the model/HF cache and scratch space, so
 * nothing quietly lands on the system drive and fills it. Explicit user
 * environment values are kept; an existing install (no recorded choice) never
 * reaches this and so never has data moved or redirected.
 */
export function scopedStorageEnv(
  root: string,
  env: NodeJS.ProcessEnv = {},
): NodeJS.ProcessEnv {
  const s = scopedStorage(root);
  const out: NodeJS.ProcessEnv = {};
  const set = (key: string, value: string) => {
    if (!env[key]?.trim()) out[key] = value;
  };
  set('OMNIVOICE_DATA_DIR', s.data);
  set('OMNIVOICE_CACHE_DIR', s.models);
  // Temp must move as a set: Windows tools read TEMP/TMP, POSIX tools TMPDIR.
  set('TEMP', s.temp);
  set('TMP', s.temp);
  set('TMPDIR', s.temp);
  return out;
}
