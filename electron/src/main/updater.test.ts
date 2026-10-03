import { beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: any[]) => void>>();
  const ipcHandlers = new Map<string, (...args: any[]) => unknown>();
  const disk = { channel: undefined as 'stable' | 'preview' | undefined };
  const fs = {
    readFileSync: vi.fn(() => {
      if (!disk.channel) throw new Error('missing');
      return JSON.stringify({ channel: disk.channel });
    }),
    writeFileSync: vi.fn((_path: string, value: string) => {
      disk.channel = JSON.parse(value).channel;
    }),
    renameSync: vi.fn(),
  };
  const autoUpdater = {
    autoDownload: true,
    autoInstallOnAppQuit: true,
    allowDowngrade: true,
    allowPrerelease: false,
    channel: '',
    on: vi.fn((event: string, listener: (...args: any[]) => void) => {
      const listeners = handlers.get(event) || new Set();
      listeners.add(listener);
      handlers.set(event, listeners);
    }),
    setFeedURL: vi.fn(),
    checkForUpdates: vi.fn(async () => undefined),
    downloadUpdate: vi.fn(async () => undefined),
    quitAndInstall: vi.fn(),
  };
  return {
    app: {
      isPackaged: true,
      getVersion: vi.fn(() => '0.5.2'),
      getPath: vi.fn(() => 'C:\\VoiceStudio-test'),
    },
    autoUpdater,
    disk,
    fs,
    handlers,
    ipcHandlers,
    ipcMain: {
      handle: vi.fn((channel: string, handler: (...args: any[]) => unknown) => {
        ipcHandlers.set(channel, handler);
      }),
      removeHandler: vi.fn((channel: string) => ipcHandlers.delete(channel)),
    },
    trusted: vi.fn(() => true),
  };
});

vi.mock('electron', () => ({
  app: mocks.app,
  BrowserWindow: class BrowserWindow {},
  ipcMain: mocks.ipcMain,
}));

vi.mock('electron-updater', () => ({ default: { autoUpdater: mocks.autoUpdater } }));

vi.mock('node:fs', () => ({ ...mocks.fs, default: mocks.fs }));

vi.mock('./trusted-renderer', () => ({ isTrustedRenderer: mocks.trusted }));

import {
  compareReleaseVersions,
  DesktopUpdater,
  feedManifestName,
  listDesktopReleases,
  registerUpdateIpc,
  resolvePreviewFeed,
  UPDATE_CHANNELS,
} from './updater';

function emit(event: string, value?: unknown) {
  for (const listener of mocks.handlers.get(event) || []) listener(value);
}

