import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import test from 'node:test';
import { configureSnapLauncher, verifySnapLauncher } from '../snap-launcher.mjs';

const command = '#!/bin/bash -e\nexec "$SNAP/desktop-init.sh" "$SNAP/desktop-common.sh" "$SNAP/desktop-gnome-specific.sh" "$SNAP/voicestudio" "$@"';

test('Snap launches without a sandbox permission probe and preserves desktop initialization and arguments', () => {
  const dir = mkdtempSync(join(tmpdir(), 'snap-launcher-'));
  try {
    for (const script of ['desktop-init.sh', 'desktop-common.sh', 'desktop-gnome-specific.sh']) {
      writeFileSync(join(dir, script), '#!/bin/sh\nexec "$@"\n', { mode: 0o755 });
    }
    writeFileSync(join(dir, 'voicestudio'), '#!/bin/sh\nprintf "%s\\n" "$@"\n', { mode: 0o755 });
    const result = spawnSync('/bin/bash', ['-c', configureSnapLauncher(command), 'launcher', 'a b', '--example'], {
      env: { SNAP: dir, PATH: dir }, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '--no-sandbox\na b\n--example\n');
    assert.equal(result.stderr, '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('accepts the launcher electron-builder generates for allow-sandbox: false', () => {
  const builderRequire = createRequire(createRequire(import.meta.url).resolve('electron-builder'));
  const { buildCommandShContent } = builderRequire('app-builder-lib/out/targets/snap/coreLegacy.js');
  const generated = buildCommandShContent({
    isTemplate: true,
    executableName: 'voicestudio',
    extraAppArgs: ['--no-sandbox'],
  });
  assert.doesNotThrow(() => verifySnapLauncher(generated));
  assert.equal(configureSnapLauncher(generated), generated);
});

test('launcher configuration is idempotent', () => {
  const configured = configureSnapLauncher(command);
  assert.equal(configureSnapLauncher(configured), configured);
  assert.doesNotThrow(() => verifySnapLauncher(configured));
});

test('rejects unexpected launch templates instead of silently shipping a broken Snap', () => {
  assert.throws(() => configureSnapLauncher('exit 0'), /launcher/);
  assert.throws(() => configureSnapLauncher(command.replace('voicestudio', 'other')), /launcher/);
  assert.throws(() => verifySnapLauncher(command), /launcher/);
  const configured = configureSnapLauncher(command);
  assert.throws(() => verifySnapLauncher(`snapctl is-connected browser-support\n${configured}`), /launcher/);
});

test('comments cannot satisfy or bypass validation of the executed command', () => {
  const comment = '# "$SNAP/voicestudio" --no-sandbox "$@"\n';
  const [shebang, ...lines] = command.split('\n');
  const commented = [shebang, comment.trimEnd(), ...lines].join('\n');
  assert.throws(() => verifySnapLauncher(commented), /launcher/);
  const configured = configureSnapLauncher(commented);
  assert.match(configured, /^exec .*"\$SNAP\/voicestudio" --no-sandbox "\$@"$/m);
  assert.equal(configureSnapLauncher(configured), configured);
  assert.throws(() => verifySnapLauncher(`#!/bin/sh\n${comment}`), /launcher/);
});

test('rejects missing, repeated or reordered desktop initialization scripts', () => {
  const init = '"$SNAP/desktop-init.sh" ';
  const common = '"$SNAP/desktop-common.sh" ';
  const invalid = [
    command.replace(init, ''),
    command.replace(common, ''),
    command.replace(init, init + init),
    command.replace(init + common, common + init),
  ];
  for (const launcher of invalid) {
    assert.throws(() => configureSnapLauncher(launcher), /launcher/);
    assert.throws(() => verifySnapLauncher(launcher), /launcher/);
  }
});
