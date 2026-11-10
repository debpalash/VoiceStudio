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

test('install links the old name; remove deletes only that link', (t) => {
  const s = sandbox(t);
  s.run(LEGACY_INSTALL);
  assert.equal(readlinkSync(s.legacy), s.target);
  s.run(LEGACY_INSTALL); // idempotent on reinstall
  assert.equal(readlinkSync(s.legacy), s.target);
  s.run(LEGACY_REMOVE);
  assert.equal(existsSync(s.legacy), false);
});

test("an upgrade replaces the old package's dangling alternative", (t) => {
  const s = sandbox(t);
  symlinkSync(join(s.root, 'etc/alternatives/voicestudio-electron'), s.legacy);
  s.run(LEGACY_INSTALL);
  assert.equal(readlinkSync(s.legacy), s.target);
});

test('a working command someone else installed is never replaced or removed', (t) => {
  const s = sandbox(t);
  writeFileSync(s.legacy, '#!/bin/sh\necho mine\n', { mode: 0o755 });
  s.run(LEGACY_INSTALL);
  s.run(LEGACY_REMOVE);
  assert.equal(readFileSync(s.legacy, 'utf8'), '#!/bin/sh\necho mine\n');
});
