import { beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: any[]) => void>>();
  const ipcHandlers = new Map<string, (...args: any[]) => unknown>();
  // updates.json holds the saved update channel ("preview" is a retired value).
  const disk = { file: undefined as string | undefined };
  const fs = {
    readFileSync: vi.fn((_path: string) => {
      if (disk.file === undefined) throw new Error('ENOENT');
      return disk.file;
    }),
    writeFileSync: vi.fn((_path: string, data: string) => {
      disk.file = data;
    }),
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
  DesktopUpdater,
  feedManifestName,
  listDesktopReleases,
  readSavedUpdateChannel,
  registerUpdateIpc,
  releaseSize,
  resolveBetaFeed,
  resolveUpdateFeedUrl,
  UPDATE_CHANNELS,
} from './updater';

function emit(event: string, value?: unknown) {
  for (const listener of mocks.handlers.get(event) || []) listener(value);
}

describe('DesktopUpdater', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.ipcHandlers.clear();
    mocks.disk.file = undefined;
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
    expect(mocks.autoUpdater).toHaveProperty('disableWebInstaller', true);
    expect(mocks.autoUpdater.channel).toBe(`electron-stable-${process.platform}-${process.arch}`);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/latest/download',
      channel: `electron-stable-${process.platform}-${process.arch}`,
      useMultipleRangeRequest: false,
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
      currentVersion: __APP_VERSION__,
      availableVersion: '0.5.3',
      notes: 'Ready',
      progress: 100,
      transferredBytes: 0,
    });
  });

  test('keeps Microsoft Store updates on the newest release containing its feed', async () => {
    const channel = `electron-microsoft-store-stable-${process.platform}-${process.arch}`;
    const manifest = feedManifestName(process.platform, process.arch, 'microsoft-store');
    const feedFetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify([
            { tag_name: 'v0.5.6', assets: [{ name: manifest }] },
            { tag_name: 'v0.5.8', assets: [{ name: 'unrelated.txt' }] },
            { tag_name: 'v0.5.7', assets: [{ name: manifest }] },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    );
    const updater = new DesktopUpdater('microsoft-store', feedFetcher);
    expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
    await updater.check();
    expect(mocks.autoUpdater.channel).toBe(channel);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.7',
      channel,
      useMultipleRangeRequest: false,
    });
  });

  test('prevents downloads from racing Store feed discovery', async () => {
    const manifest = feedManifestName(process.platform, process.arch, 'microsoft-store');
    let finishFeedLookup!: (response: Response) => void;
    const feedFetcher = vi.fn(
      () => new Promise<Response>((resolve) => (finishFeedLookup = resolve)),
    );
    const updater = new DesktopUpdater('microsoft-store', feedFetcher);
    emit('update-available', { version: '0.5.7', releaseNotes: null });

    const check = updater.check();
    expect(updater.snapshot().status).toBe('checking');
    await updater.download();
    expect(mocks.autoUpdater.downloadUpdate).not.toHaveBeenCalled();

    finishFeedLookup(
      new Response(JSON.stringify([{ tag_name: 'v0.5.7', assets: [{ name: manifest }] }]), {
        status: 200,
      }),
    );
    await check;
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
  });

  test('preserves an available update when quiet Store feed discovery fails', async () => {
    const feedFetcher = vi.fn(async () => new Response('unavailable', { status: 503 }));
    const updater = new DesktopUpdater('microsoft-store', feedFetcher);
    emit('update-available', { version: '0.5.7', releaseNotes: 'Ready' });

    await updater.check(true);

    expect(updater.snapshot()).toMatchObject({
      status: 'available',
      availableVersion: '0.5.7',
      notes: 'Ready',
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

  test('reads a saved Preview or damaged choice as Stable', () => {
    for (const saved of ['{"channel":"preview"}', 'not json', '{}', 'null']) {
      mocks.disk.file = saved;
      expect(readSavedUpdateChannel()).toBe('stable');
      expect(new DesktopUpdater().snapshot().channel).toBe('stable');
    }
    mocks.disk.file = undefined;
    expect(readSavedUpdateChannel()).toBe('stable');
  });

  test('a saved Beta choice survives restarts and checks the newest beta or stable release', async () => {
    const platform = process.platform;
    const arch = process.arch;
    const beta = feedManifestName(platform, arch, 'direct', 'beta');
    const stable = feedManifestName(platform, arch, 'direct', 'stable');
    const feedFetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify([
            { tag_name: 'v0.5.9-beta.2', assets: [{ name: beta }] },
            { tag_name: 'v0.5.9-beta.10', assets: [{ name: beta }] },
            { tag_name: 'v0.5.8', assets: [{ name: stable }] },
            { tag_name: 'v0.5.10-beta.1', draft: true, assets: [{ name: beta }] },
            { tag_name: 'v0.5.9-beta.11', assets: [{ name: 'unrelated.txt' }] },
            { tag_name: 'nightly', assets: [{ name: beta }] },
          ]),
          { status: 200 },
        ),
    );
    mocks.disk.file = '{"channel":"beta"}';
    const updater = new DesktopUpdater('direct', feedFetcher);
    expect(updater.snapshot().channel).toBe('beta');
    await updater.check();

    expect(mocks.autoUpdater.allowDowngrade).toBe(false);
    expect(mocks.autoUpdater.channel).toBe(`electron-beta-${platform}-${arch}`);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9-beta.10',
      channel: `electron-beta-${platform}-${arch}`,
      useMultipleRangeRequest: false,
    });
  });

  test('Beta rolls onto a stable release that passes the newest beta', async () => {
    const stable = feedManifestName(process.platform, process.arch, 'direct', 'stable');
    const beta = feedManifestName(process.platform, process.arch, 'direct', 'beta');
    const feed = await resolveBetaFeed(
      process.platform,
      process.arch,
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              { tag_name: 'v0.5.9-beta.3', assets: [{ name: beta }] },
              { tag_name: 'v0.5.9', assets: [{ name: stable }] },
            ]),
            { status: 200 },
          ),
      ),
    );
    expect(feed).toEqual({
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9',
      channel: `electron-stable-${process.platform}-${process.arch}`,
    });
  });

  test('Beta feed lookup fails closed on an unusable response', async () => {
    await expect(
      resolveBetaFeed(
        process.platform,
        process.arch,
        vi.fn(async () => new Response('', { status: 503 })),
      ),
    ).rejects.toThrow('503');
    await expect(
      resolveBetaFeed(
        process.platform,
        process.arch,
        vi.fn(async () => new Response('[]', { status: 200 })),
      ),
    ).rejects.toThrow('No beta or stable release');
  });

  test('switching channel persists, clears the old offer, and is refused mid-download', () => {
    const updater = new DesktopUpdater();
    emit('update-available', { version: '0.5.3', releaseNotes: 'Old' });

    expect(updater.setChannel('beta')).toMatchObject({ status: 'idle', channel: 'beta' });
    expect(updater.snapshot().availableVersion).toBeUndefined();
    expect(JSON.parse(mocks.disk.file!)).toEqual({ channel: 'beta' });
    expect(readSavedUpdateChannel()).toBe('beta');

    emit('download-progress', { percent: 10 });
    expect(updater.setChannel('stable').channel).toBe('beta');
    expect(readSavedUpdateChannel()).toBe('beta');
  });

  test('Store builds expose no channel and ignore switch requests', () => {
    const updater = new DesktopUpdater('microsoft-store', vi.fn());
    expect(updater.snapshot()).not.toHaveProperty('channel', expect.anything());
    updater.setChannel('beta');
    expect(mocks.fs.writeFileSync).not.toHaveBeenCalled();
    expect(feedManifestName(process.platform, process.arch, 'microsoft-store', 'beta')).toContain(
      'microsoft-store-stable',
    );
  });

  test('a channel file that cannot be written never blocks the updater', () => {
    mocks.fs.writeFileSync.mockImplementationOnce(() => {
      throw new Error('EPERM');
    });
    const updater = new DesktopUpdater();
    expect(() => updater.setChannel('beta')).not.toThrow();
    expect(updater.snapshot().channel).toBe('beta');
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
    expect(updater.snapshot()).toMatchObject({
      status: 'unsupported',
      unsupportedReason: 'development',
    });
    await updater.check();
    await updater.download();
    updater.install();
    expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
    expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
  });

  test('a development run shows and saves the channel without updating itself', async () => {
    mocks.app.isPackaged = false;
    mocks.disk.file = '{"channel":"beta"}';
    const updater = new DesktopUpdater();
    expect(updater.snapshot()).toMatchObject({ status: 'unsupported', channel: 'beta' });
    expect(updater.setChannel('stable')).toMatchObject({
      status: 'unsupported',
      channel: 'stable',
    });
    expect(mocks.disk.file).toContain('"stable"');
    await updater.check();
    expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
  });

  test('a package-managed install exposes no channel to switch', () => {
    vi.stubEnv('VOICESTUDIO_DISABLE_UPDATER', '1');
    try {
      expect(new DesktopUpdater().snapshot().channel).toBeUndefined();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  test('stays unsupported when a package manager owns the install', async () => {
    vi.stubEnv('VOICESTUDIO_DISABLE_UPDATER', '1');
    try {
      const updater = new DesktopUpdater();
      expect(updater.snapshot()).toMatchObject({
        status: 'unsupported',
        unsupportedReason: 'package-manager',
      });
      await updater.check();
      expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
      expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  test.each(['snap-store', 'mac-app-store'] as const)(
    'defers updates to the %s even when the updater environment override is absent',
    async (distribution) => {
      const updater = new DesktopUpdater(distribution);
      expect(updater.snapshot()).toMatchObject({
        status: 'unsupported',
        unsupportedReason: 'store',
      });
      await updater.check();
      await updater.download();
      updater.install();
      expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
      expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
      expect(mocks.autoUpdater.downloadUpdate).not.toHaveBeenCalled();
      expect(mocks.autoUpdater.quitAndInstall).not.toHaveBeenCalled();
    },
  );

  test('retains application-owned updates for the Microsoft Store EXE package', async () => {
    const manifest = feedManifestName(process.platform, process.arch, 'microsoft-store');
    const feedFetcher = vi.fn(
      async () =>
        new Response(JSON.stringify([{ tag_name: 'v0.5.7', assets: [{ name: manifest }] }]), {
          status: 200,
        }),
    );
    const updater = new DesktopUpdater('microsoft-store', feedFetcher);
    expect(updater.snapshot()).toMatchObject({ status: 'idle' });
    expect(updater.snapshot().unsupportedReason).toBeUndefined();
    expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalled();
    await updater.check();
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenCalledOnce();
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
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
          {
            tag_name: 'mobile-v0.5.7-beta.2',
            name: 'mobile-v0.5.7-beta.2 — VoiceStudio mobile',
            published_at: '2026-10-24T14:00:00Z',
            prerelease: true,
            body: 'Phone apps',
          },
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
    expect(feedManifestName('win32', 'x64')).toBe('electron-stable-win32-x64.yml');
    expect(feedManifestName('darwin', 'arm64')).toBe('electron-stable-darwin-arm64-mac.yml');
    expect(feedManifestName('linux', 'x64')).toBe('electron-stable-linux-x64-linux.yml');
    expect(feedManifestName('win32', 'x64', 'microsoft-store')).toBe(
      'electron-microsoft-store-stable-win32-x64.yml',
    );
  });

  test('rejects Store feed discovery when no release contains its manifest', async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify([{ tag_name: 'v0.5.8', assets: [] }]), { status: 200 }),
    );
    await expect(resolveUpdateFeedUrl('microsoft-store', 'win32', 'x64', fetcher)).rejects.toThrow(
      'No Microsoft Store update feed publishes',
    );
  });
});

describe('update IPC', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.ipcHandlers.clear();
    mocks.disk.file = undefined;
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

    const setChannel = mocks.ipcHandlers.get(UPDATE_CHANNELS.setChannel)!;
    const trustedEvent = { sender: webContents, senderFrame: webContents.mainFrame };
    expect(() => setChannel(trustedEvent, 'nightly')).toThrow('Unknown update channel');
    // A downloaded update pins the channel until it is installed.
    expect(setChannel(trustedEvent, 'beta')).toMatchObject({ channel: 'stable' });
    expect(() =>
      setChannel({ sender: {}, senderFrame: { url: 'https://attacker.invalid' } }, 'beta'),
    ).toThrow('Untrusted update request');

    dispose();
    expect(mocks.ipcMain.removeHandler).toHaveBeenCalledTimes(7);
  });
});

