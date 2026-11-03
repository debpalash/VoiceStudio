// electron-builder configuration.
//
// The installer version is NOT stored in electron/package.json (its version
// field is a placeholder). It is read from the root package.json at build time
// so the Electron shell can never drift from the app version (CLAUDE.md,
// Versioning: package.json is the single source of truth).
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { packageNativeHelper } from './native-helper-build.mjs';
import { finalizeOmnivoiceTts, omnivoiceTtsResources, signMac } from './omnivoice-tts-bundle.mjs';
import { repairSnapArtifacts } from './snap-template-payload.mjs';
import { signMasRuntime } from './mas-runtime-bundle.mjs';
import { APP_ID, PRODUCT_NAME } from '../config/app-config.mjs';
import { DISTRIBUTION_PROFILE, DISTRIBUTION_TARGET } from '../config/distributions.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(readFileSync(resolve(here, '../package.json'), 'utf-8'));
const storeMetadata = JSON.parse(readFileSync(resolve(here, '../store/metadata.json'), 'utf-8'));
const storeIdentity = storeMetadata.identity;
const isMacAppStore = DISTRIBUTION_TARGET === 'mac-app-store';
// Intel Macs retain their existing remote-backend UI. Never ship an arm64
// interpreter in an x64 package (the release matrix always names its target).
const macRuntimeArch = process.env.VOICESTUDIO_RUST_TARGET?.startsWith('x86_64')
  ? 'x64'
  : process.env.VOICESTUDIO_RUST_TARGET?.startsWith('aarch64')
    ? 'arm64'
    : process.arch;
const bundlesMasRuntime = isMacAppStore && macRuntimeArch === 'arm64';
// Direct downloads are `VoiceStudio-<version>-<os>-<arch>.<ext>`; store builds
// keep a label so they never collide with them. Releases up to v0.5.7-beta.3
// used `VoiceStudio-Electron-*`; the installers still resolve those names.
const artifactLabel = {
  'microsoft-store': 'MicrosoftStore',
  'mac-app-store': 'MacAppStore',
}[DISTRIBUTION_TARGET];
const artifactPrefix = [PRODUCT_NAME.replace(/[^A-Za-z0-9]+/g, '-'), artifactLabel]
  .filter(Boolean)
  .join('-');
// Stable and Beta are the Electron update feeds (Beta = `-beta.N` prereleases,
// stamped by electron-release.yml). Packaging rehearsals from main are stamped
// stable; they are review artifacts and are never published.
const updateChannel =
  process.env.VOICESTUDIO_UPDATE_CHANNEL || `electron-stable-${process.platform}-${process.arch}`;
if (
  !/^electron-(?:(?:microsoft-store-)?stable|beta)-(?:win32|darwin|linux)-(?:x64|arm64)$/.test(
    updateChannel,
  )
) {
  throw new Error(`Unsupported Electron update channel: ${updateChannel}`);
}
const updateUrl = 'https://github.com/debpalash/VoiceStudio/releases/latest/download';

export function distributionPackaging(target = DISTRIBUTION_TARGET) {
  if (target === 'snap-store') {
    return {
      linuxTargets: ['snap'],
      linuxExecutableName: 'voicestudio',
      snapcraft: {
        // Core 22 uses electron-builder's reproducible Electron snap template;
        // strict confinement is explicit so a future default cannot weaken it.
        base: 'core22',
        core22: {
          confinement: 'strict',
          grade: 'stable',
          title: PRODUCT_NAME,
          summary: storeMetadata.listing.shortDescription,
          description: storeMetadata.listing.description,
          allowNativeWayland: true,
          plugs: [
            { 'browser-support': { interface: 'browser-support', 'allow-sandbox': false } },
            'desktop',
            'desktop-legacy',
            'home',
            'x11',
            'wayland',
            'unity7',
            'network',
            'network-bind',
            'gsettings',
            'audio-playback',
            'audio-record',
            'pulseaudio',
            'opengl',
          ],
        },
      },
      publish: { provider: 'snapStore' },
    };
  }
  return {
    linuxTargets: ['AppImage', 'deb'],
    publish: {
      provider: 'generic',
      url: updateUrl,
      channel: updateChannel,
    },
  };
}

const packaging = distributionPackaging();

