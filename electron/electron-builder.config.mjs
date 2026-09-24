// electron-builder configuration.
//
// The installer version is NOT stored in electron/package.json (its version
// field is a placeholder). It is read from frontend/package.json at build time
// so the Electron shell can never drift from the app version (CLAUDE.md,
// Versioning: frontend/package.json is the single source of truth).
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { packageNativeHelper } from './native-helper-build.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(readFileSync(resolve(here, '../frontend/package.json'), 'utf-8'));
const updateChannel =
  process.env.VOICESTUDIO_UPDATE_CHANNEL || `electron-stable-${process.platform}-${process.arch}`;
const updateUrl = updateChannel.startsWith('electron-preview-')
  ? 'https://github.com/debpalash/VoiceStudio/releases/download/preview'
  : 'https://github.com/debpalash/VoiceStudio/releases/latest/download';

const defaultRustTarget =
  process.platform === 'win32' && process.arch === 'x64'
    ? 'x86_64-pc-windows-msvc'
    : process.platform === 'darwin' && process.arch === 'arm64'
      ? 'aarch64-apple-darwin'
      : process.platform === 'darwin' && process.arch === 'x64'
        ? 'x86_64-apple-darwin'
        : process.platform === 'linux' && process.arch === 'x64'
          ? 'x86_64-unknown-linux-gnu'
          : null;
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
const notarizeMac = Boolean(
  process.env.CSC_LINK &&
  process.env.CSC_KEY_PASSWORD &&
  process.env.APPLE_ID &&
  process.env.APPLE_APP_SPECIFIC_PASSWORD &&
  process.env.APPLE_TEAM_ID,
);

/** @type {import('electron-builder').Configuration} */
export default {
  appId: 'com.voicestudio.desktop',
  productName: 'VoiceStudio',
  extraMetadata: { version },
  directories: { output: 'release', buildResources: 'build' },
  artifactName: 'VoiceStudio-Electron-${version}-${os}-${arch}.${ext}',
  // The default FUSE2 runtime cannot start on distros without libfuse.so.2.
  // v26's pinned static runtime keeps AppImage mounting independent of FUSE2.
  toolsets: { appimage: '1.0.3' },
  files: ['out/**/*', 'package.json'],
  // The Python backend + engine sources ride along as plain resources (same as
  // the Tauri bundle): the shell bootstraps a uv venv on first run.
  extraResources: [
    { from: '../frontend/src-tauri/icons/icon.png', to: 'brand/icon.png' },
    { from: '../frontend/src-tauri/icons/icon.ico', to: 'brand/icon.ico' },
    { from: '../frontend/src-tauri/icons/32x32.png', to: 'brand/32x32.png' },
    {
      from: '../frontend/src-tauri/icons/tray-recording.png',
      to: 'brand/tray-recording.png',
    },
    {
      from: '../backend',
      to: 'backend',
      filter: ['**/*', '!**/__pycache__/**', '!**/*.pyc'],
    },
    {
      from: '../omnivoice',
      to: 'omnivoice',
      filter: ['**/*', '!**/__pycache__/**', '!**/*.pyc'],
    },
    { from: '../pyproject.toml', to: 'pyproject.toml' },
    { from: '../uv.lock', to: 'uv.lock' },
    { from: '../README.md', to: 'README.md' },
    { from: '../LICENSE', to: 'LICENSE' },
    ...bundledUvResources,
  ],
  asar: true,
  afterPack: packageNativeHelper,
  win: {
    icon: '../frontend/src-tauri/icons/icon.ico',
    target: [{ target: 'nsis', arch: ['x64'] }],
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    perMachine: false,
  },
  mac: {
    icon: '../frontend/src-tauri/icons/icon.icns',
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    // Keep unsigned artifact rehearsals at their existing signing defaults;
    // a Developer ID certificate and Apple credentials enable both together.
    hardenedRuntime: notarizeMac,
    notarize: notarizeMac,
    extendInfo: {
      NSMicrophoneUsageDescription: readFileSync(
        resolve(here, '../frontend/src-tauri/Info.plist'),
        'utf8',
      ).match(/<key>NSMicrophoneUsageDescription<\/key>\s*<string>([^<]+)<\/string>/)[1],
    },
    // The CLI matrix selects one architecture per runner and updater feed.
    target: ['dmg', 'zip'],
    category: 'public.app-category.productivity',
  },
  linux: {
    // Linux targets rewrite ${arch} to x86_64/amd64; feeds use Node's x64.
    artifactName: 'VoiceStudio-Electron-${version}-linux-x64.${ext}',
    icon: '../frontend/src-tauri/icons/icon.png',
    syncDesktopName: true,
    target: ['AppImage', 'deb'],
    category: 'Audio',
  },
  publish: {
    provider: 'generic',
    url: updateUrl,
    channel: updateChannel,
  },
};
