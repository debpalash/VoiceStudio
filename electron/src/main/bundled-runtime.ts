import { existsSync, readFileSync } from 'node:fs';
import { delimiter, isAbsolute, join, relative, resolve } from 'node:path';

/** A store runtime lives inside the signed app; managed/user runtimes are never a fallback. */
export function bundledPython(resourcesPath: string): string {
  const root = join(resourcesPath, 'python-runtime');
  const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8')) as {
    schemaVersion?: unknown;
    python?: unknown;
  };
  if (manifest.schemaVersion !== 1 || typeof manifest.python !== 'string') {
    throw new Error('Invalid bundled Python manifest');
  }
  const executable = resolve(root, manifest.python);
  const child = relative(root, executable);
  if (!child || child.startsWith('..') || isAbsolute(child) || !existsSync(executable)) {
    throw new Error('Invalid bundled Python executable');
  }
  return executable;
}

/** Mutable model, data and cache files stay in the app's own sandbox container. */
export function bundledRuntimeEnv(resourcesPath: string, userData: string): NodeJS.ProcessEnv {
  return {
    VOICESTUDIO_BUNDLED_CODE_ONLY: '1',
    VOICESTUDIO_BUNDLED_RESOURCES: resourcesPath,
    PYTHONNOUSERSITE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    OMNIVOICE_DATA_DIR: join(userData, 'data'),
    OMNIVOICE_CACHE_DIR: join(userData, 'models'),
    HF_HOME: join(userData, 'models'),
    HF_HUB_CACHE: join(userData, 'models'),
    XDG_CACHE_HOME: join(userData, 'cache'),
    XDG_DATA_HOME: join(userData, 'data'),
    XDG_CONFIG_HOME: join(userData, 'config'),
    MPLCONFIGDIR: join(userData, 'cache', 'matplotlib'),
    NUMBA_CACHE_DIR: join(userData, 'cache', 'numba'),
    FFMPEG_PATH: join(resourcesPath, 'media-tools', 'ffmpeg'),
    FFPROBE_PATH: join(resourcesPath, 'media-tools', 'ffprobe'),
    OMNIVOICE_FFPROBE_PATH: join(resourcesPath, 'media-tools', 'ffprobe'),
    PATH: [
      join(resourcesPath, 'media-tools'),
      join(resourcesPath, 'python-runtime', 'bin'),
      '/usr/bin',
      '/bin',
      '/usr/sbin',
      '/sbin',
    ].join(delimiter),
  };
}
