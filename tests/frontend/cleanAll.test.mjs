import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { appDataPaths, cleanAll, planCleanup } from '../../scripts/clean-all.mjs';

test('plain make preserves the release default and never selects cleanup', (t) => {
  const ctx = fixture(t);
  const makefile = fileURLToPath(new URL('../../Makefile', import.meta.url));
  const result = spawnSync('make', ['--dry-run', '--file', makefile], {
    cwd: ctx.root, encoding: 'utf8', env: { ...process.env, MAKEFLAGS: '' },
  });
  if (result.error?.code === 'ENOENT') {
    t.skip('Make is optional; Windows users can run the Bun cleanup command directly.');
    return;
  }
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /uv run python scripts\/release\.py/);
  assert.doesNotMatch(result.stdout, /clean-all\.mjs/);
});

test('Make previews truthy and unfamiliar DRY_RUN values without deleting', (t) => {
  const ctx = fixture(t);
  const makefile = fileURLToPath(new URL('../../Makefile', import.meta.url));
  for (const [value, preview] of [
    ['1', true], ['true', true], ['yes', true], ['TRUE', true], ['typo', true],
    ['', false], ['0', false], ['false', false], ['no', false],
  ]) {
    const result = spawnSync('make', ['--dry-run', '--file', makefile, 'clean:all', `DRY_RUN=${value}`], {
      cwd: ctx.root, encoding: 'utf8', env: { ...process.env, MAKEFLAGS: '' },
    });
    if (result.error?.code === 'ENOENT') {
      t.skip('Make is optional; Windows users can run the Bun cleanup command directly.');
      return;
    }
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.includes('--dry-run'), preview, `DRY_RUN=${value}`);
  }
});

function fixture(t) {
  const base = mkdtempSync(join(tmpdir(), 'voicestudio-clean-test-'));
  t.after(() => rmSync(base, { recursive: true, force: true }));
  const root = join(base, 'repo');
  const home = join(base, 'home');
  mkdirSync(root);
  mkdirSync(home);
  execFileSync('git', ['init', '-q', root]);
  const put = (path) => { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, 'keep'); };
  return { root, home, put, env: {}, platform: 'linux', log: () => {} };
}

test('cleans nested dependencies, arbitrary venv names, builds, caches and app data', (t) => {
  const ctx = fixture(t);
  const artifacts = [
    '.venv/lib/file', 'electron/node_modules/lib/file', 'node_modules/lib/file',
    'backend/custom-python/pyvenv.cfg', 'backend/__pycache__/file.pyc',
    'mobile/android/app/build/file', 'mobile/android/.gradle/file',
    'mobile/ios/VoiceStudioKit/.build/file', 'mobile/ios/.build-device/file',
    'native/desktop-bridge/target/debug/file', 'electron/out/main.js',
    'electron/release/installer', '.cache/iphone/file', 'omnivoice_data/voices/file',
    'omnivoice.log', '.coverage', 'electron/tsconfig.tsbuildinfo',
  ].map((path) => join(ctx.root, path));
  const appFiles = appDataPaths(ctx).map((path) => join(path, 'state'));
  [...artifacts, ...appFiles].forEach(ctx.put);
  const kept = ['source.py', '.env', 'bun.lock', 'tests/fixtures/omnivoice_data/voice.wav',
    'assets/build/source.js'].map((path) => join(ctx.root, path));
  kept.forEach(ctx.put);
  execFileSync('git', ['add', 'assets/build/source.js'], { cwd: ctx.root });
  const shared = join(ctx.home, '.cache/huggingface/hub/model');
  const otherApp = join(ctx.home, '.config/OtherApp/state');
  [shared, otherApp].forEach(ctx.put);
  cleanAll(ctx);
  for (const path of [...artifacts, ...appFiles]) assert.equal(existsSync(path), false, path);
  for (const path of [...kept, shared, otherApp]) assert.equal(existsSync(path), true, path);
  assert.deepEqual(planCleanup(ctx), []);
});

test('dry-run prints targets without deleting anything', (t) => {
  const ctx = fixture(t);
  const path = join(ctx.root, '.venv/pyvenv.cfg');
  ctx.put(path);
  const messages = [];
  cleanAll({ ...ctx, dryRun: true, log: (line) => messages.push(line) });
  assert.ok(existsSync(path));
  assert.ok(messages.some((line) => line.includes(join(ctx.root, '.venv'))));
});

