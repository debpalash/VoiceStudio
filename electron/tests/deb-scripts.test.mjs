import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readlinkSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  LEGACY_COMMAND,
  LEGACY_INSTALL,
  LEGACY_REMOVE,
  debMaintainerScripts,
} from '../packaging/deb-scripts.mjs';

const macros = {
  executable: 'voicestudio',
  sanitizedProductName: 'VoiceStudio',
  productFilename: 'VoiceStudio',
};
// electron-builder's FpmTarget substitution: an unknown macro fails the build.
const render = (text) =>
  text.replace(/\${([a-zA-Z]+)}/g, (_, name) => {
    assert.ok(name in macros, `undefined electron-builder macro \${${name}}`);
    return macros[name];
  });

// Debian maintainer scripts are POSIX shell and these cases replace files with
// symlinks, so the probe covers both halves of what they need: the shell's own
// `ln -s`, which is what the maintainer scripts run, and Node's `symlinkSync`,
// which the upgrade case uses to stage the old alternative. A Git Bash whose
// `ln -s` copies files would pass a Node-only probe and then fail the real
// script, which is why the shell is asked to do the symlinking. Probe the
// capability rather than the platform name, so a Windows host that does provide
// both still runs the cases — the same shape cli-bundle.test.mjs uses for its
// symlink case. Without this the suite is red for every Windows contributor on a
// clean checkout, for reasons unrelated to their change.
const shellReason = (() => {
  if (spawnSync('sh', ['-c', 'exit 0']).status !== 0) return 'needs a POSIX sh on PATH';
  const shellDir = mkdtempSync(join(tmpdir(), 'vs-shell-probe-'));
  try {
    const linked = spawnSync('sh', ['-c', 'ln -s probe-target probe-link && [ -L probe-link ]'], {
      cwd: shellDir,
    });
    if (linked.status !== 0) return "the shell's `ln -s` does not create a symlink here";
    // An MSYS2/Cygwin-style link can satisfy the shell's own `[ -L ]` while a
    // native program cannot read it back, and the upgrade case compares
    // `readlinkSync` output, so check the link the way the test will.
    let target;
    try {
      target = readlinkSync(join(shellDir, 'probe-link'));
    } catch {
      return 'the shell creates a link that Node cannot read back';
    }
    if (target !== 'probe-target') return 'the shell creates a link that Node cannot read back';
  } finally {
    rmSync(shellDir, { recursive: true, force: true });
  }
  const nodeDir = mkdtempSync(join(tmpdir(), 'vs-symlink-probe-'));
  try {
    symlinkSync('probe-target', join(nodeDir, 'probe-link'));
  } catch {
    return 'creating symlinks needs privileges on this host';
  } finally {
    rmSync(nodeDir, { recursive: true, force: true });
  }
  return null;
})();
// Node treats an explicitly null skip option as skipped in its TAP summary.
const needsShell = { skip: shellReason ?? false };

test('deb scripts keep electron-builder behaviour and add the legacy name', () => {
  const scripts = debMaintainerScripts();
  const install = readFileSync(scripts.afterInstall, 'utf8');
  const remove = readFileSync(scripts.afterRemove, 'utf8');
  assert.match(install, /update-alternatives --install '\/usr\/bin\/\${executable}'/);
  assert.match(remove, /update-alternatives --remove '\${executable}'/);
  assert.ok(install.endsWith(LEGACY_INSTALL));
  assert.ok(remove.endsWith(LEGACY_REMOVE));
  render(install);
  render(remove);
});

function sandbox(t) {
  const root = mkdtempSync(join(tmpdir(), 'vs-deb-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'usr/bin'), { recursive: true });
  mkdirSync(join(root, 'opt/VoiceStudio'), { recursive: true });
  writeFileSync(join(root, 'opt/VoiceStudio/voicestudio'), '#!/bin/sh\n', { mode: 0o755 });
  const legacy = join(root, LEGACY_COMMAND);
  const run = (block) => {
    const script = render(block)
      .replaceAll("'/usr/bin/", `'${root}/usr/bin/`)
      .replaceAll("'/opt/", `'${root}/opt/`);
    const result = spawnSync('sh', ['-eu', '-c', script], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  };
  return { root, legacy, target: join(root, 'opt/VoiceStudio/voicestudio'), run };
}

test('install links the old name; remove deletes only that link', needsShell, (t) => {
  const s = sandbox(t);
  s.run(LEGACY_INSTALL);
  assert.equal(readlinkSync(s.legacy), s.target);
  s.run(LEGACY_INSTALL); // idempotent on reinstall
  assert.equal(readlinkSync(s.legacy), s.target);
  s.run(LEGACY_REMOVE);
  assert.equal(existsSync(s.legacy), false);
});

test("an upgrade replaces the old package's dangling alternative", needsShell, (t) => {
  const s = sandbox(t);
  symlinkSync(join(s.root, 'etc/alternatives/voicestudio-electron'), s.legacy);
  s.run(LEGACY_INSTALL);
  assert.equal(readlinkSync(s.legacy), s.target);
});

test('a working command someone else installed is never replaced or removed', needsShell, (t) => {
  const s = sandbox(t);
  writeFileSync(s.legacy, '#!/bin/sh\necho mine\n', { mode: 0o755 });
  s.run(LEGACY_INSTALL);
  s.run(LEGACY_REMOVE);
  assert.equal(readFileSync(s.legacy, 'utf8'), '#!/bin/sh\necho mine\n');
});
