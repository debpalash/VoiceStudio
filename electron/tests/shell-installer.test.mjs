import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const installer = process.env.INSTALLER_UNDER_TEST || resolve('../scripts/install.sh');
const payload = 'fixture Electron AppImage';
const digest = createHash('sha256').update(payload).digest('hex');

for (const mac of [false, true]) {
  test(`uninstall preserves data and a recoverable app (${mac ? 'macOS' : 'Linux'})`, (t) => {
    const f = fixture(t, { mac });
    const app = mac ? join(f.home, 'Applications/VoiceStudio.app') : join(f.home, '.local/bin/VoiceStudio');
    if (mac) {
      mkdirSync(join(app, 'Contents/Resources'), { recursive: true });
      writeFileSync(join(app, 'Contents/Resources/app.asar'), 'app');
    } else {
      mkdirSync(join(f.home, '.local/bin'), { recursive: true });
      writeFileSync(app, 'app');
    }
    const result = f.run(['--uninstall']);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(app), false);
    const recovery = result.stdout.match(/Recoverable copy: (.+)/)[1];
    if (mac) {
      assert.ok(recovery.startsWith(join(f.home, '.Trash') + '/'));
      assert.match(readFileSync(join(f.root, 'registration'), 'utf8'), /-u .*VoiceStudio\.app/);
    }
    assert.equal(existsSync(join(recovery, mac ? 'VoiceStudio.app' : 'VoiceStudio')), true);
    assert.equal(readFileSync(f.settings, 'utf8'), 'preserve me');
    assert.equal(existsSync(join(f.root, 'requests')), false);
    assert.equal(f.run(['--uninstall']).status, 0);
    assert.notEqual(f.run(['--uninstall', '--main']).status, 0);
    assert.notEqual(f.run(['--version', '1.2.3', '--uninstall']).status, 0);
  });
}

function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'vs-installer-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const bin = join(root, 'bin');
  const home = join(root, 'home');
  mkdirSync(bin);
  mkdirSync(home);
  mkdirSync(join(home, 'Applications'));
  mkdirSync(join(home, '.local/bin'), { recursive: true });
  const shim = (name, body) =>
    writeFileSync(join(bin, name), '#!/bin/sh\nset -eu\n' + body, { mode: 0o755 });
  shim(
    'uname',
    `case "$1" in -s) echo ${options.mac ? 'Darwin' : 'Linux'};; -m) echo ${options.mac ? 'arm64' : 'x86_64'};; esac\n`,
  );
  shim(
    'hdiutil',
    `
if [ "$1" = attach ]; then
 while [ "$1" != -mountpoint ]; do shift; done
 shift; mkdir -p "$1/VoiceStudio.app/Contents"
 printf fixture > "$1/VoiceStudio.app/Contents/Info.plist"
fi
`,
  );
  shim('ditto', 'cp -R "$1" "$2"\n');
  shim(
    'curl',
    `
printf '%s\\n' "$*" >> "$TEST_ROOT/requests"
out=; url=
while [ "$#" -gt 0 ]; do
 case "$1" in -o) shift; out=$1;; https://*) url=$1;; esac
 shift
done
case "$url" in
 */latest) printf 'https://github.com/debpalash/VoiceStudio/releases/tag/v1.2.3';;
 */SHA256SUMS.txt)
   version=$(printf '%s' "$url" | sed 's|.*/download/v||; s|/.*||')
   printf '%s  VoiceStudio-Electron-%s-${options.mac ? 'mac-arm64.dmg' : 'linux-x64.AppImage'}\\n' "$TEST_DIGEST" "$version" > "$out";;
 */VoiceStudio-Electron-*.AppImage|*/VoiceStudio-Electron-*.dmg)
   [ "$TEST_MISSING" = 0 ] || exit 22
   printf '${payload}' > "$out";;
 *) exit 22;;
esac
`,
  );
  shim(
    'git',
    `
printf 'git %s\\n' "$*" >> "$TEST_ROOT/commands"
if [ "$1" = clone ]; then
 for arg in "$@"; do dest=$arg; done
 mkdir -p "$dest/frontend" "$dest/electron/release"
 mkdir -p "$dest/electron/tests" "$dest/electron/scripts"
 touch "$dest/electron/tests/packaging-contract.mjs" "$dest/electron/tests/update-package-contract.mjs" "$dest/electron/scripts/embed-appimage-update.mjs"
 printf '{"version":"1.2.4"}' > "$dest/frontend/package.json"
else echo fixture-commit; fi
`,
  );
  shim(
    'bun',
    `
printf 'bun %s\\n' "$*" >> "$TEST_ROOT/commands"
[ "$TEST_BUILD_FAIL" = 0 ] || exit 1
if [ "$1 $2" = 'run electron-builder' ]; then printf '${payload}' > release/VoiceStudio-Electron-1.2.4-linux-x64.AppImage; fi
`,
  );
  shim('cargo', 'exit 0\n');
  shim('readelf', 'exit 0\n');
  shim('zsyncmake', 'exit 0\n');
  shim('pgrep', options.linuxRunning
    ? `case "$*" in *voicestudio-electron*) exit 0;; *) exit 1;; esac\n`
    : options.running ? 'exit 0\n' : 'exit 1\n');
  shim('lsregister', 'printf "%s\\n" "$*" >> "$TEST_ROOT/registration"\n' + (options.registrationFails ? 'exit 1\n' : ''));
  const settings = join(home, 'settings');
  writeFileSync(settings, 'preserve me');
  return {
    home,
    root,
    settings,
    run(args = []) {
      return spawnSync('sh', [installer, ...args], {
        encoding: 'utf8',
        cwd: root,
        env: {
          ...process.env,
          PATH: `${bin}:${process.env.PATH}`,
          HOME: home,
          TMPDIR: root,
          TEST_ROOT: root,
          TEST_DIGEST: options.badChecksum ? '0'.repeat(64) : digest,
          VOICESTUDIO_INSTALL_DIR: options.installDir ?? (options.mac
            ? join(home, 'Applications')
            : join(home, '.local/bin')),
          TEST_MISSING: options.missing ? '1' : '0',
          TEST_BUILD_FAIL: options.buildFails ? '1' : '0',
        },
      });
    },
  };
}

