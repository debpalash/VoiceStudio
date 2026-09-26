// WebUI feature-coverage guard — the frontend counterpart to the backend's
// route-inventory test. Verifies, by static analysis (no fragile full-page
// render), that every app feature is actually wired up:
//
//   1. every AppMode (minus documented legacy aliases) has a render branch in
//      App.jsx — a feature can't be in the nav state yet unreachable;
//   2. every page component App.jsx lazy-imports resolves to a real file;
//   3. every major feature has its i18n namespace in en.json — so a shipped
//      feature can't be missing its user-facing copy (localization rule).
//
// Self-maintaining: the mode list is read from the store and the branches from
// App.jsx, so adding a mode + page + branch keeps this green automatically.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const _dir = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(_dir, '..'); // electron/src/shared
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

// Legacy/alias modes intentionally kept in the union but routed elsewhere
// (see uiSlice.ts comments): clone/design → studio; generate/batch unused.
const LEGACY_MODES = new Set(['clone', 'design', 'generate', 'batch']);

function appModes() {
  const ts = read('store/uiSlice.ts');
  const block = ts.slice(
    ts.indexOf('export type AppMode'),
    ts.indexOf(';', ts.indexOf('export type AppMode')),
  );
  return [...block.matchAll(/\|\s*'([a-z]+)'/g)].map((m) => m[1]);
}

describe('webUI feature coverage', () => {
  const app = read('App.jsx');

  it('every non-legacy AppMode has a render branch in App.jsx', () => {
    const modes = appModes();
    expect(modes.length).toBeGreaterThan(10); // sanity: union parsed
    const missing = modes.filter((m) => !LEGACY_MODES.has(m) && !app.includes(`mode === '${m}'`));
    expect(missing, `AppModes with no render branch in App.jsx: ${missing.join(', ')}`).toEqual([]);
  });

  it('every page App.jsx lazy-imports exists on disk', () => {
    const imports = [...app.matchAll(/import\(['"]\.\/(pages|components)\/([\w/]+)['"]\)/g)];
    expect(imports.length).toBeGreaterThan(8);
    const missing = imports
      .map(([, dir, name]) => `${dir}/${name}`)
      .filter((rel) => {
        return !['.jsx', '.tsx', '.js', '.ts'].some((ext) =>
          fs.existsSync(path.join(SRC, rel + ext)),
        );
      });
    expect(missing, `lazy() imports with no file: ${missing.join(', ')}`).toEqual([]);
  });

  it('every major feature has an i18n namespace in en.json', () => {
    const en = JSON.parse(read('i18n/locales/en.json'));
    // One namespace per shipped feature surface.
    const required = [
      'launchpad',
      'dub',
      'dub_workflow',
      'clone',
      'stories',
      'audiobook',
      'gallery',
      'projects',
      'transcriptions',
      'catalogue',
      'settings',
      'engines',
      'donate',
      'enterprise',
      'contact',
      'tools',
      'batch',
      'voice',
      'history',
      'glossary',
      'network',
      'header',
      'nav',
      'common',
    ];
    const missing = required.filter((k) => !(k in en));
    expect(missing, `feature i18n namespaces missing from en.json: ${missing.join(', ')}`).toEqual(
      [],
    );
  });
});
