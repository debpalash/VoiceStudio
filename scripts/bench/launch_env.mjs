// Environment for a benchmarked Electron launch (#3924). Kept free of
// dependencies so tests/test_bench_harness.py can check it with plain node.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Same isolation as bench.py's isolated_env: none of this machine's app or proxy
 * settings may redirect the data folder or the network. HOME (and its Windows
 * and XDG equivalents) points inside the throwaway profile, because a packaged
 * app's startup links the `voicestudio` terminal command into ~/.local/bin and
 * edits shell profiles; a benchmark must never touch the real home.
 */
export function launchEnv(baseEnv, { port, profile }) {
  const inherited = Object.fromEntries(
    Object.entries(baseEnv).filter(
      ([key]) =>
        key !== 'ELECTRON_RUN_AS_NODE' &&
        !key.toUpperCase().endsWith('_PROXY') &&
        !key.startsWith('OMNIVOICE_') &&
        !key.startsWith('VOICESTUDIO_'),
    ),
  );
  const home = join(profile, 'home');
  for (const dir of ['.config', '.local/share', '.local/state', '.cache']) {
    mkdirSync(join(home, dir), { recursive: true });
  }
  return {
    ...inherited,
    HOME: home,
    USERPROFILE: home,
    XDG_CONFIG_HOME: join(home, '.config'),
    XDG_DATA_HOME: join(home, '.local/share'),
    XDG_STATE_HOME: join(home, '.local/state'),
    XDG_CACHE_HOME: join(home, '.cache'),
    ELECTRON_RENDERER_URL: '',
    OMNIVOICE_PORT: String(port),
    VOICESTUDIO_SKIP_BACKEND: '1',
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICESTUDIO_DISABLE_UPDATER: '1',
    VOICESTUDIO_ELECTRON_PROXY_PORT: '0',
    OMNIVOICE_ANALYTICS_DISABLED: '1',
  };
}
