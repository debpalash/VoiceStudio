import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import {
  refreshSignedRuntime,
  runtimeSignTargets,
  verifyMediaInventory,
  verifyRuntimeInventory,
} from '../mas-runtime-bundle.mjs';

test('signing targets exclude binary weights, static archives and symlink aliases', () => {
  const root = mkdtempSync(join(tmpdir(), 'mas-signing-'));
  try {
    mkdirSync(join(root, 'lib'));
    writeFileSync(join(root, 'lib', 'native.so'), Buffer.from('cffaedfe01020304', 'hex'));
    symlinkSync('native.so', join(root, 'lib', 'native-link.so'));
    writeFileSync(join(root, 'lib', 'weights.pth'), Buffer.from('80010203040506', 'hex'));
    writeFileSync(join(root, 'lib', 'archive.a'), '!<arch>\n');
    writeFileSync(join(root, 'lib', 'LICENSE'), 'notice');
    assert.deepEqual(runtimeSignTargets(root), [join(root, 'lib', 'native.so')]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('the recorded inventory matches the signed bytes and retains licence review status', () => {
  const root = mkdtempSync(join(tmpdir(), 'mas-signing-'));
  try {
    const executable = join(root, 'python');
    writeFileSync(executable, 'signed executable with a larger signature');
    writeFileSync(
      join(root, 'manifest.json'),
      JSON.stringify({
        redistributionApproved: false,
        files: { python: { bytes: 3, sha256: 'before signing' } },
      }),
    );
    refreshSignedRuntime(root, [executable]);
    const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
    assert.equal(manifest.files.python.bytes, readFileSync(executable).length);
    assert.equal(
      manifest.files.python.sha256,
      createHash('sha256').update(readFileSync(executable)).digest('hex'),
    );
    assert.equal(manifest.redistributionApproved, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('rejects changed bytes, a different lock or architecture and escaping inventory paths', () => {
  const root = mkdtempSync(join(tmpdir(), 'mas-provenance-'));
  const hash = (value) => createHash('sha256').update(value).digest('hex');
  try {
    const lock = join(root, 'uv.lock');
    writeFileSync(lock, 'locked dependencies');
    writeFileSync(join(root, 'python'), 'runtime');
    const manifest = {
      schemaVersion: 1,
      arch: 'arm64',
      lockSha256: hash('locked dependencies'),
      redistributionApproved: false,
      files: {
        python: { bytes: 7, sha256: hash('runtime') },
        'uv.lock': { bytes: 19, sha256: hash('locked dependencies') },
      },
    };
    const save = () => writeFileSync(join(root, 'manifest.json'), JSON.stringify(manifest));
    save();
    assert.equal(verifyRuntimeInventory(root, lock, 'arm64').redistributionApproved, false);
    writeFileSync(join(root, 'unrecorded.py'), 'unexpected code');
    assert.throws(() => verifyRuntimeInventory(root, lock, 'arm64'), /Unrecorded runtime file/);
    rmSync(join(root, 'unrecorded.py'));
    assert.throws(() => verifyRuntimeInventory(root, lock, 'x86_64'), /provenance mismatch/);
    writeFileSync(join(root, 'python'), 'changed');
    assert.throws(() => verifyRuntimeInventory(root, lock, 'arm64'), /bytes changed/);
    writeFileSync(join(root, 'python'), 'runtime');
    writeFileSync(lock, 'different lock');
    assert.throws(() => verifyRuntimeInventory(root, lock, 'arm64'), /provenance mismatch/);
    writeFileSync(lock, 'locked dependencies');
    manifest.files = { '../outside': { bytes: 0, sha256: hash('') } };
    save();
    assert.throws(() => verifyRuntimeInventory(root, lock, 'arm64'), /escapes/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('media provenance follows signed bytes while preserving acquisition hashes and pending clearance', () => {
  const root = mkdtempSync(join(tmpdir(), 'mas-media-'));
  const hash = (value) => createHash('sha256').update(value).digest('hex');
  try {
    const files = ['ffmpeg', 'ffprobe'].map((file) => {
      writeFileSync(join(root, file), file);
      return { file, sha256: hash(file), sourceSha256: hash(`original ${file}`) };
    });
    writeFileSync(
      join(root, 'provenance.json'),
      JSON.stringify({ files, archive: { sha256: 'a'.repeat(64) }, redistributionApproved: false }),
    );
    assert.equal(verifyMediaInventory(root).redistributionApproved, false);
    writeFileSync(join(root, 'ffmpeg'), 'modified');
    assert.throws(() => verifyMediaInventory(root), /media bytes changed/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
