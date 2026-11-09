import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createArtifactManifest } from '../../scripts/create_store_artifact_manifest.mjs';

test('creates deterministic provenance for regular files only', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'voicestudio-store-manifest-'));
  writeFileSync(join(dir, 'VoiceStudio-0.5.7-win-x64.exe'), 'installer');
  writeFileSync(join(dir, 'VoiceStudio-MicrosoftStore-0.5.7-win-x64.exe'), 'store installer');
  writeFileSync(join(dir, 'VoiceStudio-MacAppStore-0.5.7-mac-arm64.pkg'), 'store package');
  writeFileSync(join(dir, 'electron-stable-win32-x64.yml'), 'version: 0.5.7\n');
  writeFileSync(join(dir, 'ignored.txt'), 'not a release artifact');
  const output = join(dir, 'store-artifact-manifest-win32-x64.json');

  await createArtifactManifest({
    releaseDir: dir,
    output,
    distribution: 'direct',
    platform: 'win32',
    arch: 'x64',
    version: '0.5.7',
    commit: 'a'.repeat(40),
    generatedAt: '2026-10-13T00:00:00.000Z',
  });

  const manifest = JSON.parse(readFileSync(output, 'utf8'));
  assert.deepEqual(
    manifest.artifacts.map((item) => item.file),
    ['VoiceStudio-0.5.7-win-x64.exe', 'electron-stable-win32-x64.yml'],
  );
  assert.equal(manifest.distribution.target, 'direct');
  assert.equal(manifest.distribution.applicationUpdates, 'application');
  assert.equal(manifest.source.commit, 'a'.repeat(40));
  assert.deepEqual(manifest.signingVerification, { status: 'not-run' });
  assert.match(manifest.artifacts[0].sha256, /^[a-f0-9]{64}$/);
});

test('keeps Microsoft Store artifacts separate from direct installers', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'voicestudio-microsoft-store-manifest-'));
  writeFileSync(join(dir, 'VoiceStudio-MicrosoftStore-0.5.7-win-x64.exe'), 'store');
  writeFileSync(join(dir, 'VoiceStudio-0.5.7-win-x64.exe'), 'direct');
  writeFileSync(join(dir, 'electron-microsoft-store-stable-win32-x64.yml'), 'version: 0.5.7\n');
  const output = join(dir, 'store-artifact-manifest-win32-x64.json');

  await createArtifactManifest({
    releaseDir: dir,
    output,
    distribution: 'microsoft-store',
    platform: 'win32',
    arch: 'x64',
    version: '0.5.7',
    commit: 'b'.repeat(40),
    generatedAt: '2026-10-13T00:00:00.000Z',
    signingVerification: { status: 'valid', evidence: 'authenticode-win32-x64.json' },
  });

  const manifest = JSON.parse(readFileSync(output, 'utf8'));
  assert.deepEqual(
    manifest.artifacts.map((item) => item.file),
    [
      'VoiceStudio-MicrosoftStore-0.5.7-win-x64.exe',
      'electron-microsoft-store-stable-win32-x64.yml',
    ],
  );
  assert.deepEqual(manifest.signingVerification, {
    status: 'valid',
    evidence: 'authenticode-win32-x64.json',
  });
});
