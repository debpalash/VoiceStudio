import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { cliBuildArguments, wirePackagedCli } from '../cli-bundle.mjs';

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
    assert.equal(cli, join(resources, 'cli', cliName));
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