for (const [name, args, version] of [
  ['latest', [], '1.2.3'],
  ['older release', ['--version', 'v1.0.0'], '1.0.0'],
]) {
  test(`installs ${name} Electron package and preserves data`, (t) => {
    const f = fixture(t);
    const r = f.run(args);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(readFileSync(join(f.home, '.local/bin/VoiceStudio'), 'utf8'), payload);
    assert.equal(readFileSync(f.settings, 'utf8'), 'preserve me');
    const requests = readFileSync(join(f.root, 'requests'), 'utf8');
    assert.match(requests, new RegExp(`download/v${version}/VoiceStudio-Electron-`));
    assert.doesNotMatch(requests, /latest\.json/);
    if (args.length) assert.doesNotMatch(requests, /releases\/latest/);
  });
}
for (const options of [{ badChecksum: true }, { missing: true }]) {
  test(`refuses unverified/missing release ${JSON.stringify(options)}`, (t) => {
    const f = fixture(t, options);
    const r = f.run();
    assert.notEqual(r.status, 0);
    assert.equal(existsSync(join(f.home, '.local/bin/VoiceStudio')), false);
  });
}
for (const option of ['--main', '--source']) {
  test(`${option} builds a fresh main checkout and installs`, (t) => {
    const f = fixture(t);
    const r = f.run([option]);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(readFileSync(join(f.home, '.local/bin/VoiceStudio'), 'utf8'), payload);
    const commands = readFileSync(join(f.root, 'commands'), 'utf8');
    assert.match(commands, /git clone --depth 1 --branch main --single-branch/);
    assert.match(commands, /bun install --frozen-lockfile/);
    assert.match(commands, /bun run electron-builder --config electron-builder.config.mjs --publish never --linux --x64/);
    assert.doesNotMatch(commands, /git pull|git reset|frontend.*build/);
  });
}
test('build failure does not replace an existing installation', (t) => {
  const f = fixture(t, { buildFails: true });
  mkdirSync(join(f.home, '.local/bin'), { recursive: true });
  writeFileSync(join(f.home, '.local/bin/VoiceStudio'), 'old app');
  assert.notEqual(f.run(['--main']).status, 0);
  assert.equal(readFileSync(join(f.home, '.local/bin/VoiceStudio'), 'utf8'), 'old app');
});
test('rejects invalid arguments before downloading', (t) => {
  const f = fixture(t);
  for (const args of [['--version'], ['--version', '../bad'], ['--main', '--version', '1.2.3']]) {
    assert.notEqual(f.run(args).status, 0);
  }
  assert.equal(existsSync(join(f.root, 'requests')), false);
});
test('macOS installs the verified Electron bundle and replaces only the app', (t) => {
  const f = fixture(t, { mac: true });
  const app = join(f.home, 'Applications/VoiceStudio.app');
  mkdirSync(app);
  writeFileSync(join(app, 'old-file'), 'old');
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(join(app, 'Contents/Info.plist'), 'utf8'), 'fixture');
  assert.equal(existsSync(join(app, 'old-file')), false);
  assert.equal(readFileSync(f.settings, 'utf8'), 'preserve me');
  assert.match(readFileSync(join(f.root, 'registration'), 'utf8'), /-f .*VoiceStudio\.app/);
});

test('refuses replacement and uninstall while VoiceStudio is running', (t) => {
  const f = fixture(t, { mac: true, running: true });
  for (const args of [[], ['--uninstall']]) {
    const result = f.run(args);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Quit VoiceStudio/);
  }
  assert.equal(existsSync(join(f.root, 'requests')), false);
});

test('macOS still removes the launchable copy if stale registration refresh fails', (t) => {
  const f = fixture(t, { mac: true, registrationFails: true });
  const app = join(f.home, 'Applications/VoiceStudio.app');
  mkdirSync(join(app, 'Contents/Resources'), { recursive: true });
  writeFileSync(join(app, 'Contents/Resources/app.asar'), 'app');
  const result = f.run(['--uninstall']);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(existsSync(app), false);
  assert.match(result.stderr, /Warning/);
  assert.ok(result.stdout.includes(join(f.home, '.Trash')));
});

test('Linux packaged process blocks installation and uninstall before downloads', (t) => {
  const f = fixture(t, { linuxRunning: true });
  for (const args of [[], ['--uninstall']]) {
    const r = f.run(args);
    assert.notEqual(r.status, 0);
    assert.match(r.stderr, /Quit VoiceStudio/);
  }
  assert.equal(existsSync(join(f.root, 'requests')), false);
});
for (const mac of [true, false]) {
  for (const installDir of ['relative', '/nonexistent-voicestudio-test-directory']) {
    test(`rejects invalid custom install directory ${installDir} mac=${mac}`, (t) => {
      const f = fixture(t, { mac, installDir });
      assert.notEqual(f.run().status, 0);
      assert.equal(existsSync(join(f.root, 'requests')), false);
    });
  }
}
