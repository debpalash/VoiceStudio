import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Smokes that use neither Playwright `chromium` nor `_electron`. Each needs a reason, so a new
 * smoke is either run by CI or consciously parked here.
 */
export const OTHER_SMOKES = {
  'renderer-smoke.vite.config.mjs': 'Vite config for the smoke server, not a test',
  'runtime-install-smoke.mjs': 'drives the runtime installer directly with node',
  'native-output-smoke.ts': 'native helper check run with the Rust helper built',
  'native-shortcut-smoke.ts': 'native helper check run with the Rust helper built',
  'native-watch-folder-smoke.ts': 'native helper check run with the Rust helper built',
  'native-wayland-output-smoke.ts': 'native helper check that needs a Wayland session',
};

const SMOKE_FILE = /smoke(\..*)?\.(mjs|ts)$/;

/** Shared harness that launches Chromium and mocks /api for renderer smokes. */
export const RENDERER_HELPER = 'smoke-api-mock.mjs';

/**
 * What a smoke needs in order to run:
 *  - renderer: mocked UI in Chromium against the Vite smoke server (runs offline)
 *  - real-backend: needs a live backend and models
 *  - electron: drives the real app (native or packaged)
 *  - other: parked in OTHER_SMOKES
 *  - unknown: not classified yet; the inventory test fails on these
 */
export function classifySmoke(file, source) {
  if (file.includes('-real-smoke.')) return 'real-backend';
  if (source.includes('_electron')) return 'electron';
  if (/\bchromium\b/.test(source) || source.includes(`'./${RENDERER_HELPER}'`)) return 'renderer';
  if (Object.hasOwn(OTHER_SMOKES, file)) return 'other';
  return 'unknown';
}

export function listSmokes(dir) {
  return readdirSync(dir)
    .filter((file) => SMOKE_FILE.test(file) && !file.endsWith('.test.mjs'))
    .sort()
    .map((file) => ({ file, kind: classifySmoke(file, readFileSync(join(dir, file), 'utf8')) }));
}
