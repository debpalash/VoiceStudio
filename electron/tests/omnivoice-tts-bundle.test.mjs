import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import test from 'node:test';

import {
  OMNIVOICE_TTS_MANIFEST,
  codesignArgs,
  finalizeOmnivoiceTts,
  isTimestampServiceError,
  manifestMismatches,
  runCodesign,
  omnivoiceTtsBinaryName,
  omnivoiceTtsResources,
  omnivoiceTtsServerName,
  omnivoiceTtsSlug,
  rewriteManifest,
  sha256File,
  signMac,
} from '../omnivoice-tts-bundle.mjs';

function stage(files) {
  const dir = mkdtempSync(join(tmpdir(), 'omnivoice-tts-'));
  const lines = [];
  for (const [name, body] of Object.entries(files)) {
    writeFileSync(join(dir, name), body);
    lines.push(`${sha256File(join(dir, name))}  ${name}\n`);
  }
  writeFileSync(join(dir, OMNIVOICE_TTS_MANIFEST), lines.join(''));
  return dir;
}

test('maps each installer target to the slug the backend resolves (#2780)', () => {
  assert.equal(omnivoiceTtsSlug('win32', 'x64'), 'windows-x86_64');
  // Windows on ARM runs the emulated x64 Python runtime.
  assert.equal(omnivoiceTtsSlug('win32', 'arm64'), 'windows-x86_64');
  assert.equal(omnivoiceTtsSlug('darwin', 'arm64'), 'darwin-arm64');
  assert.equal(omnivoiceTtsSlug('linux', 'x64'), 'linux-x86_64');
  // Intel Macs cannot run the local backend; no Linux ARM64 installer exists.
  assert.equal(omnivoiceTtsSlug('darwin', 'x64'), null);
  assert.equal(omnivoiceTtsSlug('linux', 'arm64'), null);
  assert.equal(omnivoiceTtsBinaryName('windows-x86_64'), 'omnivoice-tts-windows-x86_64.exe');
  assert.equal(omnivoiceTtsBinaryName('darwin-arm64'), 'omnivoice-tts-darwin-arm64');
});

test('packages a staged runtime under resources/bin and nothing otherwise', () => {
  assert.deepEqual(omnivoiceTtsResources(join(tmpdir(), 'missing-omnivoice-tts')), []);
  const dir = stage({
    'omnivoice-tts-linux-x86_64': 'rt',
    'tts-server-linux-x86_64': 'srv',
    'libggml.so': 'lib',
  });
  assert.deepEqual(omnivoiceTtsResources(dir), [{ from: dir, to: 'bin', filter: ['**/*'] }]);
});

test('refreshes the manifest after signing changes the shipped bytes', () => {
  const dir = stage({ 'omnivoice-tts-darwin-arm64': 'unsigned', 'libggml.dylib': 'lib' });
  writeFileSync(join(dir, 'omnivoice-tts-darwin-arm64'), 'signed');
  assert.deepEqual(manifestMismatches(dir), ['omnivoice-tts-darwin-arm64']);
  rewriteManifest(dir);
  assert.deepEqual(manifestMismatches(dir), []);
  assert.match(
    readFileSync(join(dir, OMNIVOICE_TTS_MANIFEST), 'utf8'),
    /^[0-9a-f]{64} {2}libggml\.dylib\n/,
  );
});

test('refuses a manifest that lists a file the package lacks', () => {
  const dir = stage({ 'omnivoice-tts-linux-x86_64': 'rt' });
  writeFileSync(
    join(dir, OMNIVOICE_TTS_MANIFEST),
    `${'0'.repeat(64)}  omnivoice-tts-linux-x86_64\n${'1'.repeat(64)}  libggml.so\n`,
  );
  assert.throws(() => rewriteManifest(dir), /missing file: libggml\.so/);
});

test('afterPack restores execute bits and rewrites the packaged manifest', () => {
  const resources = mkdtempSync(join(tmpdir(), 'resources-'));
  const bin = join(resources, 'bin');
  mkdirSync(bin);
  writeFileSync(join(bin, 'omnivoice-tts-linux-x86_64'), 'rt', { mode: 0o644 });
  writeFileSync(
    join(bin, OMNIVOICE_TTS_MANIFEST),
    `${'0'.repeat(64)}  omnivoice-tts-linux-x86_64\n`,
  );
  finalizeOmnivoiceTts({
    electronPlatformName: 'linux',
    appOutDir: resources,
    packager: { getResourcesDir: () => resources },
  });
  if (process.platform !== 'win32') {
    assert(statSync(join(bin, 'omnivoice-tts-linux-x86_64')).mode & 0o111);
  }
  assert.deepEqual(manifestMismatches(bin), []);
});

test('signs the runtime with the same codesign options osx-sign uses', () => {
  const args = codesignArgs(
    {
      identity: 'ABC123',
      keychain: '/tmp/k.keychain',
      optionsForFile: () => ({ hardenedRuntime: true, entitlements: 'inherit.plist' }),
    },
    '/App.app/Contents/Resources/bin/omnivoice-tts-darwin-arm64',
  );
  assert.deepEqual(args, [
    '--sign',
    'ABC123',
    '--force',
    '--keychain',
    '/tmp/k.keychain',
    '--timestamp',
    '--options',
    'runtime',
    '--entitlements',
    'inherit.plist',
    '/App.app/Contents/Resources/bin/omnivoice-tts-darwin-arm64',
  ]);
});

