import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const root = fileURLToPath(new URL('../..', import.meta.url));

for (const distribution of ['direct', 'microsoft-store', 'snap-store', 'mac-app-store']) {
  test(`${distribution} packages use their registered application identity`, () => {
    const applicationId = execFileSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        "import config from './electron/electron-builder.config.mjs'; console.log(config.appId)",
      ],
      {
        cwd: root,
        env: { ...process.env, VOICESTUDIO_DISTRIBUTION: distribution },
        encoding: 'utf8',
      },
    ).trim();
    assert.equal(
      applicationId,
      distribution === 'mac-app-store' ? 'sh.voicestudio.pro' : 'com.voicestudio.desktop',
    );
  });
}

for (const [arch, target] of [
  ['arm64', 'aarch64-apple-darwin'],
  ['x64', 'x86_64-apple-darwin'],
]) {
  test(`MAS ${arch} runtime resources follow the supported local-backend architecture`, () => {
    const resources = JSON.parse(
      execFileSync(
        process.execPath,
        [
          '--input-type=module',
          '-e',
          "import config from './electron/electron-builder.config.mjs'; console.log(JSON.stringify(config.extraResources.map(item=>item.to)))",
        ],
        {
          cwd: root,
          env: {
            ...process.env,
            VOICESTUDIO_DISTRIBUTION: 'mac-app-store',
            VOICESTUDIO_RUST_TARGET: target,
          },
          encoding: 'utf8',
        },
      ),
    );
    assert.equal(resources.includes('python-runtime'), arch === 'arm64');
    assert.equal(resources.includes('media-tools'), arch === 'arm64');
    assert.equal(
      resources.some((name) => name.startsWith('tools/uv')),
      false,
    );
  });
}
