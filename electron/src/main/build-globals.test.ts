import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// Lives in src/main: the renderer's tsconfig has no Node types, and this reads files.
const root = resolve(__dirname, '../..');
const read = (path: string) => readFileSync(resolve(root, path), 'utf8');

// Every config that builds or serves the renderer must define each build-time global the
// renderer declares; a missing one is a ReferenceError that only the route using it hits.
const configs = [
  'electron.vite.config.ts',
  'vite.config.ts',
  'vite.web.config.ts',
  'tests/renderer-smoke.vite.config.mjs',
];
const globals = [...read('src/renderer/env.d.ts').matchAll(/declare const (__\w+__):/g)].map(
  (match) => match[1],
);

describe('renderer build-time globals', () => {
  it('declares at least the known globals', () => {
    expect(globals).toEqual(expect.arrayContaining(['__APP_VERSION__', '__BUILD_COMMIT__']));
  });

  for (const config of configs) {
    it(`${config} defines every declared global`, () => {
      const source = read(config);
      const missing = globals.filter((name) => !source.includes(name));
      expect(missing, `${config} is missing ${missing.join(', ')}`).toEqual([]);
    });
  }
});

describe('main-process app version', () => {
  // app.getVersion() reads electron/package.json, a placeholder outside packaged
  // builds; the root package.json version reaches main as __APP_VERSION__.
  it('never reads the placeholder package version', () => {
    const offenders = readdirSync(resolve(root, 'src/main'))
      .filter((file) => file.endsWith('.ts') && !file.endsWith('.test.ts'))
      .filter((file) => /\bapp\.getVersion\(\)/.test(read(`src/main/${file}`)));
    expect(offenders).toEqual([]);
  });
});