test('default signing skips the pre-signed runtime and keeps other ignores', async () => {
  const app = mkdtempSync(join(tmpdir(), 'VoiceStudio-')) + `${sep}VoiceStudio.app`;
  const bin = join(app, 'Contents', 'Resources', 'bin');
  mkdirSync(bin, { recursive: true });
  // Not Mach-O, so nothing is passed to codesign here; the manifest still refreshes.
  writeFileSync(join(bin, 'omnivoice-tts-darwin-arm64'), 'rt');
  writeFileSync(
    join(bin, OMNIVOICE_TTS_MANIFEST),
    `${'0'.repeat(64)}  omnivoice-tts-darwin-arm64\n`,
  );
  let forwarded;
  await signMac(
    { app, identity: '-', ignore: (file) => file.endsWith('.kext') },
    { doSign: async (opts, custom, identity) => (forwarded = { opts, custom, identity }) },
  );
  assert.deepEqual(manifestMismatches(bin), []);
  assert.equal(forwarded.custom.sign, null);
  assert.equal(forwarded.identity.name, '-');
  assert(forwarded.opts.ignore(join(bin, 'omnivoice-tts-darwin-arm64')));
  assert(forwarded.opts.ignore('/x/driver.kext'));
  assert(!forwarded.opts.ignore(join(app, 'Contents', 'MacOS', 'VoiceStudio')));
});

test('ignores empty CLI placeholders for other platforms (Mac App Store copy of bin/)', () => {
  const dir = stage({
    'omnivoice-tts-darwin-arm64': 'rt',
    'tts-server-darwin-arm64': 'srv',
    'omnivoice-tts-darwin-x86_64': '',
    'omnivoice-tts-linux-aarch64': '',
  });
  assert.equal(omnivoiceTtsResources(dir).length, 1);
});

test('refuses to package a runtime without its resident tts-server (#3654)', () => {
  const missing = stage({ 'omnivoice-tts-linux-x86_64': 'rt', 'libggml.so': 'lib' });
  assert.throws(() => omnivoiceTtsResources(missing), /missing the resident server tts-server-linux-x86_64/);
  // Present on disk but not covered by the manifest is refused too.
  const unlisted = stage({ 'omnivoice-tts-linux-x86_64': 'rt' });
  writeFileSync(join(unlisted, 'tts-server-linux-x86_64'), 'srv');
  assert.throws(() => omnivoiceTtsResources(unlisted), /missing the resident server/);
  const complete = stage({
    'omnivoice-tts-windows-x86_64.exe': 'rt',
    'tts-server-windows-x86_64.exe': 'srv',
  });
  assert.equal(omnivoiceTtsResources(complete).length, 1);
  assert.equal(omnivoiceTtsServerName('windows-x86_64'), 'tts-server-windows-x86_64.exe');
  assert.equal(omnivoiceTtsServerName('darwin-arm64'), 'tts-server-darwin-arm64');
});

const TS_ERR = 'file: A timestamp was expected but was not found.\n';
const fakeRun = (failures) => {
  const calls = [];
  const run = (cmd, args) => {
    calls.push([cmd, args]);
    const failure = failures[calls.length - 1];
    if (failure) throw Object.assign(new Error('Command failed: codesign'), { stderr: failure });
  };
  return { run, calls };
};

test('runCodesign retries a timestamp-service error once then succeeds', async () => {
  const { run, calls } = fakeRun([TS_ERR]);
  const sleeps = [];
  const logs = [];
  await runCodesign(['--sign', 'x', 'f'], { run, sleep: async (ms) => sleeps.push(ms), log: (m) => logs.push(m) });
  assert.equal(calls.length, 2);
  assert.deepEqual(sleeps, [5000]);
  assert.match(logs[0], /attempt 1\/4.*timestamp was expected/);
});

test('runCodesign fails at once on non-timestamp errors', async () => {
  const { run, calls } = fakeRun(['f: no identity found\n']);
  const sleeps = [];
  await assert.rejects(runCodesign(['f'], { run, sleep: async (ms) => sleeps.push(ms), log() {} }), /Command failed/);
  assert.equal(calls.length, 1);
  assert.deepEqual(sleeps, []);
});

test('runCodesign gives up after the max attempts with the stderr message', async () => {
  const { run, calls } = fakeRun([TS_ERR, TS_ERR, TS_ERR, TS_ERR, TS_ERR]);
  const sleeps = [];
  await assert.rejects(
    runCodesign(['f'], { run, sleep: async (ms) => sleeps.push(ms), log() {} }),
    /failed after 4 attempts: .*A timestamp was expected/,
  );
  assert.equal(calls.length, 4);
  assert.deepEqual(sleeps, [5000, 15000, 30000]);
});

test('isTimestampServiceError matches only timestamp-service failures', () => {
  assert(isTimestampServiceError('The timestamp service is not available.'));
  assert(isTimestampServiceError('timestamp: network connection was lost'));
  assert(!isTimestampServiceError('invalid entitlements'));
  assert(!isTimestampServiceError(''));
});
