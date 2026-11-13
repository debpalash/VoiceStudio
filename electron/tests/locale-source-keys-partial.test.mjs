// The Docker web build copies only electron/ before `build:web` runs locale:check.
// locale-source-keys.mjs must pass there (skipping the repo-wide unused-key scan),
// and must still fail in CI when a scanned tree goes missing.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const electron = fileURLToPath(new URL('../', import.meta.url));
const root = mkdtempSync(join(tmpdir(), 'locale-partial-'));
try {
  cpSync(join(electron, 'tests/locale-source-keys.mjs'), join(root, 'electron/tests/locale-source-keys.mjs'));
  cpSync(join(electron, 'src'), join(root, 'electron/src'), { recursive: true });
  const script = join(root, 'electron/tests/locale-source-keys.mjs');
  const { CI: _ci, ...env } = process.env;

  const partial = spawnSync(process.execPath, [script], { env, encoding: 'utf8' });
  assert.equal(partial.status, 0, partial.stderr);
  assert.match(partial.stdout, /Locale unused keys: skipped \(partial checkout/);

  const ciFalse = spawnSync(process.execPath, [script], { env: { ...env, CI: 'false' }, encoding: 'utf8' });
  assert.equal(ciFalse.status, 0, ciFalse.stderr);

  const ci = spawnSync(process.execPath, [script], { env: { ...env, CI: 'true' }, encoding: 'utf8' });
  assert.notEqual(ci.status, 0);
  assert.match(ci.stderr, /Unused-key scan cannot find/);
  console.log('Locale source keys: partial checkout skips the unused-key scan; CI fails');
} finally {
  rmSync(root, { recursive: true, force: true });
}
