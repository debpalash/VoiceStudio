import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(
  new URL('../../electron/src/renderer/src/features/radio/radio-studio-page.tsx', import.meta.url),
  'utf8',
);
const shell = readFileSync(
  new URL('../../electron/src/renderer/src/components/app-shell/app-shell.tsx', import.meta.url),
  'utf8',
);
const routes = readFileSync(
  new URL('../../electron/src/renderer/src/routes/index.ts', import.meta.url),
  'utf8',
);

test('NAS radio studio stays the full-screen root workspace', () => {
  assert.match(routes, /features\/radio\/radio-studio-page/);
  assert.match(routes, /component: RadioStudioPage/);
  assert.doesNotMatch(routes, /features\/home\/home-page/);
  assert.match(shell, /const nasStudio = pathname === '\/'/);
  assert.match(shell, /\{nasStudio \? \(/);
});

test('NAS radio studio keeps the approved horizontal workflow contract', () => {
  assert.match(page, /h-full min-h-0 overflow-hidden/);
  assert.doesNotMatch(page, /overflow-y-auto/);
  assert.match(page, /grid-cols-\[330px_minmax\(720px,1\.55fr\)_minmax\(520px,\.95fr\)\]/);
  assert.match(page, /title="Voice"/);
  assert.match(page, /title="Script & Style"/);
  assert.match(page, /title="Takes"/);
});

test('NAS radio generation uses one fixed profile and three broadcast takes', () => {
  assert.match(page, /const TAKE_IDS: TakeId\[\] = \['A', 'B', 'C'\]/);
  assert.match(page, /for \(let index = 0; index < TAKE_IDS\.length; index \+= 1\)/);
  assert.match(page, /profileId: selectedProfile\.id/);
  assert.match(page, /effectPreset: 'broadcast'/);
  assert.match(page, /wavBits: 24/);
  assert.match(page, /audioUrl\(selectedTake\.result\.audioPath\)/);
});
