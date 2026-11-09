import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { RENDERER_HELPER, classifySmoke, listSmokes } from './smoke-inventory.mjs';

const chromiumSource = "import { chromium } from 'playwright';";
const electronSource = "import { _electron as electron } from 'playwright';";

test('mocked renderer smokes are classified by content, not name', () => {
  assert.equal(classifySmoke('convert-live-smoke.mjs', chromiumSource), 'renderer');
});

test('smokes that launch through the shared renderer helper are renderer smokes', () => {
  const source = "import { launchSmokeBrowser } from './smoke-api-mock.mjs';";
  assert.equal(classifySmoke('theme-picker-smoke.mjs', source), 'renderer');
});

test('-real-smoke files need a real backend', () => {
  assert.equal(classifySmoke('gallery-trim-real-smoke.mjs', chromiumSource), 'real-backend');
});

test('_electron smokes are native', () => {
  assert.equal(classifySmoke('native-capture-smoke.mjs', electronSource), 'electron');
});

test('an unlisted file with neither import is unknown', () => {
  assert.equal(classifySmoke('mystery-smoke.mjs', 'console.log(1)'), 'unknown');
});

test('explicitly listed non-browser smokes are other', () => {
  assert.equal(classifySmoke('native-output-smoke.ts', 'export {}'), 'other');
});

const dir = dirname(fileURLToPath(import.meta.url));

test('every smoke in electron/tests is classified', () => {
  const unknown = listSmokes(dir)
    .filter((smoke) => smoke.kind === 'unknown')
    .map((smoke) => smoke.file);
  assert.deepEqual(unknown, [], 'Classify these in OTHER_SMOKES or give them a known runner');
});

test('renderer smokes honour PLAYWRIGHT_CHANNEL instead of hardcoding Edge', () => {
  // CI has Playwright's chromium, not Edge; a hardcoded channel makes the smoke unrunnable there.
  const hardcoded = listSmokes(dir)
    .filter((smoke) => smoke.kind === 'renderer')
    .filter(({ file }) => {
      const source = readFileSync(join(dir, file), 'utf8');
      // Every Edge default must sit behind the env override; /usr/bin/chromium is a host path.
      return (
        /(?<!PLAYWRIGHT_CHANNEL \|\| |fallbackChannel: )['"]msedge['"]/.test(source) ||
        source.includes('/usr/bin/chromium')
      );
    })
    .map((smoke) => smoke.file);
  assert.deepEqual(hardcoded, []);
});

test('the shared renderer helper lets PLAYWRIGHT_CHANNEL override a fallback channel', () => {
  const source = readFileSync(join(dir, RENDERER_HELPER), 'utf8');
  assert.match(source, /process\.env\.PLAYWRIGHT_CHANNEL \|\| fallbackChannel/);
});

test('renderer smokes do not depend on a live backend', () => {
  // A fixed backend port means the smoke talks to a real server; CI has none.
  const live = listSmokes(dir)
    .filter((smoke) => smoke.kind === 'renderer')
    .filter(({ file }) =>
      /(127\.0\.0\.1|localhost):3900|VOICESTUDIO_BACKEND_URL/.test(
        readFileSync(join(dir, file), 'utf8'),
      ),
    )
    .map((smoke) => smoke.file);
  assert.deepEqual(live, []);
});

test('renderer smoke count is deliberate', () => {
  // Update on purpose when a renderer smoke is added or removed.
  const renderer = listSmokes(dir).filter((smoke) => smoke.kind === 'renderer');
  assert.equal(renderer.length, 40);
});
