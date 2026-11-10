import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readlinkSync,
  existsSync,
  rmSync,
  symlinkSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { cliBuildArguments, wirePackagedCli } from '../cli-bundle.mjs';
import { bundleSymlinkProblems } from '../packaging/bundle-symlinks.mjs';

test('builds the CLI package with an embedded interpreter and package data', () => {
  const args = cliBuildArguments('/fixture');
  assert(args.includes('--onedir'));
  assert(args.includes('--console'));
  assert(args.includes('--copy-metadata'));
  assert(args.includes('voicestudio-cli'));
  assert(args.includes('voicestudio_cli'));
  assert(cliBuildArguments('/fixture', 'win32').includes('cpython-3.11-windows-x86_64-none'));
});

for (const platform of ['linux', 'darwin', 'win32']) {
  test(`packages a private desktop target and a public CLI (${platform})`, (t) => {
    const root = mkdtempSync(join(tmpdir(), 'vs-cli-layout-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const appOutDir = join(root, 'installed app');
    const resources =
      platform === 'darwin'
        ? join(appOutDir, 'VoiceStudio.app/Contents/Resources')
        : join(appOutDir, 'resources');
    mkdirSync(resources, { recursive: true });
    const bundle = join(root, 'bundle');
    mkdirSync(join(bundle, '_internal'), { recursive: true });
    writeFileSync(join(bundle, '_internal/contracts.json'), '{}');
    // PyInstaller's macOS layout: _internal/Python -> Python.framework/Versions/3.11/Python.
    const framework = join(bundle, '_internal/Python.framework/Versions/3.11');
    mkdirSync(framework, { recursive: true });
    writeFileSync(join(framework, 'Python'), 'interpreter');
    const linked = process.platform !== 'win32';
    if (linked) symlinkSync('Python.framework/Versions/3.11/Python', join(bundle, '_internal/Python'));
    const cliName = platform === 'win32' ? 'voicestudio.exe' : 'voicestudio';
    writeFileSync(join(bundle, cliName), '#!/bin/sh\nprintf "CLI:%s\\n" "$*"\n', { mode: 0o755 });
    const desktop =
      platform === 'darwin'
        ? join(appOutDir, 'VoiceStudio.app/Contents/MacOS/VoiceStudio')
        : join(appOutDir, platform === 'win32' ? 'VoiceStudio.exe' : 'voicestudio');
    mkdirSync(join(desktop, '..'), { recursive: true });
    writeFileSync(desktop, 'original desktop');
    const context = {
      appOutDir,
      electronPlatformName: platform,
      packager: {
        executableName: 'voicestudio',
        appInfo: { productFilename: 'VoiceStudio', version: '9.8.7' },
        getResourcesDir: () => resources,
      },
    };
    const cli = wirePackagedCli(context, bundle);
    // packageCli deletes the build folder; the shipped copy must not point into it.
    rmSync(bundle, { recursive: true, force: true });
    assert.equal(cli, join(resources, 'cli', cliName));
    if (linked) {
      const link = join(resources, 'cli/_internal/Python');
      assert.equal(readlinkSync(link), 'Python.framework/Versions/3.11/Python');
      assert.equal(readFileSync(link, 'utf8'), 'interpreter');
      assert.deepEqual(bundleSymlinkProblems(appOutDir), []);
    }
    assert(existsSync(join(resources, 'cli/_internal/contracts.json')));
    const manifest = JSON.parse(readFileSync(join(resources, 'cli/desktop.json')));
    if (platform === 'linux') {
      assert.equal(manifest.executable.replaceAll('\\', '/'), '../../voicestudio-desktop');
      assert.equal(
        readFileSync(join(appOutDir, 'voicestudio-desktop'), 'utf8'),
        'original desktop',
      );
      if (process.platform === 'linux') {
        const result = spawnSync(desktop, ['voices', '--help'], { encoding: 'utf8' });
        assert.equal(result.status, 0, result.stderr);
        assert.equal(result.stdout, 'CLI:voices --help\n');
      }
    } else {
      assert.equal(readFileSync(desktop, 'utf8'), 'original desktop');
      assert.equal(
        manifest.executable.replaceAll('\\', '/'),
        platform === 'darwin' ? '../../MacOS/VoiceStudio' : '../../VoiceStudio.exe',
      );
    }
  });
}

test('reports broken, absolute and escaping symlinks in a bundle', (t) => {
  if (process.platform === 'win32') return t.skip('symlinks need privileges on Windows');
  const root = mkdtempSync(join(tmpdir(), 'vs-bundle-links-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const bundle = join(root, 'bundle');
  mkdirSync(join(bundle, 'lib'), { recursive: true });
  writeFileSync(join(bundle, 'lib/real'), 'x');
  writeFileSync(join(root, 'outside'), 'x');
  symlinkSync('real', join(bundle, 'lib/good'));
  symlinkSync('lib', join(bundle, 'dir-link'));
  assert.deepEqual(bundleSymlinkProblems(bundle), []);
  symlinkSync('missing', join(bundle, 'lib/broken'));
  symlinkSync(join(bundle, 'lib/real'), join(bundle, 'lib/absolute'));
  symlinkSync('../../outside', join(bundle, 'lib/escape'));
  assert.deepEqual(bundleSymlinkProblems(bundle).sort(), [
    `${join('lib', 'absolute')} -> ${join(bundle, 'lib/real')} (absolute)`,
    `${join('lib', 'broken')} -> missing (broken)`,
    `${join('lib', 'escape')} -> ../../outside (outside the bundle)`,
  ]);
});
