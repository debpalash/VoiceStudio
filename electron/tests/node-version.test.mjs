import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { assertSupportedNode } from '../scripts/node-version.mjs';
import { prepareDev } from '../../scripts/prepare-dev.mjs';

test('old Node versions get actionable guidance', () => {
  for (const version of ['18.20.0', '20.18.0', '20.19.0', '21.7.0', '22.11.0']) {
    assert.throws(() => assertSupportedNode(version), (error) => {
      assert.match(error.message, /Node.js 22.12.0 or newer/);
      assert.ok(error.message.includes(version));
      assert.match(error.message, /https:\/\/nodejs.org/);
      return true;
    });
  }
});

test('supported Node releases pass', () => {
  for (const version of ['22.12.0', '22.23.4', '24.0.0', '25.0.0']) {
    assert.doesNotThrow(() => assertSupportedNode(version));
  }
});

test('predev rejects old Node before installing or setting up the backend', () => {
  assert.throws(() => prepareDev({
    nodeVersion: '20.18.0',
    run() { assert.fail('must not start setup'); },
  }), /Node.js 22.12.0/);
});

test('desktop commands exit cleanly before Electron or port cleanup', () => {
  const entry = new URL('../scripts/dev.mjs', import.meta.url);
  for (const command of ['dev', 'preview']) {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      Object.defineProperty(process.versions, 'node', { value: '20.18.0' });
      process.argv = [process.execPath, ${JSON.stringify(fileURLToPath(entry))}, ${JSON.stringify(command)}];
      await import(${JSON.stringify(entry.href)});
    `], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /VoiceStudio needs Node.js 22.12.0 or newer/);
    assert.match(result.stderr, /20.18.0/);
    assert.doesNotMatch(result.stderr, /ERR_REQUIRE_ESM|Downloading|\n\s+at /);
    assert.equal(result.stdout, '');
  }
});