const defaultRustTarget =
  {
    'win32-x64': 'x86_64-pc-windows-msvc',
    // Windows on ARM: native Electron shell + helper; the Python runtime is the
    // emulated x64 build (see docs/install/windows.md).
    'win32-arm64': 'aarch64-pc-windows-msvc',
    'darwin-arm64': 'aarch64-apple-darwin',
    'darwin-x64': 'x86_64-apple-darwin',
    'linux-x64': 'x86_64-unknown-linux-gnu',
  }[`${process.platform}-${process.arch}`] ?? null;
const rustTarget = process.env.VOICESTUDIO_RUST_TARGET || defaultRustTarget;
const uvExtension = rustTarget?.includes('windows') ? '.exe' : '';
const uvSource = process.env.VOICESTUDIO_BUNDLED_UV
  ? resolve(process.env.VOICESTUDIO_BUNDLED_UV)
  : rustTarget
    ? resolve(here, `build/uv/uv-${rustTarget}${uvExtension}`)
    : null;
const bundledUvResources =
  uvSource && existsSync(uvSource) && statSync(uvSource).size > 0
    ? [{ from: uvSource, to: `tools/uv${uvExtension}` }]
    : [];
// OmniVoice GGUF runtime, staged by the release workflows (#2780).
const bundledOmnivoiceTtsResources =
  isMacAppStore && !bundlesMasRuntime ? [] : omnivoiceTtsResources();
const notarizeMac =
  !isMacAppStore &&
  Boolean(
    process.env.CSC_LINK &&
    process.env.CSC_KEY_PASSWORD &&
    process.env.APPLE_ID &&
    process.env.APPLE_APP_SPECIFIC_PASSWORD &&
    process.env.APPLE_TEAM_ID,
  );

// electron-builder never signs ad hoc on its own when no certificate exists, so
// the bundle keeps Electron's original signature while its contents change. That
// signature is invalid, and quarantined downloads then fail as "damaged"
// (#2763, #2829, #2681). Sign ad hoc explicitly when no Developer ID is supplied.
const adhocSignMac = !isMacAppStore && !process.env.CSC_LINK;

