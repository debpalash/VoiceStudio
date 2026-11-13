import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: any[]) => void>>();
  const ipcHandlers = new Map<string, (...args: any[]) => unknown>();
  const appHandlers = new Map<string, (...args: any[]) => void>();
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
    disableDifferentialDownload: false,
    on: vi.fn((event: string, listener: (...args: any[]) => void) => {
      const listeners = handlers.get(event) || new Set();
      listeners.add(listener);
      handlers.set(event, listeners);
    }),
    removeListener: vi.fn((event: string, listener: (...args: any[]) => void) => {
      handlers.get(event)?.delete(listener);
    }),
    setFeedURL: vi.fn(),
    checkForUpdates: vi.fn(async (): Promise<unknown> => undefined),
    downloadUpdate: vi.fn(async (_token?: unknown): Promise<unknown> => undefined),
    quitAndInstall: vi.fn(),
  };
  class CancellationToken {
    cancelled = false;
    cancel() {
      this.cancelled = true;
    }
  }
  return {
    CancellationToken,
    app: {
      isPackaged: true,
      getVersion: vi.fn(() => '0.5.2'),
      once: vi.fn((event: string, listener: (...args: any[]) => void) => {
        appHandlers.set(event, listener);
      }),
      getPath: vi.fn(() => 'C:\\VoiceStudio-test'),
    },
    autoUpdater,
    appHandlers,
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

vi.mock('electron-updater', () => ({
  default: { autoUpdater: mocks.autoUpdater, CancellationToken: mocks.CancellationToken },
}));

vi.mock('node:fs', () => ({ ...mocks.fs, default: mocks.fs }));

vi.mock('./trusted-renderer', () => ({ isTrustedRenderer: mocks.trusted }));

import {
  classifyUpdateError,
  compareAppVersions,
  DesktopUpdater,
  installsOnQuit,
  isTransientUpdateError,
  feedManifestName,
  listDesktopReleases,
  readSavedUpdateChannel,
  registerUpdateIpc,
  releaseSize,
  resolveBetaFeed,
  resolveUpdateFeedUrl,
  UPDATE_CHANNELS,
  updatePayloadExtension,
} from './updater';
import { GitHubAssetProvider } from './github-asset-provider';

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
    mocks.autoUpdater.disableDifferentialDownload = false;
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
    expect(mocks.autoUpdater.autoInstallOnAppQuit).toBe(installsOnQuit());
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
    const updater = new DesktopUpdater('microsoft-store', feedFetcher, []);
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
            {
              tag_name: 'v0.5.10-beta.1',
              draft: true,
              assets: [{ name: beta }],
            },
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
    expect(feed).toMatchObject({
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9',
      channel: `electron-stable-${process.platform}-${process.arch}`,
      tag: 'v0.5.9',
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

    expect(updater.setChannel('beta')).toMatchObject({
      status: 'idle',
      channel: 'beta',
    });
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

  test('classifies transient update failures', () => {
    for (const message of [
      '504 Gateway Timeout',
      'HttpError: 503',
      'Beta update feed lookup failed (502)',
      'getaddrinfo EAI_AGAIN github.com',
      'read ECONNRESET',
    ])
      expect(isTransientUpdateError(new Error(message)), message).toBe(true);
    expect(
      isTransientUpdateError(new TypeError('fetch failed', { cause: { code: 'ETIMEDOUT' } })),
    ).toBe(true);
    expect(isTransientUpdateError({ statusCode: 429 })).toBe(true);
    expect(
      isTransientUpdateError(new DOMException('The operation timed out.', 'TimeoutError')),
    ).toBe(true);
    expect(
      isTransientUpdateError(new DOMException('This operation was aborted', 'AbortError')),
    ).toBe(false);
    for (const message of [
      '404 Not Found',
      'sha512 checksum mismatch',
      'New version 0.5.9 is not signed',
    ])
      expect(isTransientUpdateError(new Error(message)), message).toBe(false);
  });

  test('retries a transient check failure while staying in checking', async () => {
    const updater = new DesktopUpdater('direct', fetch, [0, 0]);
    const observed: string[] = [];
    updater.subscribe((state) => observed.push(state.status));
    mocks.autoUpdater.checkForUpdates
      .mockRejectedValueOnce(new Error('504 Gateway Timeout'))
      .mockRejectedValueOnce(new Error('getaddrinfo ENOTFOUND github.com'));
    expect(await updater.check()).toMatchObject({ status: 'idle' });
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledTimes(3);
    expect(observed).not.toContain('error');
  });

  test('gives up after three attempts and the API fallback, reporting the last error', async () => {
    const feedFetcher = vi.fn(async () => new Response('', { status: 503 }));
    const updater = new DesktopUpdater('direct', feedFetcher, [0, 0]);
    mocks.autoUpdater.checkForUpdates.mockRejectedValue(new Error('503 Service Unavailable'));
    expect(await updater.check()).toMatchObject({
      status: 'error',
      error: 'Latest release lookup failed (503)',
    });
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledTimes(3);
    expect(feedFetcher).toHaveBeenCalledOnce();
  });

  test('a failing autoUpdater error event does not end a retrying check', async () => {
    const updater = new DesktopUpdater('direct', fetch, [0]);
    mocks.autoUpdater.checkForUpdates
      .mockImplementationOnce(async () => {
        emit('error', new Error('504 Gateway Timeout'));
        throw new Error('504 Gateway Timeout');
      })
      .mockResolvedValueOnce(undefined);
    expect((await updater.check()).status).toBe('idle');
  });

  test('does not retry permanent failures', async () => {
    const updater = new DesktopUpdater('direct', fetch, [0, 0]);
    mocks.autoUpdater.checkForUpdates.mockRejectedValue(new Error('404 Not Found'));
    expect((await updater.check()).status).toBe('error');
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
  });

  test('retries the Beta releases lookup', async () => {
    const beta = feedManifestName(process.platform, process.arch, 'direct', 'beta');
    const feedFetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('bad gateway', { status: 502 }))
      .mockResolvedValue(
        new Response(JSON.stringify([{ tag_name: 'v0.5.9-beta.1', assets: [{ name: beta }] }]), {
          status: 200,
        }),
      );
    mocks.disk.file = '{"channel":"beta"}';
    const updater = new DesktopUpdater('direct', feedFetcher, [0]);
    expect((await updater.check()).status).toBe('idle');
    expect(feedFetcher).toHaveBeenCalledTimes(2);
    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledOnce();
  });

  test('retries a download once on a transient error', async () => {
    // Store builds have no asset-route fallback, so this isolates the retry.
    const updater = new DesktopUpdater('microsoft-store', vi.fn(), [0, 0]);
    emit('update-available', { version: '0.5.3' });
    mocks.autoUpdater.downloadUpdate.mockRejectedValue(new Error('504 Gateway Timeout'));
    expect((await updater.download()).status).toBe('error');
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledTimes(2);
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
    expect(updater.snapshot()).toMatchObject({
      status: 'unsupported',
      channel: 'beta',
    });
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

  test('checks only after the installed-download cleanup, so it never races a download', async () => {
    let locate!: (helper: { cacheDirForPendingUpdate: string }) => void;
    const getOrCreateDownloadHelper = vi.fn(
      () => new Promise<{ cacheDirForPendingUpdate: string }>((resolve) => (locate = resolve)),
    );
    Object.assign(mocks.autoUpdater, { getOrCreateDownloadHelper });
    try {
      const updater = new DesktopUpdater();
      expect(getOrCreateDownloadHelper).toHaveBeenCalledTimes(1);
      const check = updater.check();
      await Promise.resolve();
      expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
      locate({ cacheDirForPendingUpdate: '/nonexistent/voicestudio-updater-test/pending' });
      await check;
      expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledTimes(1);
    } finally {
      delete (mocks.autoUpdater as { getOrCreateDownloadHelper?: unknown })
        .getOrCreateDownloadHelper;
    }
  });

  test('skips the installed-download cleanup when updates are unsupported', () => {
    const getOrCreateDownloadHelper = vi.fn();
    Object.assign(mocks.autoUpdater, { getOrCreateDownloadHelper });
    mocks.app.isPackaged = false;
    try {
      new DesktopUpdater();
      expect(getOrCreateDownloadHelper).not.toHaveBeenCalled();
    } finally {
      delete (mocks.autoUpdater as { getOrCreateDownloadHelper?: unknown })
        .getOrCreateDownloadHelper;
    }
  });
});

describe('release-download outage fallback', () => {
  const channel = `electron-stable-${process.platform}-${process.arch}`;
  const manifest = feedManifestName(process.platform, process.arch, 'direct', 'stable');
  const latestRelease = () =>
    new Response(
      JSON.stringify({
        tag_name: 'v0.5.9',
        assets: [
          { name: manifest, id: 11 },
          { name: 'VoiceStudio-0.5.9.zip', id: 12 },
          { name: 'bogus', id: 'x' },
        ],
      }),
      { status: 200 },
    );
  const gatewayTimeout = () => Object.assign(new Error('504 Gateway Timeout'), { statusCode: 504 });

  beforeEach(() => {
    mocks.handlers.clear();
    mocks.disk.file = undefined;
    mocks.app.isPackaged = true;
    mocks.autoUpdater.disableDifferentialDownload = false;
    vi.clearAllMocks();
    mocks.autoUpdater.checkForUpdates.mockReset().mockResolvedValue(undefined);
    mocks.autoUpdater.downloadUpdate.mockReset().mockResolvedValue(undefined);
  });

  test('a 504 manifest check retries once through the API asset route', async () => {
    const feedFetcher = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
      latestRelease(),
    );
    const updater = new DesktopUpdater('direct', feedFetcher, [0, 0]);
    mocks.autoUpdater.checkForUpdates
      .mockRejectedValueOnce(gatewayTimeout())
      .mockRejectedValueOnce(gatewayTimeout())
      .mockRejectedValueOnce(gatewayTimeout());

    expect(await updater.check()).toMatchObject({ status: 'idle' });

    expect(mocks.autoUpdater.checkForUpdates).toHaveBeenCalledTimes(4);
    // One API request per fallback check.
    expect(feedFetcher).toHaveBeenCalledOnce();
    expect(feedFetcher.mock.calls[0][0]).toBe(
      'https://api.github.com/repos/debpalash/VoiceStudio/releases/latest',
    );
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'custom',
      updateProvider: GitHubAssetProvider,
      url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9',
      channel,
      assets: { [manifest]: 11, 'VoiceStudio-0.5.9.zip': 12 },
      payloadExtension: updatePayloadExtension(),
    });
    expect(mocks.autoUpdater.disableDifferentialDownload).toBe(true);

    // GitHub recovers: the next check is the normal feed with patch downloads.
    await updater.check();
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith({
      provider: 'generic',
      url: 'https://github.com/debpalash/VoiceStudio/releases/latest/download',
      channel,
      useMultipleRangeRequest: false,
    });
    expect(mocks.autoUpdater.disableDifferentialDownload).toBe(false);
    expect(feedFetcher).toHaveBeenCalledOnce();
  });

  test('a healthy feed never touches the API or disables patch downloads', async () => {
    const feedFetcher = vi.fn();
    const updater = new DesktopUpdater('direct', feedFetcher, [0, 0]);
    await updater.check();
    emit('update-available', { version: '0.5.9' });
    await updater.download();
    expect(feedFetcher).not.toHaveBeenCalled();
    expect(mocks.autoUpdater.disableDifferentialDownload).toBe(false);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenCalledTimes(2);
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith(
      expect.objectContaining({ provider: 'generic' }),
    );
  });

  test('Beta reuses its release lookup for the fallback', async () => {
    const beta = feedManifestName(process.platform, process.arch, 'direct', 'beta');
    const feedFetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify([{ tag_name: 'v0.5.9-beta.1', assets: [{ name: beta, id: 21 }] }]),
          { status: 200 },
        ),
    );
    mocks.disk.file = '{"channel":"beta"}';
    const updater = new DesktopUpdater('direct', feedFetcher, []);
    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(gatewayTimeout());
    expect((await updater.check()).status).toBe('idle');
    expect(feedFetcher).toHaveBeenCalledOnce();
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith(
      expect.objectContaining({
        provider: 'custom',
        url: 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9-beta.1',
        assets: { [beta]: 21 },
      }),
    );
  });

  test('a 504 download re-reads the offer through the asset route without leaving downloading', async () => {
    const feedFetcher = vi.fn(async () => latestRelease());
    const updater = new DesktopUpdater('direct', feedFetcher, [0]);
    const observed: string[] = [];
    emit('update-available', { version: '0.5.9' });
    updater.subscribe((state) => observed.push(state.status));
    mocks.autoUpdater.downloadUpdate
      .mockRejectedValueOnce(gatewayTimeout())
      .mockRejectedValueOnce(gatewayTimeout());
    mocks.autoUpdater.checkForUpdates.mockImplementationOnce(async () => {
      emit('checking-for-update');
      emit('update-available', { version: '0.5.9' });
      return { isUpdateAvailable: true, updateInfo: { version: '0.5.9' } };
    });

    expect((await updater.download()).status).toBe('downloading');

    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledTimes(3);
    expect(feedFetcher).toHaveBeenCalledOnce();
    expect(mocks.autoUpdater.setFeedURL).toHaveBeenLastCalledWith(
      expect.objectContaining({ provider: 'custom', updateProvider: GitHubAssetProvider }),
    );
    expect(mocks.autoUpdater.disableDifferentialDownload).toBe(true);
    expect(observed.every((status) => status === 'downloading')).toBe(true);
  });

  test('a download fallback refuses a release that no longer offers the same version', async () => {
    const updater = new DesktopUpdater(
      'direct',
      vi.fn(async () => latestRelease()),
      [],
    );
    emit('update-available', { version: '0.5.8' });
    mocks.autoUpdater.downloadUpdate.mockRejectedValueOnce(gatewayTimeout());
    mocks.autoUpdater.checkForUpdates.mockResolvedValueOnce({
      isUpdateAvailable: true,
      updateInfo: { version: '0.5.9' },
    });
    expect(await updater.download()).toMatchObject({
      status: 'error',
      error: expect.stringContaining('check for updates again'),
    });
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledOnce();
  });

  test('404 and checksum failures never use the asset route', async () => {
    const feedFetcher = vi.fn();
    const updater = new DesktopUpdater('direct', feedFetcher, [0, 0]);
    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(
      Object.assign(new Error('Cannot find channel: 404 Not Found'), { statusCode: 404 }),
    );
    expect((await updater.check()).status).toBe('error');

    emit('update-available', { version: '0.5.9' });
    mocks.autoUpdater.downloadUpdate.mockRejectedValueOnce(
      new Error('sha512 checksum mismatch, expected abc, got def'),
    );
    expect((await updater.download()).status).toBe('error');

    expect(feedFetcher).not.toHaveBeenCalled();
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledOnce();
    expect(mocks.autoUpdater.setFeedURL).not.toHaveBeenCalledWith(
      expect.objectContaining({ provider: 'custom' }),
    );
    expect(mocks.autoUpdater.disableDifferentialDownload).toBe(false);
  });

  test('background checks report failure to the check schedule', async () => {
    const updater = new DesktopUpdater('direct', vi.fn(), []);
    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(new Error('offline'));
    expect(await updater.checkInBackground()).toBe(false);
    expect(updater.snapshot().status).toBe('idle');
    expect(await updater.checkInBackground()).toBe(true);
  });
});

