import { spawnSync } from 'node:child_process';
import './locale-encoding.mjs';
import config from '../electron-builder.config.mjs';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { verifyLinuxLibraries } from '../native-linux-libraries.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.platform === 'linux') {
  const librariesTest = spawnSync(
    process.execPath,
    ['--test', resolve(root, 'tests/native-linux-libraries.test.mjs')],
    { encoding: 'utf8' },
  );
  assert.equal(librariesTest.status, 0, librariesTest.stdout + librariesTest.stderr);
}
const require = createRequire(import.meta.url);
const artifactRequested = process.argv.includes('--artifact');
const artifactArch = process.env.VOICESTUDIO_RUST_TARGET?.startsWith('aarch64')
  ? 'arm64'
  : process.env.VOICESTUDIO_RUST_TARGET?.startsWith('x86_64')
    ? 'x64'
    : process.arch;
const artifactCandidates =
  process.platform === 'win32'
    ? artifactArch === 'arm64'
      ? ['release/win-arm64-unpacked', 'release/win-unpacked']
      : ['release/win-unpacked']
    : process.platform === 'darwin'
      ? artifactArch === 'arm64'
        ? ['release/mac-arm64/VoiceStudio.app/Contents', 'release/mac/VoiceStudio.app/Contents']
        : ['release/mac/VoiceStudio.app/Contents', 'release/mac-x64/VoiceStudio.app/Contents']
      : artifactArch === 'arm64'
        ? ['release/linux-arm64-unpacked', 'release/linux-unpacked']
        : ['release/linux-unpacked'];
const artifactRoot = resolve(
  root,
  artifactCandidates.find((candidate) => existsSync(resolve(root, candidate))) ??
    artifactCandidates[0],
);
const artifactResources = resolve(
  artifactRoot,
  process.platform === 'darwin' ? 'Resources' : 'resources',
);
const artifactExecutable = resolve(
  artifactRoot,
  process.platform === 'win32'
    ? 'VoiceStudio.exe'
    : process.platform === 'darwin'
      ? 'MacOS/VoiceStudio'
      : 'voicestudio-electron',
);
const electronPackage = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
assert.match(electronPackage.author?.email ?? '', /@/, 'Linux package declares a maintainer email');
assert.equal(electronPackage.desktopName, 'VoiceStudio', 'Linux desktop identity stays branded');
assert.equal(
  config.linux.syncDesktopName,
  true,
  'Linux window and desktop entry share one identity',
);
assert(config.mac.extendInfo.NSMicrophoneUsageDescription.includes('VoiceStudio'));
for (const entitlementFile of [config.mac.entitlements, config.mac.entitlementsInherit]) {
  const entitlements = readFileSync(resolve(root, entitlementFile), 'utf8');
  assert.match(entitlements, /<key>com\.apple\.security\.device\.audio-input<\/key>\s*<true\s*\/>/);
  assert.match(entitlements, /<key>com\.apple\.security\.cs\.allow-jit<\/key>\s*<true\s*\/>/);
}
const project = readFileSync(resolve(root, '../pyproject.toml'), 'utf8');
const readme = project.match(/^readme\s*=\s*"([^"]+)"/m)?.[1];
assert(readme, 'Python project declares a README');
for (const resource of [readme, 'LICENSE', 'pyproject.toml', 'uv.lock', 'backend', 'omnivoice']) {
  const entry = config.extraResources.find((item) => item.to === resource);
  assert(entry && existsSync(resolve(root, entry.from)), 'Required resource: ' + resource);
  if (artifactRequested)
    assert(existsSync(resolve(artifactResources, resource)), 'Packaged resource: ' + resource);
}
const bundledUvSource = config.extraResources.find((item) => /^tools\/uv(?:\.exe)?$/.test(item.to));
if (process.env.VOICESTUDIO_RUST_TARGET || process.env.VOICESTUDIO_BUNDLED_UV) {
  assert(bundledUvSource, 'Release builds package the pinned uv executable');
  assert(existsSync(bundledUvSource.from), 'Bundled uv source exists');
}
if (artifactRequested && bundledUvSource) {
  assert(existsSync(resolve(artifactResources, bundledUvSource.to)), 'Packaged uv executable');
}
assert.equal(
  config.extraMetadata.version,
  JSON.parse(readFileSync(resolve(root, '../package.json'))).version,
);
const platformIcons = {
  win: 'build/icons/icon.ico',
  mac: 'build/icons/icon.icns',
  linux: 'build/icons/icon.png',
};
for (const [platform, icon] of Object.entries(platformIcons)) {
  assert.equal(config[platform].icon, icon, `${platform} uses the shared VoiceStudio icon`);
  assert(existsSync(resolve(root, icon)), `${platform} VoiceStudio icon exists`);
}
for (const icon of ['brand/icon.png', 'brand/icon.ico', 'brand/32x32.png']) {
  const entry = config.extraResources.find((item) => item.to === icon);
  assert(entry && existsSync(resolve(root, entry.from)), `Runtime icon: ${icon}`);
}
assert.equal(typeof config.afterPack, 'function', 'Native helper must be built before signing');
if (artifactRequested) {
  if (process.platform === 'linux') {
    await verifyLinuxLibraries(resolve(artifactResources, 'native/voicestudio-desktop-bridge'));
  }
  assert(existsSync(artifactExecutable), 'Packaged VoiceStudio executable');
  assert(
    existsSync(
      resolve(
        artifactResources,
        'native',
        'voicestudio-desktop-bridge' + (process.platform === 'win32' ? '.exe' : ''),
      ),
    ),
    'Packaged native dictation helper',
  );
}

const hostIsNode = /^node(?:\.exe)?$/i.test(basename(process.execPath));
const syntaxRuntime = hostIsNode ? process.execPath : require('electron');
for (const entry of ['out/main/index.js', 'out/preload/index.mjs']) {
  const check = spawnSync(syntaxRuntime, ['--check', resolve(root, entry)], {
    encoding: 'utf8',
    env: hostIsNode ? process.env : { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
  });
  assert.equal(check.status, 0, `Built entry must parse: ${entry}\n${check.stderr}`);
}

const rendererHtml = readFileSync(resolve(root, 'out/renderer/index.html'), 'utf8');
const earlyCapture = rendererHtml.indexOf('early-error-capture.js');
const rendererModule = rendererHtml.search(/<script[^>]+type="module"/);
assert(earlyCapture >= 0, 'Renderer packages the early error capture');
assert(
  rendererModule < 0 || earlyCapture < rendererModule,
  'Early error capture loads before the renderer module graph',
);
assert(
  existsSync(resolve(root, 'out/renderer/early-error-capture.js')),
  'Early error capture is a standalone classic script',
);

console.log(
  `PASS: built entry syntax, Python resource contract, app version source${artifactRequested ? ` and ${process.platform} artifact` : ''}`,
);