/** @type {import('electron-builder').Configuration} */
export default {
  appId: isMacAppStore ? storeIdentity.macAppStore.applicationId : APP_ID,
  productName: PRODUCT_NAME,
  extraMetadata: {
    version,
    productName: PRODUCT_NAME,
    desktopName: PRODUCT_NAME,
    voicestudioDistribution: DISTRIBUTION_TARGET,
    voicestudioUpdateOwner: DISTRIBUTION_PROFILE.applicationUpdates,
    ...(DISTRIBUTION_TARGET === 'microsoft-store'
      ? { author: { name: storeIdentity.publisherLegalName, email: 'hi@voicestudio.sh' } }
      : {}),
  },
  directories: { output: 'release', buildResources: 'build' },
  artifactName: `${artifactPrefix}-\${version}-\${os}-\${arch}.\${ext}`,
  // The default FUSE2 runtime cannot start on distros without libfuse.so.2.
  // v26's pinned static runtime keeps AppImage mounting independent of FUSE2.
  toolsets: { appimage: '1.0.3' },
  files: ['out/**/*', 'package.json'],
  // The Python backend + engine sources ride along as plain resources (same as
  // the installer): the shell bootstraps a uv venv on first run.
  extraResources: [
    { from: 'build/icons/icon.png', to: 'brand/icon.png' },
    { from: 'build/icons/icon-macos.png', to: 'brand/icon-macos.png' },
    { from: 'build/icons/icon.ico', to: 'brand/icon.ico' },
    { from: 'build/icons/32x32.png', to: 'brand/32x32.png' },
    { from: 'build/icons/trayTemplate.png', to: 'brand/trayTemplate.png' },
    {
      from: 'build/icons/trayRecordingTemplate.png',
      to: 'brand/trayRecordingTemplate.png',
    },
    {
      from: 'build/icons/tray-recording.png',
      to: 'brand/tray-recording.png',
    },
    {
      from: '../backend',
      to: 'backend',
      filter: ['**/*', '!**/__pycache__/**', '!**/*.pyc'],
    },
    {
      from: '../frontend/dist',
      to: 'frontend/dist',
      filter: ['**/*'],
    },
    {
      from: '../omnivoice',
      to: 'omnivoice',
      filter: ['**/*', '!**/__pycache__/**', '!**/*.pyc'],
    },
    { from: '../pyproject.toml', to: 'pyproject.toml' },
    { from: '../uv.lock', to: 'uv.lock' },
    { from: '../scripts/windows-rocm-recipe.json', to: 'scripts/windows-rocm-recipe.json' },
    { from: '../scripts/smoke_windows_rocm.py', to: 'scripts/smoke_windows_rocm.py' },
    { from: '../README.md', to: 'README.md' },
    { from: '../LICENSE', to: 'LICENSE' },
    { from: '../LICENSE-NOTICE.md', to: 'LICENSE-NOTICE.md' },
    { from: '../config/app.json', to: 'config/app.json' },
    { from: 'T3CODE-LICENSE.txt', to: 'electron/T3CODE-LICENSE.txt' },
    ...(!isMacAppStore ? bundledUvResources : []),
    ...bundledOmnivoiceTtsResources,
    ...(bundlesMasRuntime
      ? [
          { from: 'build/python-runtime', to: 'python-runtime' },
          { from: 'build/media-tools', to: 'media-tools' },
        ]
      : []),
  ],
  asar: true,
  afterPack: async (context) => {
    await packageNativeHelper(context);
    finalizeOmnivoiceTts(context);
  },
  ...(DISTRIBUTION_TARGET === 'snap-store'
    ? { afterAllArtifactBuild: ({ artifactPaths }) => repairSnapArtifacts(artifactPaths) }
    : {}),
  win: {
    icon: 'build/icons/icon.ico',
    // The CLI matrix selects one architecture per runner and updater feed
    // (--x64 / --arm64); an unflagged local build uses the host's. Never pin
    // `arch` here: electron-builder unions it with the CLI flag, so
    // `--win --arm64` would also build x64 (tests/packaging-contract.mjs).
    target: ['nsis'],
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    perMachine: false,
  },
  mac: {
    icon: 'build/icons/icon.icns',
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    // Keep unsigned artifact rehearsals at their existing signing defaults;
    // a Developer ID certificate and Apple credentials enable both together.
    hardenedRuntime: isMacAppStore ? false : notarizeMac,
    ...(adhocSignMac ? { identity: '-' } : {}),
    // Signs the bundled GGUF runtime and refreshes its checksum manifest
    // before the bundle seal (electron/omnivoice-tts-bundle.mjs).
    ...(isMacAppStore
      ? { sign: signMasRuntime }
      : bundledOmnivoiceTtsResources.length
        ? { sign: signMac }
        : {}),
    notarize: notarizeMac,
    extendInfo: {
      NSMicrophoneUsageDescription: readFileSync(resolve(here, 'build/Info.plist'), 'utf8')
        .match(/<key>NSMicrophoneUsageDescription<\/key>\s*<string>([^<]+)<\/string>/)[1]
        .replaceAll('__APP_PRODUCT_NAME__', PRODUCT_NAME),
    },
    // The CLI matrix selects one architecture per runner and updater feed.
    target: isMacAppStore ? ['mas'] : ['dmg', 'zip'],
    category: 'public.app-category.productivity',
  },
  mas: {
    entitlements: 'build/entitlements.mas.plist',
    entitlementsInherit: 'build/entitlements.mas.inherit.plist',
    ...(process.env.VOICESTUDIO_MAS_PROVISIONING_PROFILE
      ? { provisioningProfile: process.env.VOICESTUDIO_MAS_PROVISIONING_PROFILE }
      : {}),
  },
  linux: {
    // Linux targets rewrite ${arch} to x86_64/amd64; feeds use Node's x64.
    artifactName: `${artifactPrefix}-\${version}-linux-x64.\${ext}`,
    icon: 'build/icons/icon.png',
    syncDesktopName: true,
    target: packaging.linuxTargets,
    ...(packaging.linuxExecutableName ? { executableName: packaging.linuxExecutableName } : {}),
    category: 'Audio',
  },
  ...(packaging.snapcraft ? { snapcraft: packaging.snapcraft } : {}),
  publish: packaging.publish,
};