test('unlinks dependency symlinks without following them or other directory links', (t) => {
  const ctx = fixture(t);
  const outside = join(ctx.home, 'shared/node_modules/keep');
  ctx.put(outside);
  symlinkSync(dirname(outside), join(ctx.root, 'node_modules'), 'junction');
  symlinkSync(join(ctx.home, 'shared'), join(ctx.root, 'linked-source'), 'junction');
  cleanAll(ctx);
  assert.ok(existsSync(outside));
  assert.equal(existsSync(join(ctx.root, 'node_modules')), false);
});

test('refuses symlinked app-data parents before deleting project files', (t) => {
  const ctx = fixture(t);
  mkdirSync(join(ctx.home, 'elsewhere'));
  symlinkSync(join(ctx.home, 'elsewhere'), join(ctx.home, '.config'), 'junction');
  ctx.put(join(ctx.home, 'elsewhere/VoiceStudio/state'));
  ctx.put(join(ctx.root, '.venv/pyvenv.cfg'));
  assert.throws(() => cleanAll(ctx), /symlink/i);
  assert.ok(existsSync(join(ctx.root, '.venv/pyvenv.cfg')));
});

test('preserves nested repositories even when named like build output', (t) => {
  const ctx = fixture(t);
  const nested = join(ctx.root, 'build');
  mkdirSync(nested);
  execFileSync('git', ['init', '-q', nested]);
  const path = join(nested, 'node_modules/file');
  ctx.put(path);
  cleanAll(ctx);
  assert.ok(existsSync(path));
});

test('cannot clean without the tracked-file inventory', (t) => {
  const ctx = fixture(t);
  rmSync(join(ctx.root, '.git'), { recursive: true });
  const path = join(ctx.root, '.venv/pyvenv.cfg');
  ctx.put(path);
  assert.throws(() => cleanAll(ctx));
  assert.ok(existsSync(path));
});

test('preserves a repository nested inside a generated directory', (t) => {
  const ctx = fixture(t);
  const nested = join(ctx.root, 'build/vendor-source');
  mkdirSync(nested, { recursive: true });
  execFileSync('git', ['init', '-q', nested]);
  const source = join(nested, 'node_modules/keep');
  const generated = join(ctx.root, 'build/.venv/pyvenv.cfg');
  [source, generated].forEach(ctx.put);
  cleanAll(ctx);
  assert.ok(existsSync(source));
  assert.equal(existsSync(generated), false);
});

test('platform defaults include Electron, backend and settings, with XDG and Windows overrides', () => {
  const home = join(tmpdir(), 'clean-home');
  const mac = appDataPaths({ home, platform: 'darwin', env: {} });
  assert.ok(mac.includes(join(home, 'Library/Application Support/VoiceStudio')));
  assert.ok(mac.includes(join(home, 'Library/Application Support/OmniVoice')));
  const win = appDataPaths({ home, platform: 'win32', env: { APPDATA: join(home, 'roam'), LOCALAPPDATA: join(home, 'local') } });
  assert.ok(win.includes(join(home, 'roam/VoiceStudio')));
  assert.ok(win.includes(join(home, 'roam/OmniVoice')));
  const linux = appDataPaths({ home, platform: 'linux', env: { XDG_CONFIG_HOME: join(home, 'config') } });
  assert.ok(linux.includes(join(home, 'config/VoiceStudio')));
  assert.ok(linux.includes(join(home, '.omnivoice')));
  for (const paths of [mac, win, linux]) assert.ok(paths.includes(join(home, '.config/omnivoice')));
});

for (const platform of ['darwin', 'win32', 'linux']) {
  test(`removes ${platform} app data while preserving other app profiles and shared models`, (t) => {
    const ctx = { ...fixture(t), platform };
    const files = appDataPaths(ctx).map((path) => path.endsWith('.plist') ? path : join(path, 'state'));
    files.forEach(ctx.put);
    const others = [join(ctx.home, 'Library/Application Support/OtherApp/state'), join(ctx.home, '.config/voicestudio-pro/state'), join(ctx.home, '.cache/huggingface/model'), join(ctx.home, 'AppData/Local/OmniVoice/hf_cache/model')];
    others.forEach(ctx.put);
    cleanAll(ctx);
    for (const path of files) assert.equal(existsSync(path), false, path);
    for (const path of others) assert.ok(existsSync(path), path);
  });
}