describe('DesktopUpdater', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.ipcHandlers.clear();
    mocks.disk.channel = undefined;
    mocks.app.isPackaged = true;
    mocks.autoUpdater.autoDownload = true;
    mocks.autoUpdater.autoInstallOnAppQuit = true;
    mocks.autoUpdater.allowDowngrade = true;
    mocks.autoUpdater.allowPrerelease = false;
    mocks.autoUpdater.channel = '';
    vi.clearAllMocks();
    mocks.autoUpdater.checkForUpdates.mockResolvedValue(undefined);
    mocks.autoUpdater.downloadUpdate.mockResolvedValue(undefined);
    mocks.trusted.mockReturnValue(true);
  });

  test('configures the stable platform feed and follows updater lifecycle events', () => {
    const updater = new DesktopUpdater();
    const observed: string[] = [];
    updater.subscribe((state) => observed.push(state.status));

    expect(mocks.autoUpdater.autoDownload).toBe(false);
    expect(mocks.autoUpdater.autoInstallOnAppQuit).toBe(false);
    expect(mocks.autoUpdater.allowDowngrade).toBe(false);
    expect(mocks.autoUpdater.allowPrerelease).toBe(false);
    expect(mocks.autoUpdater.channel).toBe(`electron-stable-${process.platform}-${process.arch}`);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/latest/download',
      channel: `electron-stable-${process.platform}-${process.arch}`,
    });

    emit('checking-for-update');
    emit('update-available', {
      version: '0.5.3',
      releaseNotes: [{ note: 'One' }, { note: 'Two' }],
    });
    emit('download-progress', { percent: 137 });
    emit('update-downloaded', { version: '0.5.3', releaseNotes: 'Ready' });

    expect(observed).toEqual(['checking', 'available', 'downloading', 'downloaded']);
    expect(updater.snapshot()).toMatchObject({
      status: 'downloaded',
      currentVersion: '0.5.2',
      availableVersion: '0.5.3',
      notes: 'Ready',
      progress: 100,
      transferredBytes: 0,
    });
  });

  test('reports transfer size, speed and eta while coalescing duplicate actions', async () => {
    const updater = new DesktopUpdater();
    let finishCheck!: () => void;
    mocks.autoUpdater.checkForUpdates.mockImplementationOnce(
      () => new Promise<undefined>((resolve) => (finishCheck = () => resolve(undefined))),
    );

    const firstCheck = updater.check();
    const secondCheck = updater.check();
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
    finishCheck();
    await Promise.all([firstCheck, secondCheck]);

    emit('update-available', {
      version: '0.5.3',
      releaseNotes: null,
      files: [{ url: 'VoiceStudio.exe', sha512: 'hash', size: 1_000 }],
    });
    emit('download-progress', {
      percent: 25,
      transferred: 250,
      total: 1_000,
      bytesPerSecond: 100,
    });
    expect(updater.snapshot()).toMatchObject({
      status: 'downloading',
      progress: 25,
      transferredBytes: 250,
      totalBytes: 1_000,
      bytesPerSecond: 100,
      etaSeconds: 8,
    });
  });

  test('switches and persists the preview channel before checking it', async () => {
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      const version = url.includes('/preview/') ? '0.5.3-144' : '0.5.2';
      return new Response(`version: ${version}\n`);
    }) as typeof fetch;
    const updater = new DesktopUpdater(fetcher);
    await updater.setChannel('preview');

    expect(mocks.disk.channel).toBe('preview');
    expect(mocks.autoUpdater.allowPrerelease).toBe(true);
    expect(mocks.autoUpdater.channel).toBe(`electron-preview-${process.platform}-${process.arch}`);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/preview',
      channel: `electron-preview-${process.platform}-${process.arch}`,
    });
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(updater.snapshot().channel).toBe('preview');
    await expect(updater.setChannel('nightly' as any)).rejects.toThrow('Invalid update channel');
  });

  test('keeps Preview selected while using a newer Stable feed without allowing downgrade', async () => {
    mocks.disk.channel = 'preview';
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const version = String(input).includes('/preview/') ? '0.5.3-144' : '0.5.3';
      return new Response(`version: ${version}\n`);
    }) as typeof fetch;
    const updater = new DesktopUpdater(fetcher);

    await updater.check();

    expect(updater.snapshot().channel).toBe('preview');
    expect(mocks.autoUpdater.allowPrerelease).toBe(true);
    expect(mocks.autoUpdater.allowDowngrade).toBe(false);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/latest/download',
      channel: `electron-stable-${process.platform}-${process.arch}`,
    });
  });

  test('keeps startup checks quiet while reporting manual check failures', async () => {
    const updater = new DesktopUpdater();
    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(new Error('offline'));
    expect((await updater.check(true)).status).toBe('idle');
    expect(updater.snapshot().error).toBeUndefined();

    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(new Error('feed unavailable'));
    expect(await updater.check()).toMatchObject({
      status: 'error',
      error: 'feed unavailable',
    });

    expect(updater.dismiss()).toMatchObject({ status: 'idle', progress: 0 });
    expect(updater.snapshot().error).toBeUndefined();
  });

  test('downloads and installs only after the matching lifecycle gates', async () => {
    const updater = new DesktopUpdater();
    await updater.download();
    updater.install();
    expect(mocks.autoUpdater.downloadUpdate).not.toHaveBeenCalled();
    expect(mocks.autoUpdater.quitAndInstall).not.toHaveBeenCalled();

    emit('update-available', { version: '0.5.3', releaseNotes: null });
    await updater.download();
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledOnce();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
    updater.install();
    expect(mocks.autoUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
  });

  test('exposes unsupported state without touching updater APIs in development', async () => {
    mocks.app.isPackaged = false;
    const updater = new DesktopUpdater();
    expect(updater.snapshot()).toMatchObject({ status: 'unsupported', channel: 'stable' });
    await updater.check();
    await updater.download();
    updater.install();
    expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
    expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
  });

  test('stays unsupported when a package manager owns the install', async () => {
    vi.stubEnv('VOICESTUDIO_DISABLE_UPDATER', '1');
    try {
      const updater = new DesktopUpdater();
      expect(updater.snapshot()).toMatchObject({ status: 'unsupported' });
      await updater.check();
      expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
      expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  test('loads and validates native release history without trusting GitHub response fields', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            tag_name: 'v0.5.2',
            name: 'VoiceStudio 0.5.2',
            published_at: '2026-09-12T12:00:00Z',
            prerelease: false,
            body: 'Ready',
          },
          { name: 'missing tag' },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    await expect(listDesktopReleases(fetcher)).resolves.toEqual([
      {
        version: '0.5.2',
        name: 'VoiceStudio 0.5.2',
        date: '2026-09-12T12:00:00Z',
        prerelease: false,
        notes: 'Ready',
      },
    ]);
    expect(fetcher).toHaveBeenCalledWith(
      expect.stringContaining('/debpalash/VoiceStudio/releases?per_page=30'),
      expect.objectContaining({
        headers: expect.objectContaining({ 'User-Agent': 'VoiceStudio' }),
        signal: expect.any(AbortSignal),
      }),
    );
  });
});