describe('offered update size', () => {
  const files = [
    { url: 'VoiceStudio-0.5.8-mac-arm64.zip', sha512: 'a', size: 280 },
    { url: 'VoiceStudio-0.5.8-mac-arm64.dmg', sha512: 'b', size: 281 },
  ];
  const linux = [
    { url: 'VoiceStudio-0.5.8-linux-x64.AppImage', sha512: 'a', size: 268 },
    { url: 'VoiceStudio-0.5.8-linux-x64.deb', sha512: 'b', size: 213 },
  ];

  test('counts only the installer this platform downloads, not every file in the feed', () => {
    expect(releaseSize({ files, path: files[0].url }, 'darwin')).toBe(280);
    expect(releaseSize({ files: linux, path: linux[0].url }, 'linux', '/opt/VoiceStudio.AppImage')).toBe(268);
    expect(releaseSize({ files: linux, path: linux[0].url }, 'linux', undefined)).toBe(213);
    const win = [{ url: 'VoiceStudio-0.5.8-win-x64.exe', sha512: 'a', size: 222 }];
    expect(releaseSize({ files: win, path: win[0].url }, 'win32')).toBe(222);
  });

  test('falls back to the feed path, then the first file, and never reports zero', () => {
    expect(releaseSize({ files: [{ url: 'x.bin', sha512: 'a', size: 5 }], path: 'x.bin' }, 'win32')).toBe(5);
    expect(releaseSize({ files: [], path: '' }, 'darwin')).toBeUndefined();
    expect(releaseSize({ files: [{ url: 'a.zip', sha512: 'a', size: 0 }], path: 'a.zip' }, 'darwin')).toBeUndefined();
  });
});
