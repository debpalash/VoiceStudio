import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('electron', () => ({
  app: { isPackaged: true, getPath: () => tmpdir() },
  BrowserWindow: class BrowserWindow {},
  ipcMain: { handle: vi.fn(), removeHandler: vi.fn() },
}));

vi.mock('electron-updater', () => ({ default: { autoUpdater: {}, CancellationToken: class {} } }));

import {
  cleanInstalledUpdateDownload,
  pendingUpdateDir,
  pendingUpdateVersion,
  removeStalePendingUpdate,
} from './updater-cache';
import { compareAppVersions } from './updater';

const require = createRequire(import.meta.url);
const { AppUpdater } = require('electron-updater/out/AppUpdater');
const { DownloadedUpdateHelper } = require('electron-updater/out/DownloadedUpdateHelper');
const { getAppCacheDir } = require('electron-updater/out/AppAdapter');

let cache: string;
let pending: string;

/** Writes `pending/` the way electron-updater 6.8 does after a download. */
async function download(fileName: string): Promise<string> {
  mkdirSync(pending, { recursive: true });
  const file = join(pending, fileName);
  writeFileSync(file, 'payload');
  writeFileSync(join(pending, 'current.blockmap'), 'blockmap');
  const helper = new DownloadedUpdateHelper(cache);
  await helper.setDownloadedFile(
    file,
    null,
    { version: 'x' },
    { info: { sha512: 'abc' } },
    fileName,
    true,
  );
  return file;
}

beforeEach(() => {
  cache = mkdtempSync(join(tmpdir(), 'vs-updater-cache-'));
  pending = join(cache, 'pending');
  // Root files are the differential-download base and must survive.
  writeFileSync(join(cache, 'update.zip'), 'base');
  writeFileSync(join(cache, 'current.blockmap'), 'base-map');
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});

afterEach(() => {
  rmSync(cache, { recursive: true, force: true });
  vi.restoreAllMocks();
});

function rootUntouched() {
  expect(existsSync(join(cache, 'update.zip'))).toBe(true);
  expect(existsSync(join(cache, 'current.blockmap'))).toBe(true);
}

describe('installed update download cleanup', () => {
  test('reads the version from every platform artifact name', () => {
    expect(pendingUpdateVersion('VoiceStudio-0.5.8-beta.3-mac-arm64.zip')).toBe('0.5.8-beta.3');
    expect(pendingUpdateVersion('VoiceStudio-0.5.8-win-x64.exe')).toBe('0.5.8');
    expect(pendingUpdateVersion('VoiceStudio-0.5.8-linux-x64.AppImage')).toBe('0.5.8');
    expect(pendingUpdateVersion('VoiceStudio-Electron-0.5.6-mac-x64.zip')).toBe('0.5.6');
    expect(pendingUpdateVersion('latest.exe')).toBeNull();
    expect(pendingUpdateVersion('VoiceStudio-0.5.8-rc.1-mac-arm64.zip')).toBeNull();
  });

  test.each([
    ['the running version', '0.5.8-beta.3-mac-arm64.zip', '0.5.8-beta.3'],
    ['an older beta', '0.5.8-beta.2-mac-arm64.zip', '0.5.8-beta.3'],
    ['a beta of the running stable', '0.5.8-beta.3-win-x64.exe', '0.5.8'],
    ['an older stable', '0.5.7-linux-x64.AppImage', '0.5.8-beta.1'],
  ])('removes a download of %s', async (_label, artifact, running) => {
    await download(`VoiceStudio-${artifact}`);
    expect(await removeStalePendingUpdate(pending, running, compareAppVersions)).toBe(true);
    expect(existsSync(pending)).toBe(false);
    rootUntouched();
  });

  test.each([
    ['a newer beta', '0.5.8-beta.4-mac-arm64.zip', '0.5.8-beta.3'],
    ['the stable after a beta', '0.5.8-win-x64.exe', '0.5.8-beta.3'],
    ['a newer patch', '0.5.9-linux-x64.AppImage', '0.5.8'],
  ])('keeps a ready download of %s', async (_label, artifact, running) => {
    const file = await download(`VoiceStudio-${artifact}`);
    expect(await removeStalePendingUpdate(pending, running, compareAppVersions)).toBe(false);
    expect(existsSync(file)).toBe(true);
    expect(existsSync(join(pending, 'update-info.json'))).toBe(true);
    rootUntouched();
  });

  test.each([
    ['missing', null],
    ['garbled', '{not json'],
    ['without a file name', '{"sha512":"abc"}'],
    ['with a path for a file name', '{"fileName":"../VoiceStudio-0.5.1-mac-arm64.zip"}'],
    ['with an unknown file name', '{"fileName":"update.zip"}'],
    ['null', 'null'],
  ])('tolerates update info that is %s', async (_label, info) => {
    mkdirSync(pending, { recursive: true });
    writeFileSync(join(pending, 'VoiceStudio-0.5.1-mac-arm64.zip'), 'payload');
    if (info !== null) writeFileSync(join(pending, 'update-info.json'), info);
    expect(await removeStalePendingUpdate(pending, '0.5.8', compareAppVersions)).toBe(false);
    expect(existsSync(join(pending, 'VoiceStudio-0.5.1-mac-arm64.zip'))).toBe(true);
    rootUntouched();
  });

  test('tolerates a cache without a pending directory', async () => {
    expect(await removeStalePendingUpdate(pending, '0.5.8', compareAppVersions)).toBe(false);
    rootUntouched();
  });

  test('uses the pending directory electron-updater resolves', async () => {
    // Bound to the real method so an electron-updater rename fails here.
    const updater = {
      downloadedUpdateHelper: null,
      _logger: { error: vi.fn(), debug: undefined },
      configOnDisk: {
        value: Promise.resolve({ updaterCacheDirName: 'voicestudio-electron-updater' }),
      },
      app: { baseCachePath: getAppCacheDir(), name: 'VoiceStudio' },
      getOrCreateDownloadHelper: AppUpdater.prototype.getOrCreateDownloadHelper,
    };
    expect(await pendingUpdateDir(updater)).toBe(
      join(getAppCacheDir(), 'voicestudio-electron-updater', 'pending'),
    );
    expect(await pendingUpdateDir({})).toBeNull();
  });

  test('startup cleanup removes the installed download and never rejects', async () => {
    await download('VoiceStudio-0.5.8-mac-arm64.zip');
    const updater = {
      getOrCreateDownloadHelper: async () => ({ cacheDirForPendingUpdate: pending }),
    };
    await cleanInstalledUpdateDownload(updater, '0.5.8', compareAppVersions);
    expect(existsSync(pending)).toBe(false);
    rootUntouched();

    const broken = {
      getOrCreateDownloadHelper: async () => {
        throw Object.assign(new Error('no app-update.yml'), { code: 'ENOENT' });
      },
    };
    await expect(
      cleanInstalledUpdateDownload(broken, '0.5.8', compareAppVersions),
    ).resolves.toBeUndefined();
    expect(console.warn).toHaveBeenCalledWith(
      '[updater] could not locate the update cache: ENOENT',
    );
  });
});