describe('download stall watchdog', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.app.isPackaged = true;
    vi.clearAllMocks();
    mocks.autoUpdater.downloadUpdate.mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // Store builds skip the asset-route fallback, isolating the watchdog.
  function startDownload(retryDelays: number[] = []) {
    const updater = new DesktopUpdater('microsoft-store', vi.fn(), retryDelays);
    emit('update-available', { version: '0.5.9' });
    return { updater, download: updater.download() };
  }

  test('cancels a download that receives no data for 45 seconds and reports a transient error', async () => {
    const tokens: Array<{ cancelled: boolean }> = [];
    mocks.autoUpdater.downloadUpdate.mockImplementation((token: any) => {
      tokens.push(token);
      return new Promise(() => undefined);
    });
    const { download } = startDownload([0]);
    await vi.advanceTimersByTimeAsync(44_999);
    expect(tokens[0].cancelled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(tokens[0].cancelled).toBe(true);
    // The stall counts as transient, so the usual single retry runs.
    await vi.runAllTimersAsync();
    const state = await download;
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledTimes(2);
    expect(tokens[1].cancelled).toBe(true);
    expect(state).toMatchObject({ status: 'error', error: expect.stringContaining('45 seconds') });
    expect(mocks.autoUpdater.removeListener).toHaveBeenCalled();
  });

  test('keeps a download that is still receiving data', async () => {
    let finish!: () => void;
    let token: { cancelled: boolean } | undefined;
    mocks.autoUpdater.downloadUpdate.mockImplementation((value: any) => {
      token = value;
      return new Promise((resolve) => (finish = () => resolve(['file'])));
    });
    const { download } = startDownload();
    for (let second = 1; second <= 6; second += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      emit('download-progress', {
        percent: second * 10,
        transferred: second * 1_000,
        total: 10_000,
      });
    }
    // A progress event without new bytes does not count as progress.
    await vi.advanceTimersByTimeAsync(30_000);
    emit('download-progress', { percent: 60, transferred: 6_000, total: 10_000 });
    expect(token?.cancelled).toBe(false);
    finish();
    expect((await download).status).toBe('downloading');
    expect(token?.cancelled).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  test('a repeated progress value does not keep a stalled download alive', async () => {
    let token: { cancelled: boolean } | undefined;
    mocks.autoUpdater.downloadUpdate.mockImplementation((value: any) => {
      token = value;
      return new Promise(() => undefined);
    });
    const { download } = startDownload();
    emit('download-progress', { percent: 10, transferred: 1_000, total: 10_000 });
    await vi.advanceTimersByTimeAsync(30_000);
    emit('download-progress', { percent: 10, transferred: 1_000, total: 10_000 });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(token?.cancelled).toBe(true);
    expect((await download).status).toBe('error');
  });

  test('stops watching once every byte is in (macOS then hands the file to Squirrel)', async () => {
    let token: { cancelled: boolean } | undefined;
    let finish!: () => void;
    mocks.autoUpdater.downloadUpdate.mockImplementation((value: any) => {
      token = value;
      return new Promise<void>((resolve) => (finish = resolve));
    });
    const { download } = startDownload();
    emit('download-progress', { percent: 100, transferred: 10_000, total: 10_000 });
    emit('update-downloaded', { version: '0.5.9' });
    await vi.advanceTimersByTimeAsync(120_000);
    expect(token?.cancelled).toBe(false);
    finish();
    expect((await download).status).toBe('downloaded');
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
        new Response(JSON.stringify([{ tag_name: 'v0.5.8', assets: [] }]), {
          status: 200,
        }),
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
    mocks.appHandlers.clear();
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
      isDestroyed: () => false,
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
    const trustedEvent = {
      sender: webContents,
      senderFrame: webContents.mainFrame,
    };
    expect(() => setChannel(trustedEvent, 'nightly')).toThrow('Unknown update channel');
    // A downloaded update pins the channel until it is installed.
    expect(setChannel(trustedEvent, 'beta')).toMatchObject({
      channel: 'stable',
    });
    expect(() =>
      setChannel({ sender: {}, senderFrame: { url: 'https://attacker.invalid' } }, 'beta'),
    ).toThrow('Untrusted update request');

    const setAutoDownload = mocks.ipcHandlers.get(UPDATE_CHANNELS.setAutoDownload)!;
    expect(() => setAutoDownload(trustedEvent, 'yes')).toThrow('Invalid automatic download');
    expect(setAutoDownload(trustedEvent, false)).toMatchObject({ autoDownload: false });
    expect(mocks.ipcHandlers.get(UPDATE_CHANNELS.later)!(trustedEvent)).toMatchObject({
      dismissedVersion: '0.5.3',
    });

    dispose();
    mocks.appHandlers.get('will-quit')?.();
    expect(mocks.ipcMain.removeHandler).toHaveBeenCalledTimes(9);
  });

  test('keeps state readable while Restart to update stops the backend', async () => {
    const updater = new DesktopUpdater();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
    const webContents = {
      mainFrame: { url: 'app://voicestudio/index.html' },
      send: vi.fn(),
      isDestroyed: () => false,
    };
    const owner = { webContents, isDestroyed: () => false } as any;
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    let finishShutdown!: () => void;
    const beforeInstall = vi.fn(() => {
      // index.ts tears down updater resources before awaiting backend shutdown,
      // while the renderer is still mounted and can refresh update state.
      dispose();
      dispose(); // Repeated quit preparation must not register cleanup twice.
      return new Promise<void>((resolve) => (finishShutdown = resolve));
    });
    const dispose = registerUpdateIpc(updater, () => owner, beforeInstall);
    const invoke = (channel: string) => {
      const handler = mocks.ipcHandlers.get(channel);
      if (!handler) throw new Error(`No handler registered for '${channel}'`);
      return handler(event);
    };

    const install = invoke(UPDATE_CHANNELS.install);
    expect(() => invoke(UPDATE_CHANNELS.getState)).not.toThrow();
    expect(invoke(UPDATE_CHANNELS.getState)).toMatchObject({ status: 'downloaded' });
    expect(mocks.app.once).toHaveBeenCalledTimes(1);
    expect(() =>
      mocks.ipcHandlers.get(UPDATE_CHANNELS.getState)!({
        sender: {},
        senderFrame: { url: 'https://attacker.invalid' },
      }),
    ).toThrow('Untrusted update request');
    finishShutdown();
    await install;
    expect(mocks.autoUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
    expect(() => invoke(UPDATE_CHANNELS.getState)).not.toThrow();

    mocks.appHandlers.get('will-quit')?.();
    dispose();
    expect(() => invoke(UPDATE_CHANNELS.getState)).toThrow('No handler registered');
    expect(mocks.ipcMain.removeHandler).toHaveBeenCalledTimes(9);
  });

  test('removes update IPC immediately when no renderer remains', () => {
    const updater = new DesktopUpdater();
    const dispose = registerUpdateIpc(updater, () => null, vi.fn());
    dispose();
    dispose();
    expect(mocks.ipcMain.removeHandler).toHaveBeenCalledTimes(9);
    expect(mocks.appHandlers.has('will-quit')).toBe(false);
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
    expect(
      releaseSize({ files: linux, path: linux[0].url }, 'linux', '/opt/VoiceStudio.AppImage'),
    ).toBe(268);
    expect(releaseSize({ files: linux, path: linux[0].url }, 'linux', undefined)).toBe(213);
    const win = [{ url: 'VoiceStudio-0.5.8-win-x64.exe', sha512: 'a', size: 222 }];
    expect(releaseSize({ files: win, path: win[0].url }, 'win32')).toBe(222);
  });

  test('falls back to the feed path, then the first file, and never reports zero', () => {
    expect(
      releaseSize({ files: [{ url: 'x.bin', sha512: 'a', size: 5 }], path: 'x.bin' }, 'win32'),
    ).toBe(5);
    expect(releaseSize({ files: [], path: '' }, 'darwin')).toBeUndefined();
    expect(
      releaseSize({ files: [{ url: 'a.zip', sha512: 'a', size: 0 }], path: 'a.zip' }, 'darwin'),
    ).toBeUndefined();
  });
});

describe('background download and install on quit', () => {
  const offer = (version = '0.5.3') =>
    mocks.autoUpdater.checkForUpdates.mockImplementation(async () => {
      emit('update-available', { version, releaseNotes: null });
      return { isUpdateAvailable: true, updateInfo: { version } };
    });

  beforeEach(() => {
    mocks.handlers.clear();
    mocks.ipcHandlers.clear();
    mocks.disk.file = undefined;
    mocks.app.isPackaged = true;
    vi.clearAllMocks();
    mocks.autoUpdater.checkForUpdates.mockResolvedValue(undefined);
    mocks.autoUpdater.downloadUpdate.mockResolvedValue(undefined);
  });

  test('a found update downloads in the background by default', async () => {
    const updater = new DesktopUpdater('direct', vi.fn(), []);
    expect(updater.snapshot()).toMatchObject({ autoDownload: true });
    offer();
    mocks.autoUpdater.downloadUpdate.mockImplementation(async () => {
      emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
      return [];
    });

    expect(await updater.checkInBackground()).toBe(true);
    expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledOnce();
    expect(updater.snapshot()).toMatchObject({ status: 'downloaded', availableVersion: '0.5.3' });
  });

  test('with automatic downloads off a found update waits for a click', async () => {
    mocks.disk.file = '{"autoDownload":false}';
    const updater = new DesktopUpdater('direct', vi.fn(), []);
    offer();

    await updater.checkInBackground();
    expect(updater.snapshot()).toMatchObject({ status: 'available', autoDownload: false });
    expect(mocks.autoUpdater.downloadUpdate).not.toHaveBeenCalled();

    // Turning it back on downloads the offer already found, and is saved.
    updater.setAutoDownload(true);
    await vi.waitFor(() => expect(mocks.autoUpdater.downloadUpdate).toHaveBeenCalledOnce());
    expect(JSON.parse(mocks.disk.file!)).toMatchObject({ autoDownload: true });
  });

  test('a network failure of a background download stays quiet and retries sooner', async () => {
    const offline = vi.fn(async () => {
      throw new Error('getaddrinfo ENOTFOUND api.github.com');
    });
    const updater = new DesktopUpdater('direct', offline as unknown as typeof fetch, []);
    offer();
    mocks.autoUpdater.downloadUpdate.mockRejectedValue(new Error('ENETUNREACH'));

    expect(await updater.checkInBackground()).toBe(false);
    expect(updater.snapshot()).toMatchObject({ status: 'available', availableVersion: '0.5.3' });
    expect(updater.snapshot().error).toBeUndefined();
  });

  test('a checksum failure is shown as a verification failure, even in the background', async () => {
    const updater = new DesktopUpdater('direct', vi.fn(), []);
    offer();
    mocks.autoUpdater.downloadUpdate.mockRejectedValue(
      new Error('sha512 checksum mismatch, expected abc, got def'),
    );

    expect(await updater.checkInBackground()).toBe(false);
    expect(updater.snapshot()).toMatchObject({ status: 'error', errorKind: 'verify' });
  });

  test('a verification failure after a network fallback still shows', async () => {
    const offline = vi.fn(async () => {
      throw new Error('getaddrinfo ENOTFOUND api.github.com');
    });
    const updater = new DesktopUpdater('direct', offline as unknown as typeof fetch, []);
    offer();
    // The release host times out, then the asset route serves a corrupt file.
    vi.spyOn(updater as any, 'downloadThroughAssetRoute').mockRejectedValue(
      new Error('sha512 checksum mismatch, expected abc, got def'),
    );
    mocks.autoUpdater.downloadUpdate.mockRejectedValue(new Error('504 Gateway Timeout'));

    expect(await updater.checkInBackground()).toBe(false);
    expect(updater.snapshot()).toMatchObject({ status: 'error', errorKind: 'verify' });
  });

  test('installs on quit where the package replaces itself silently', () => {
    expect(installsOnQuit('darwin', undefined)).toBe(true);
    expect(installsOnQuit('win32', undefined)).toBe(true);
    expect(installsOnQuit('linux', '/home/me/VoiceStudio.AppImage')).toBe(true);
    // A .deb needs an administrator prompt: restart-to-install only.
    expect(installsOnQuit('linux', undefined)).toBe(false);
  });

  test('records the version a quit installs, and skips install for a relaunch', () => {
    const updater = new DesktopUpdater();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });

    updater.prepareForQuit(true);
    expect(JSON.parse(mocks.disk.file ?? '{}').pendingVersion).toBe(
      installsOnQuit() ? '0.5.3' : undefined,
    );

    updater.prepareForQuit(false);
    expect(mocks.autoUpdater.autoInstallOnAppQuit).toBe(false);
  });

  test('restart records the pending version before quitAndInstall', () => {
    const updater = new DesktopUpdater();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
    mocks.autoUpdater.quitAndInstall.mockImplementationOnce(() => {
      expect(JSON.parse(mocks.disk.file!).pendingVersion).toBe('0.5.3');
    });
    updater.install();
    expect(mocks.autoUpdater.quitAndInstall).toHaveBeenCalledOnce();
  });

  test('an update that did not apply is reported once and waits for the user', async () => {
    mocks.disk.file = '{"channel":"beta","pendingVersion":"99.0.0"}';
    const updater = new DesktopUpdater();
    expect(updater.snapshot()).toMatchObject({
      status: 'error',
      errorKind: 'not-applied',
      availableVersion: '99.0.0',
    });
    expect(JSON.parse(mocks.disk.file)).toEqual({ channel: 'beta' });

    // Background checks do not loop it through another download and install.
    await updater.checkInBackground();
    expect(mocks.autoUpdater.checkForUpdates).not.toHaveBeenCalled();
    expect(updater.snapshot().errorKind).toBe('not-applied');
  });

  test('an update that applied clears the pending version quietly', () => {
    mocks.disk.file = JSON.stringify({ pendingVersion: __APP_VERSION__ });
    const updater = new DesktopUpdater();
    expect(updater.snapshot()).toMatchObject({ status: 'idle' });
    expect(updater.snapshot().errorKind).toBeUndefined();
    expect(JSON.parse(mocks.disk.file)).toEqual({});
  });

  test('"Later" remembers the version across launches', () => {
    const updater = new DesktopUpdater();
    emit('update-downloaded', { version: '0.5.3', releaseNotes: null });
    expect(updater.later()).toMatchObject({ dismissedVersion: '0.5.3' });
    expect(new DesktopUpdater().snapshot().dismissedVersion).toBe('0.5.3');
  });

  test('development runs never download or install on their own', async () => {
    mocks.app.isPackaged = false;
    const updater = new DesktopUpdater();
    expect(updater.snapshot()).toMatchObject({ status: 'unsupported' });
    expect(updater.snapshot().autoDownload).toBeUndefined();
    updater.setAutoDownload(true);
    updater.prepareForQuit(true);
    expect(mocks.fs.writeFileSync).not.toHaveBeenCalled();
  });
});

describe('update error classification', () => {
  beforeEach(() => {
    mocks.handlers.clear();
    mocks.disk.file = undefined;
    mocks.app.isPackaged = true;
    vi.clearAllMocks();
    mocks.autoUpdater.checkForUpdates.mockResolvedValue(undefined);
    mocks.autoUpdater.downloadUpdate.mockResolvedValue(undefined);
    mocks.autoUpdater.quitAndInstall.mockImplementation(() => undefined);
  });

  test('names the failed step', () => {
    expect(classifyUpdateError(new Error('504'), 'check')).toBe('check');
    expect(classifyUpdateError(new Error('HttpError: 404'), 'download')).toBe('download');
    expect(classifyUpdateError(new Error('sha512 checksum mismatch'), 'download')).toBe('verify');
    expect(
      classifyUpdateError(
        new Error('New version 0.5.9 is not signed by the application owner'),
        'download',
      ),
    ).toBe('verify');
    expect(
      classifyUpdateError(
        new Error('Update failed', { cause: new Error('Code signature did not pass validation') }),
        'install',
      ),
    ).toBe('verify');
    expect(classifyUpdateError(new Error('spawn EACCES'), 'install')).toBe('install');
  });

  test('reports check, download and install failures with their step', async () => {
    const updater = new DesktopUpdater('direct', vi.fn(), []);
    mocks.autoUpdater.checkForUpdates.mockRejectedValueOnce(new Error('feed unavailable'));
    expect(await updater.check()).toMatchObject({ status: 'error', errorKind: 'check' });

    updater.dismiss();
    emit('update-available', { version: '0.5.3' });
    mocks.autoUpdater.downloadUpdate.mockRejectedValueOnce(new Error('HttpError: 404'));
    expect(await updater.download()).toMatchObject({ status: 'error', errorKind: 'download' });

    emit('update-downloaded', { version: '0.5.3' });
    mocks.autoUpdater.quitAndInstall.mockImplementationOnce(() => {
      throw new Error('spawn EACCES');
    });
    updater.install();
    expect(updater.snapshot()).toMatchObject({ status: 'error', errorKind: 'install' });
    // A failed start is not reported as "did not apply" next launch.
    expect(JSON.parse(mocks.disk.file!).pendingVersion).toBeUndefined();

    emit('update-downloaded', { version: '0.5.3' });
    emit('error', new Error('installer exited with code 2'));
    expect(updater.snapshot()).toMatchObject({ status: 'error', errorKind: 'install' });
  });

  test('orders release versions', () => {
    expect(compareAppVersions('0.5.8-beta.3', '0.5.8')).toBe(-1);
    expect(compareAppVersions('0.5.8', '0.5.8')).toBe(0);
    expect(compareAppVersions('0.5.9', '0.5.8')).toBe(1);
  });
});
