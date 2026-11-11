import { join } from 'node:path';

/**
 * The key Settings → Performance & Device saves (backend/core/prefs.py,
 * core/device_caps.py `requested_device_override`). The backend reads it at
 * startup, so a crashed backend can be moved to CPU by writing it here and
 * starting again; the user switches back in the same Settings control.
 */
export const COMPUTE_DEVICE_PREF = 'compute_device';

/**
 * A saved environment override (backend/core/prefs.py `restore_env` puts
 * `env.*` keys into the backend's environment at startup). A stored
 * `OMNIVOICE_DEVICE` there beats `compute_device`, so the CPU choice must clear it.
 */
export const STORED_DEVICE_ENV_PREF = 'env.OMNIVOICE_DEVICE';

/**
 * Store CPU as the compute device in the backend's prefs.json, keeping every
 * other setting. Only called while the backend is down, so nothing else writes
 * the file. A file that is not a JSON object is left alone rather than
 * replaced: it may hold settings the user can still recover.
 */
export async function pinComputeDeviceToCpu(
  dataDir: string,
  platform: NodeJS.Platform = process.platform,
): Promise<void> {
  // Loaded on use: backend.ts imports this module, and its tests mock the fs.
  const { mkdir, readFile } = await import('node:fs/promises');
  const { replaceFile } = await import('./replace-file');
  const path = join(dataDir, 'prefs.json');
  let prefs: Record<string, unknown> = {};
  let text: string | undefined;
  try {
    text = await readFile(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  if (text !== undefined && text.trim()) {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
      throw new Error('prefs.json is not a settings object');
    prefs = parsed as Record<string, unknown>;
  }
  // A stored device override would be restored into the backend's environment
  // and win over the CPU choice, sending the restart back to the GPU that
  // crashed. Dropping it hands the device back to Settings, where the user can
  // choose Auto again after a driver update.
  const { [STORED_DEVICE_ENV_PREF]: _storedOverride, ...kept } = prefs;
  prefs = kept;
  await mkdir(dataDir, { recursive: true });
  // Created 0600 from the start, the mode the backend gives the file: it can hold tokens.
  await replaceFile(
    path,
    JSON.stringify({ ...prefs, [COMPUTE_DEVICE_PREF]: 'cpu' }, null, 2),
    undefined,
    { platform, mode: 0o600 },
  );
}