describe('update feed selection', () => {
  test('matches electron-updater platform manifest names', () => {
    expect(feedManifestName('preview', 'win32', 'x64')).toBe('electron-preview-win32-x64.yml');
    expect(feedManifestName('preview', 'darwin', 'arm64')).toBe(
      'electron-preview-darwin-arm64-mac.yml',
    );
    expect(feedManifestName('preview', 'linux', 'x64')).toBe(
      'electron-preview-linux-x64-linux.yml',
    );
  });

  test('orders numeric previews below the stable release with the same core version', () => {
    expect(compareReleaseVersions('0.5.3-145', '0.5.3-144')).toBe(1);
    expect(compareReleaseVersions('0.5.3', '0.5.3-999')).toBe(1);
    expect(compareReleaseVersions('0.5.2', '0.5.3-1')).toBe(-1);
  });

  test.each([
    ['preview', '0.5.3-144', '0.5.2', 200, 200],
    ['stable', '0.5.3-144', '0.5.3', 200, 200],
    ['stable', '', '0.5.2', 404, 200],
    ['preview', '0.5.3-144', '', 200, 404],
    ['preview', '', '', 404, 404],
  ] as const)(
    'selects %s for preview=%s stable=%s',
    async (expected, previewVersion, stableVersion, previewStatus, stableStatus) => {
      const fetcher = vi.fn(async (input: string | URL | Request) => {
        const preview = String(input).includes('/preview/');
        return new Response(`version: ${preview ? previewVersion : stableVersion}\n`, {
          status: preview ? previewStatus : stableStatus,
        });
      }) as typeof fetch;
      await expect(resolvePreviewFeed(fetcher, 'win32', 'x64')).resolves.toBe(expected);
    },
  );
});

describe('update IPC', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.ipcHandlers.clear();
    mocks.disk.channel = undefined;
    mocks.app.isPackaged = true;
    vi.clearAllMocks();
    mocks.autoUpdater.checkForUpdates.mockResolvedValue(undefined);
    mocks.trusted.mockReturnValue(true);
  });

  test('flushes app state before installing and rejects foreign renderers', async () => {
    const updater = new DesktopUpdater();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
    const beforeInstall = vi.fn(async () => undefined);
    const webContents = {
      mainFrame: { url: 'app://voicestudio/index.html' },
      send: vi.fn(),
    };
    const owner = { webContents, isDestroyed: () => false } as any;
    const dispose = registerUpdateIpc(updater, () => owner, beforeInstall);
    const install = mocks.ipcHandlers.get(UPDATE_CHANNELS.install)!;

    await install({ sender: webContents, senderFrame: webContents.mainFrame });
    expect(beforeInstall).toHaveBeenCalledOnce();
    expect(mocks.autoUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);

    expect(() =>
      mocks.ipcHandlers.get(UPDATE_CHANNELS.getState)!({
        sender: {},
        senderFrame: { url: 'https://attacker.invalid' },
      }),
    ).toThrow('Untrusted update request');

    dispose();
    expect(mocks.ipcMain.removeHandler).toHaveBeenCalledTimes(7);
  });
});
