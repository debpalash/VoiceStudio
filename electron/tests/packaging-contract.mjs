import { spawnSync } from 'node:child_process';
import './locale-encoding.mjs';
import config, { ggufRuntimeName } from '../electron-builder.config.mjs';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { verifyLinuxLibraries } from '../native-linux-libraries.mjs';
import { APP_ID, PRODUCT_NAME } from '../../config/app-config.mjs';
import { DISTRIBUTION_PROFILE, DISTRIBUTION_TARGET } from '../../config/distributions.mjs';
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
      ? DISTRIBUTION_TARGET === 'mac-app-store'
        ? artifactArch === 'arm64'
          ? [
              `release/mas-arm64/${PRODUCT_NAME}.app/Contents`,
              `release/mas/${PRODUCT_NAME}.app/Contents`,
            ]
          : [
              `release/mas/${PRODUCT_NAME}.app/Contents`,
              `release/mas-x64/${PRODUCT_NAME}.app/Contents`,
            ]
        : artifactArch === 'arm64'
          ? [
              `release/mac-arm64/${PRODUCT_NAME}.app/Contents`,
              `release/mac/${PRODUCT_NAME}.app/Contents`,
            ]
          : [
              `release/mac/${PRODUCT_NAME}.app/Contents`,
              `release/mac-x64/${PRODUCT_NAME}.app/Contents`,
            ]
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
    ? `${PRODUCT_NAME}.exe`
    : process.platform === 'darwin'
      ? `MacOS/${PRODUCT_NAME}`
      : (config.linux.executableName ?? 'voicestudio-electron'),
);
const electronPackage = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
assert.match(electronPackage.author?.email ?? '', /@/, 'Linux package declares a maintainer email');
assert.equal(config.productName, PRODUCT_NAME, 'Builder uses the central product name');
assert.equal(config.appId, APP_ID, 'Builder uses the central application ID');
assert.equal(
  config.artifactName,
  `${PRODUCT_NAME}-${
    {
      'microsoft-store': 'MicrosoftStore',
      'mac-app-store': 'MacAppStore',
    }[DISTRIBUTION_TARGET] ?? 'Electron'
  }-\${version}-\${os}-\${arch}.\${ext}`,
  'Store packaging is distinct without renaming direct-download artifacts',
);
assert.equal(
  config.extraMetadata.voicestudioDistribution,
  DISTRIBUTION_TARGET,
  'Package records its immutable distribution identity',
);
assert.equal(
  config.extraMetadata.voicestudioUpdateOwner,
  DISTRIBUTION_PROFILE.applicationUpdates,
  'Package records who owns application updates',
);
if (DISTRIBUTION_TARGET === 'microsoft-store') {
  assert.equal(
    config.extraMetadata.author.name,
    'Yupcha Softwares Private Limited',
    'Microsoft Store uninstall metadata uses the legal publisher',
  );
} else {
  assert.equal(
    config.extraMetadata.author,
    undefined,
    'Direct package author metadata is unchanged',
  );
}
assert.equal(
  config.extraMetadata.desktopName,
  PRODUCT_NAME,
  'Linux desktop identity stays branded',
);
assert.equal(
  config.linux.syncDesktopName,
  true,
  'Linux window and desktop entry share one identity',
);
assert(config.mac.extendInfo.NSMicrophoneUsageDescription.includes(PRODUCT_NAME));
for (const entitlementFile of [config.mac.entitlements, config.mac.entitlementsInherit]) {
  const entitlements = readFileSync(resolve(root, entitlementFile), 'utf8');
  assert.match(entitlements, /<key>com\.apple\.security\.device\.audio-input<\/key>\s*<true\s*\/>/);
  assert.match(entitlements, /<key>com\.apple\.security\.cs\.allow-jit<\/key>\s*<true\s*\/>/);
}
if (DISTRIBUTION_TARGET !== 'mac-app-store') {
  assert.equal(
    config.mac.identity,
    process.env.CSC_LINK ? undefined : '-',
    'Unsigned macOS builds are signed ad hoc so the bundle signature stays valid',
  );
  if (config.mac.identity === '-') assert.equal(config.mac.hardenedRuntime, false);
}
if (DISTRIBUTION_TARGET === 'mac-app-store') {
  assert.deepEqual(config.mac.target, ['mas'], 'Mac App Store builds only the MAS package');
  assert.equal(config.mac.notarize, false, 'Apple handles Mac App Store notarization');
  assert.equal(config.mac.hardenedRuntime, false, 'MAS uses App Sandbox instead of Developer ID');
  assert.equal(config.mas.entitlements, 'build/entitlements.mas.plist');
  assert.equal(config.mas.entitlementsInherit, 'build/entitlements.mas.inherit.plist');
  const appEntitlements = readFileSync(resolve(root, config.mas.entitlements), 'utf8');
  for (const key of [
    'com.apple.security.app-sandbox',
    'com.apple.security.cs.allow-jit',
    'com.apple.security.device.audio-input',
    'com.apple.security.files.user-selected.read-write',
    'com.apple.security.network.client',
    'com.apple.security.network.server',
  ]) {
    assert.match(
      appEntitlements,
      new RegExp(`<key>${key.replaceAll('.', '\\.')}</key>\\s*<true\\s*/>`),
    );
  }
  const inheritedEntitlements = readFileSync(resolve(root, config.mas.entitlementsInherit), 'utf8');
  for (const key of ['com.apple.security.app-sandbox', 'com.apple.security.inherit']) {
    assert.match(
      inheritedEntitlements,
      new RegExp(`<key>${key.replaceAll('.', '\\.')}</key>\\s*<true\\s*/>`),
    );
  }
} else {
  assert.deepEqual(config.mac.target, ['dmg', 'zip'], 'Direct macOS artifacts stay unchanged');
}
const project = readFileSync(resolve(root, '../pyproject.toml'), 'utf8');
const readme = project.match(/^readme\s*=\s*"([^"]+)"/m)?.[1];
assert(readme, 'Python project declares a README');
for (const resource of [
  readme,
  'LICENSE',
  'LICENSE-NOTICE.md',
  'electron/T3CODE-LICENSE.txt',
  'pyproject.toml',
  'uv.lock',
  'backend',
  'omnivoice',
  'config/app.json',
]) {
  const entry = config.extraResources.find((item) => item.to === resource);
  assert(entry && existsSync(resolve(root, entry.from)), 'Required resource: ' + resource);
  if (artifactRequested)
    assert(existsSync(resolve(artifactResources, resource)), 'Packaged resource: ' + resource);
}
// #2599: LAN devices load the web UI from the backend, which serves this
// build from the app's resources. Without it they only get an error page.
const webUi = config.extraResources.find((item) => item.to === 'frontend/dist');
assert(webUi, 'Required resource: frontend/dist (web UI for LAN sharing)');
for (const [label, dir] of [
  ['Built', resolve(root, webUi.from)],
  ...(artifactRequested ? [['Packaged', resolve(artifactResources, webUi.to)]] : []),
]) {
  const index = resolve(dir, 'index.html');
  assert(existsSync(index), `${label} web UI entry (run build:web first): ${index}`);
  const html = readFileSync(index, 'utf8');
  for (const [, asset] of html.matchAll(/(?:src|href)="\/(assets\/[^"]+)"/g)) {
    assert(existsSync(resolve(dir, asset)), `${label} web UI asset: ${asset}`);
  }
}
const bundledUvSource = config.extraResources.find((item) => /^tools\/uv(?:\.exe)?$/.test(item.to));
if (process.env.VOICESTUDIO_RUST_TARGET || process.env.VOICESTUDIO_BUNDLED_UV) {
  assert(bundledUvSource, 'Release builds package the pinned uv executable');
  assert(existsSync(bundledUvSource.from), 'Bundled uv source exists');
}
if (artifactRequested && bundledUvSource) {
  assert(existsSync(resolve(artifactResources, bundledUvSource.to)), 'Packaged uv executable');
}
// #2780: every installer shipped without the GGUF runtime, so the engine was
// always unavailable. Release legs that build it set the requirement flag, so a
// package can no longer leave it out silently.
const ggufRuntime = config.extraResources.find((item) => item.to === 'bin');
if (process.env.VOICESTUDIO_REQUIRE_GGUF_RUNTIME === '1') {
  assert(ggufRuntimeName, 'This package target has an OmniVoice GGUF runtime');
  assert(ggufRuntime, `Release builds package the GGUF runtime: bin/${ggufRuntimeName}`);
}
if (ggufRuntime) {
  assert(ggufRuntime.filter.includes(ggufRuntimeName), 'GGUF resource ships the runtime');
  assert(ggufRuntime.filter.includes('checksums.sha256'), 'GGUF resource ships its checksum');
  if (artifactRequested)
    assert(
      existsSync(resolve(artifactResources, 'bin', ggufRuntimeName)),
      `Packaged GGUF runtime: bin/${ggufRuntimeName}`,
    );
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
// electron-builder UNIONS an explicit per-target `arch` with the --x64/--arm64
// CLI flag, so `--win --arm64` over `{ target: 'nsis', arch: ['x64'] }` also
// packages x64, and its native helper needs a toolchain the runner lacks. The
// CLI matrix alone picks the architecture; the default is the host's.
for (const platform of ['win', 'mac', 'linux']) {
  for (const target of [config[platform]?.target ?? []].flat()) {
    assert(
      typeof target === 'string' || target.arch === undefined,
      `${platform} target ${target.target} must not pin arch; CLI flags choose it`,
    );
  }
}
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
  `PASS: built entry syntax, Python and web UI resource contract, app version source${artifactRequested ? ` and ${process.platform} artifact` : ''}`,
);
