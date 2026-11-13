import { readFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

/** Read archived setup paths without moving, rewriting, or deleting user files. */
export function legacyStorageEnv(roots: string[]): NodeJS.ProcessEnv {
  for (const root of roots) {
    try {
      const config = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8'));
      if (!config || typeof config !== 'object' || Array.isArray(config)) continue;
      const absolute = (value: unknown): string | undefined =>
        typeof value === 'string' && value.trim() && isAbsolute(value.trim())
          ? value.trim()
          : undefined;
      const portable = absolute(config.portable_dir ?? config.portableDir);
      const isPortable = (config.install_mode ?? config.installMode) === 'portable';
      const data = isPortable && portable
        ? join(portable, 'data')
        : absolute(config.data_dir ?? config.dataDir);
      const models = isPortable && portable
        ? join(portable, 'data', 'models')
        : absolute(config.models_dir ?? config.modelsDir);
      if (data || models) return {
        ...(data ? { OMNIVOICE_DATA_DIR: data } : {}),
        ...(models ? { OMNIVOICE_CACHE_DIR: models } : {}),
      };
    } catch {
      // Missing or malformed legacy configuration must not block a fresh install.
    }
  }
  return {};
}
