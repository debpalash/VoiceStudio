import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createMicrosoftStoreHandoff } from '../../scripts/create_microsoft_store_handoff.mjs';

test('creates a Partner Center handoff only from validated signed packages', () => {
  const dir = mkdtempSync(join(tmpdir(), 'voicestudio-microsoft-store-handoff-'));
  const file = 'VoiceStudio-MicrosoftStore-0.5.7-win-x64.exe';
  const packagePath = join(dir, file);
  writeFileSync(packagePath, 'signed installer fixture');
  const updateManifest = 'electron-microsoft-store-stable-win32-x64.yml';
  writeFileSync(join(dir, updateManifest), 'version: 0.5.7\n');
  const digest = '78406fc74fb60fb5a9babc70740938a6d95da5ca2cd0b5ec143f43dc57865604';
  const updateDigest = 'e9d1c3d8921489039e65fb6319a0d178d7dd9ffd2b75f08d42e76f76d56307c9';
  writeFileSync(
    join(dir, 'store-artifact-manifest-win32-x64.json'),
    JSON.stringify({
      distribution: { target: 'microsoft-store', applicationUpdates: 'application' },
      signingVerification: {
        status: 'valid',
        evidence: 'authenticode-win32-x64.json',
      },
      artifacts: [
        { file, bytes: 24, sha256: digest },
        { file: updateManifest, bytes: 15, sha256: updateDigest },
      ],
    }),
  );
  writeFileSync(join(dir, 'authenticode-win32-x64.json'), '{}');
  const output = join(dir, 'submission.json');

  const handoff = createMicrosoftStoreHandoff({
    releaseDir: dir,
    output,
    version: '0.5.7',
    tag: 'v0.5.7',
    generatedAt: '2026-10-13T00:00:00.000Z',
  });

  assert.equal(handoff.lifecycle, 'validated');
  assert.equal(handoff.ownerApprovalRequiredForSubmission, true);
  assert.equal(handoff.packages[0].installerParameters, '/S');
  assert.equal(handoff.packages[0].updateManifest, updateManifest);
  assert.equal(
    handoff.packages[0].url,
    'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.7/' + file,
  );
  assert.deepEqual(JSON.parse(readFileSync(output, 'utf8')), handoff);
});

test('rejects unsigned or non-versioned handoffs', () => {
  const dir = mkdtempSync(join(tmpdir(), 'voicestudio-microsoft-store-reject-'));
  assert.throws(
    () =>
      createMicrosoftStoreHandoff({
        releaseDir: dir,
        output: join(dir, 'submission.json'),
        version: '0.5.7',
        tag: 'main',
      }),
    /exact version tag/,
  );
});
