// Explicit native build check; uses fixture apps, never the user's desktop/backend.
import assert from 'node:assert/strict';
import {
  cpSync,
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  renameSync,
  rmSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { execFile, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { promisify } from 'node:util';
import { Arch } from 'builder-util';
import { packageCli } from '../cli-bundle.mjs';
import { assertBundleSymlinks } from '../packaging/bundle-symlinks.mjs';

const root = mkdtempSync(join(tmpdir(), 'vs-cli-native-'));
const actualElectron = process.argv.includes('--electron');
if (actualElectron && process.platform !== 'linux')
  throw new Error('--electron currently requires Linux');
try {
  const resources =
    process.platform === 'darwin'
      ? join(root, 'VoiceStudio.app/Contents/Resources')
      : join(root, 'resources');
  mkdirSync(resources, { recursive: true });
  const desktop =
    process.platform === 'darwin'
      ? join(root, 'VoiceStudio.app/Contents/MacOS/VoiceStudio')
      : join(root, process.platform === 'win32' ? 'VoiceStudio.exe' : 'voicestudio');
  mkdirSync(join(desktop, '..'), { recursive: true });
  if (actualElectron) {
    const require = createRequire(import.meta.url);
    cpSync(dirname(require('electron')), root, { recursive: true });
    renameSync(join(root, 'electron'), desktop);
    mkdirSync(join(resources, 'app'));
    writeFileSync(
      join(resources, 'app/package.json'),
      JSON.stringify({ name: 'voicestudio-cli-smoke', main: 'index.cjs' }),
    );
    writeFileSync(
      join(resources, 'app/index.cjs'),
      `
const { app, BrowserWindow } = require('electron');
const { writeFileSync } = require('node:fs');
app.setPath('userData', process.env.VS_CLI_SMOKE_PROFILE);
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false });
  await window.loadURL('about:blank');
  writeFileSync(process.env.VS_CLI_SMOKE_RECORD, JSON.stringify({ setting: process.env.VS_CLI_SMOKE_SETTING }));
  window.destroy();
  app.quit();
});
`,
    );
  } else if (process.platform !== 'win32') {
    // Stands in for Electron and records the switches it was started with.
    writeFileSync(desktop, `#!/bin/sh\nprintf '%s\\n' "$@" > '${join(root, 'switches')}'\n`, {
      mode: 0o755,
    });
  }
  await packageCli({
    appOutDir: root,
    electronPlatformName: process.platform,
    arch: Arch[process.arch],
    packager: {
      executableName: 'voicestudio',
      appInfo: {
        productFilename: 'VoiceStudio',
        version: JSON.parse(readFileSync(new URL('../../package.json', import.meta.url))).version,
      },
      getResourcesDir: () => resources,
      signIf: async () => {},
    },
  });
  // packageCli removed its build folder: the shipped copy must not link into it
  // (v0.5.8-beta.4's macOS _internal/Python did, and signing failed).
  assertBundleSymlinks(root);
  const command = join(
    resources,
    'cli',
    'voicestudio' + (process.platform === 'win32' ? '.exe' : ''),
  );
  const run = (args, env = {}) =>
    spawnSync(command, args, {
      encoding: 'utf8',
      cwd: tmpdir(),
      env: {
        ...process.env,
        VOICESTUDIO_CONFIG: join(root, 'config.json'),
        OMNIVOICE_API_KEY: '',
        ...env,
      },
      timeout: 15000,
    });
  for (const args of [
    ['--version'],
    ['voices', '--help'],
    ['models', '--help'],
    ['update', 'info'],
  ]) {
    const result = run(args);
    assert.equal(result.status, 0, result.stderr);
  }
  const typo = run(['udpate', '--json']);
  assert.equal(typo.status, 2);
  assert.equal(JSON.parse(typo.stderr).error.code, 'usage_error');
  const noDesktop = run([], { VOICESTUDIO_DESKTOP_APP: join(root, 'missing') });
  assert.equal(noDesktop.status, 1);
  assert.match(noDesktop.stderr, /Can't find the VoiceStudio app/);
  if (process.platform === 'linux') {
    const launch = actualElectron
      ? run(['--no-sandbox'], {
          VS_CLI_SMOKE_PROFILE: join(root, 'profile'),
          VS_CLI_SMOKE_RECORD: join(root, 'launch.json'),
          HOME: join(root, 'home'),
          XDG_CONFIG_HOME: join(root, 'home/.config'),
          XDG_CACHE_HOME: join(root, 'home/.cache'),
          VS_CLI_SMOKE_SETTING: 'value with spaces',
        })
      : run([]);
    assert.equal(launch.status, 0, launch.stderr);
    if (actualElectron)
      assert.deepEqual(JSON.parse(readFileSync(join(root, 'launch.json'))), {
        setting: 'value with spaces',
      });
    else {
      // AppImage starts this public entry; Playwright's electron.launch (the packaged
      // smoke test) adds these switches, which v0.5.8-beta.4 rejected as a command.
      const switches = [
        '--no-sandbox',
        '--inspect=0',
        '--remote-debugging-port=0',
        `--user-data-dir=${join(root, 'profile')}`,
      ];
      const entry = spawnSync(join(root, 'voicestudio'), switches, {
        encoding: 'utf8',
        env: { ...process.env, VOICESTUDIO_CONFIG: join(root, 'config.json') },
        timeout: 15000,
      });
      assert.equal(entry.status, 0, entry.stderr);
      assert.deepEqual(readFileSync(join(root, 'switches'), 'utf8').trim().split('\n'), switches);
    }
  }
  const requests = [];
  const server = createServer((request, response) => {
    requests.push(request.url);
    response.setHeader('Content-Type', 'application/json');
    response.setHeader('X-OmniVoice-Backend', 'fixture');
    response.end(
      JSON.stringify(request.url === '/health' ? { status: 'ok', version: 'fixture' } : []),
    );
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const result = await promisify(execFile)(
      command,
      ['--url', `http://127.0.0.1:${server.address().port}`, 'projects', 'list', '--json'],
      {
        cwd: tmpdir(),
        timeout: 15000,
        env: {
          ...process.env,
          VOICESTUDIO_CONFIG: join(root, 'config.json'),
          OMNIVOICE_API_KEY: '',
        },
      },
    );
    assert.deepEqual(JSON.parse(result.stdout), []);
    assert.deepEqual(requests, ['/health', '/projects']);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  console.log('PASS: packaged CLI commands, desktop launch and HTTP contracts');
} finally {
  rmSync(root, { recursive: true, force: true });
}
