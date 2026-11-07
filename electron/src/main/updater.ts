import { app, BrowserWindow, ipcMain } from 'electron';
import updaterPackage, { type ProgressInfo, type UpdateInfo } from 'electron-updater';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { isTrustedRenderer } from './trusted-renderer';
import type { UpdateReleaseInfo, UpdateState } from '../preload/index.d';
import { sendToLiveWindow } from './window-safety';
import { applicationUpdatesUnsupportedReason, CURRENT_DISTRIBUTION } from './distribution';
import type { DistributionTarget } from '../../../config/distributions.mjs';

// electron-updater is CommonJS. Electron executes our main bundle as ESM, so
// named runtime imports fail before the app can create a window.
const { autoUpdater } = updaterPackage;

// Two update channels, both fed by tagged GitHub releases cut from main:
// Stable follows `vX.Y.Z` (the `latest` release); Beta follows `vX.Y.Z-beta.N`
// prereleases and rolls onto the next stable once it passes them. GitHub's
// `latest/download` never serves a prerelease, so Beta resolves its release
// through the releases API (see resolveBetaFeed).
export type UpdateChannel = 'stable' | 'beta';
const STABLE_FEED = 'https://github.com/debpalash/VoiceStudio/releases/latest/download';
const RELEASES_API = 'https://api.github.com/repos/debpalash/VoiceStudio/releases?per_page=30';

function feedChannelName(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  distribution: DistributionTarget = CURRENT_DISTRIBUTION,
  channel: UpdateChannel = 'stable',
): string {
  // Store builds are stable-only: the Store listing is the beta gate.
  const prefix =
    distribution === 'microsoft-store' ? 'electron-microsoft-store-stable' : `electron-${channel}`;
  return `${prefix}-${platform}-${arch}`;
}

export function feedManifestName(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  distribution: DistributionTarget = CURRENT_DISTRIBUTION,
  channel: UpdateChannel = 'stable',
): string {
  const suffix = platform === 'darwin' ? '-mac' : platform === 'linux' ? '-linux' : '';
  return `${feedChannelName(platform, arch, distribution, channel)}${suffix}.yml`;
}

const RELEASE_DOWNLOAD = 'https://github.com/debpalash/VoiceStudio/releases/download';
const RELEASE_TAG = /^v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$/;

/** Orders `vX.Y.Z-beta.N` below `vX.Y.Z`, as semver does. */
function releaseRank(tag: string): number[] | null {
  const match = RELEASE_TAG.exec(tag);
  if (!match) return null;
  return [
    Number(match[1]),
    Number(match[2]),
    Number(match[3]),
    match[4] === undefined ? Number.MAX_SAFE_INTEGER : Number(match[4]),
  ];
}

/**
 * Beta follows the newest stable-or-beta release that publishes this platform's
 * manifest. When a stable release is the newest, Beta reads the stable manifest
 * from it, so beta users land on the final build instead of lingering on a beta.
 */
export async function resolveBetaFeed(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  fetcher: typeof fetch = fetch,
): Promise<{ url: string; channel: string }> {
  const response = await fetcher(RELEASES_API, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'VoiceStudio' },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Beta update feed lookup failed (${response.status})`);
  const releases: unknown = await response.json();
  if (!Array.isArray(releases)) throw new Error('Beta update feed lookup returned invalid data');
  const candidates = releases.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const item = value as Record<string, unknown>;
    const tag = typeof item.tag_name === 'string' ? item.tag_name : '';
    const rank = releaseRank(tag);
    if (!rank || item.draft === true || !Array.isArray(item.assets)) return [];
    const kind = rank[3] === Number.MAX_SAFE_INTEGER ? 'stable' : 'beta';
    const manifest = feedManifestName(platform, arch, 'direct', kind);
    const published = item.assets.some(
      (asset) => asset && (asset as Record<string, unknown>).name === manifest,
    );
    return published
      ? [{ tag, rank, channel: feedChannelName(platform, arch, 'direct', kind) }]
      : [];
  });
  candidates.sort((left, right) => {
    for (let index = 0; index < left.rank.length; index += 1) {
      const difference = right.rank[index] - left.rank[index];
      if (difference) return difference;
    }
    return 0;
  });
  const newest = candidates[0];
  if (!newest)
    throw new Error('No beta or stable release publishes an update feed for this device');
  return { url: `${RELEASE_DOWNLOAD}/${encodeURIComponent(newest.tag)}`, channel: newest.channel };
}

const CHANNEL_FILE = 'updates.json';

/** Anything but an explicit "beta" (including the retired "preview") is Stable. */
export function readSavedUpdateChannel(userData: string = app.getPath('userData')): UpdateChannel {
  try {
    const saved: unknown = JSON.parse(readFileSync(join(userData, CHANNEL_FILE), 'utf8'));
    return saved && (saved as Record<string, unknown>).channel === 'beta' ? 'beta' : 'stable';
  } catch {
    return 'stable';
  }
}

function saveUpdateChannel(channel: UpdateChannel, userData: string = app.getPath('userData')) {
  try {
    writeFileSync(join(userData, CHANNEL_FILE), JSON.stringify({ channel }));
  } catch {
    // Best effort: the choice still applies for this session.
  }
}

export async function resolveUpdateFeedUrl(
  distribution: DistributionTarget,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  if (distribution !== 'microsoft-store') return STABLE_FEED;
  const manifest = feedManifestName(platform, arch, distribution);
  const response = await fetcher(RELEASES_API, {
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'VoiceStudio',
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Store update feed lookup failed (${response.status})`);
  const releases: unknown = await response.json();
  if (!Array.isArray(releases)) throw new Error('Store update feed lookup returned invalid data');
  const matchingReleases = releases.flatMap(
    (
      value,
    ): Array<{
      release: Record<string, unknown>;
      version: [number, number, number];
    }> => {
      if (!value || typeof value !== 'object') return [];
      const item = value as Record<string, unknown>;
      const version =
        typeof item.tag_name === 'string' ? item.tag_name.match(/^v(\d+)\.(\d+)\.(\d+)$/) : null;
      const publishesManifest =
        Array.isArray(item.assets) &&
        item.assets.some(
          (asset) =>
            asset &&
            typeof asset === 'object' &&
            (asset as Record<string, unknown>).name === manifest,
        );
      return version && publishesManifest
        ? [{ release: item, version: [Number(version[1]), Number(version[2]), Number(version[3])] }]
        : [];
    },
  );
  matchingReleases.sort((left, right) => {
    for (let index = 0; index < left.version.length; index += 1) {
      const difference = right.version[index] - left.version[index];
      if (difference) return difference;
    }
    return 0;
  });
  const release = matchingReleases[0]?.release;
  if (!release) throw new Error(`No Microsoft Store update feed publishes ${manifest}`);
  return `https://github.com/debpalash/VoiceStudio/releases/download/${encodeURIComponent(
    release.tag_name as string,
  )}`;
}

export const UPDATE_CHANNELS = {
  getState: 'updates:getState',
  check: 'updates:check',
  download: 'updates:download',
  dismiss: 'updates:dismiss',
  install: 'updates:install',
  listReleases: 'updates:listReleases',
  setChannel: 'updates:setChannel',
  state: 'updates:state',
} as const;

export async function listDesktopReleases(
  fetcher: typeof fetch = fetch,
): Promise<UpdateReleaseInfo[]> {
  const response = await fetcher(RELEASES_API, {
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'VoiceStudio',
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Releases request failed (${response.status})`);
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) throw new Error('Releases response was not a list');
  return payload.flatMap((value): UpdateReleaseInfo[] => {
    if (!value || typeof value !== 'object') return [];
    const item = value as Record<string, unknown>;
    const tag = typeof item.tag_name === 'string' ? item.tag_name.trim() : '';
    // Only desktop releases: the phone apps publish `mobile-vX.Y.Z` prereleases too.
    if (!RELEASE_TAG.test(tag)) return [];
    const name = typeof item.name === 'string' && item.name.trim() ? item.name.trim() : tag;
    return [
      {
        version: tag.replace(/^v/, ''),
        name,
        date: typeof item.published_at === 'string' ? item.published_at : '',
        prerelease: item.prerelease === true,
        notes: typeof item.body === 'string' ? item.body : '',
      },
    ];
  });
}

function releaseNotes(info: UpdateInfo): string | null {
  const notes = info.releaseNotes;
  if (typeof notes === 'string') return notes;
  if (!Array.isArray(notes)) return null;
  return (
    notes
      .map((entry) => entry.note)
      .filter(Boolean)
      .join('\n\n') || null
  );
}

// The feed lists every installer for a platform (macOS: zip and dmg; Linux:
// AppImage and deb), but electron-updater downloads only one of them. Summing
// them doubled the offered size; report the file this install will fetch.
const UPDATE_PAYLOAD_EXTENSION: Partial<Record<NodeJS.Platform, string>> = {
  darwin: '.zip',
  win32: '.exe',
};

export function releaseSize(
  info: Pick<UpdateInfo, 'files' | 'path'>,
  platform: NodeJS.Platform = process.platform,
  appImage: string | undefined = process.env.APPIMAGE,
): number | undefined {
  const files = info.files ?? [];
  const extension =
    platform === 'linux' ? (appImage ? '.AppImage' : '.deb') : UPDATE_PAYLOAD_EXTENSION[platform];
  const payload =
    (extension && files.find((file) => file.url.endsWith(extension))) ||
    files.find((file) => file.url === info.path) ||
    files[0];
  return payload?.size && payload.size > 0 ? payload.size : undefined;
}

export class DesktopUpdater {
  private listeners = new Set<(state: UpdateState) => void>();
  private state: UpdateState;
  private checkInFlight: Promise<UpdateState> | null = null;
  private downloadInFlight: Promise<UpdateState> | null = null;
  private channel: UpdateChannel = 'stable';

  constructor(
    private readonly distribution: DistributionTarget = CURRENT_DISTRIBUTION,
    private readonly feedFetcher: typeof fetch = fetch,
  ) {
    const supported = this.updatesSupported();
    // Store builds are stable-only, so they expose no channel to switch. A dev
    // run cannot update itself but still shows and saves the channel.
    const selectable = (supported || !app.isPackaged) && distribution !== 'microsoft-store';
    if (selectable) this.channel = readSavedUpdateChannel();
    this.state = {
      status: supported ? 'idle' : 'unsupported',
      currentVersion: __APP_VERSION__,
      progress: 0,
      channel: selectable ? this.channel : undefined,
      unsupportedReason: this.unsupportedReason() ?? undefined,
    };
    if (!supported) return;

    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = false;
    autoUpdater.allowDowngrade = false;
    // The NSIS installer is not a web installer; this also silences the
    // per-download warning electron-updater logs otherwise.
    autoUpdater.disableWebInstaller = true;
    autoUpdater.on('checking-for-update', () =>
      this.patch({
        status: 'checking',
        progress: 0,
        transferredBytes: undefined,
        totalBytes: undefined,
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        error: undefined,
      }),
    );
    autoUpdater.on('update-available', (info) =>
      this.patch({
        status: 'available',
        availableVersion: info.version,
        notes: releaseNotes(info),
        transferredBytes: 0,
        totalBytes: releaseSize(info),
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        lastCheckedAt: Date.now(),
        error: undefined,
      }),
    );
    autoUpdater.on('update-not-available', () =>
      this.patch({
        status: 'idle',
        availableVersion: undefined,
        notes: null,
        progress: 0,
        transferredBytes: undefined,
        totalBytes: undefined,
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        lastCheckedAt: Date.now(),
        error: undefined,
      }),
    );
    autoUpdater.on('download-progress', (progress: ProgressInfo) => {
      const totalBytes = Math.max(0, progress.total || 0);
      const transferredBytes = Math.max(0, progress.transferred || 0);
      const bytesPerSecond = Math.max(0, progress.bytesPerSecond || 0);
      this.patch({
        status: 'downloading',
        progress: Math.max(0, Math.min(100, progress.percent)),
        transferredBytes,
        totalBytes: totalBytes || this.state.totalBytes,
        bytesPerSecond: bytesPerSecond || undefined,
        etaSeconds:
          totalBytes > transferredBytes && bytesPerSecond > 0
            ? Math.ceil((totalBytes - transferredBytes) / bytesPerSecond)
            : 0,
      });
    });
    autoUpdater.on('update-downloaded', (info) => {
      const totalBytes = this.state.totalBytes || releaseSize(info);
      this.patch({
        status: 'downloaded',
        availableVersion: info.version,
        notes: releaseNotes(info),
        progress: 100,
        transferredBytes: totalBytes || this.state.transferredBytes || 0,
        totalBytes,
        bytesPerSecond: undefined,
        etaSeconds: 0,
        error: undefined,
      });
    });
    autoUpdater.on('error', (error) =>
      this.patch({ status: 'error', error: error.message || String(error) }),
    );
    if (this.distribution !== 'microsoft-store') this.configureFeed(STABLE_FEED, feedChannelName());
  }

  snapshot(): UpdateState {
    return { ...this.state };
  }

  subscribe(listener: (state: UpdateState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async check(quiet = false): Promise<UpdateState> {
    if (
      !this.updatesSupported() ||
      this.downloadInFlight ||
      ['downloading', 'downloaded'].includes(this.state.status)
    )
      return this.snapshot();
    if (this.checkInFlight) return this.checkInFlight;
    const previousState = this.snapshot();
    this.patch({
      status: 'checking',
      progress: 0,
      transferredBytes: undefined,
      totalBytes: undefined,
      bytesPerSecond: undefined,
      etaSeconds: undefined,
      error: undefined,
    });
    const operation = (async () => {
      try {
        const feed =
          this.distribution === 'microsoft-store'
            ? {
                url: await resolveUpdateFeedUrl(
                  this.distribution,
                  process.platform,
                  process.arch,
                  this.feedFetcher,
                ),
                channel: feedChannelName(process.platform, process.arch, this.distribution),
              }
            : this.channel === 'beta'
              ? await resolveBetaFeed(process.platform, process.arch, this.feedFetcher)
              : { url: STABLE_FEED, channel: feedChannelName() };
        this.configureFeed(feed.url, feed.channel);
        await autoUpdater.checkForUpdates();
        if (this.state.status === 'checking') {
          this.patch({ status: 'idle', lastCheckedAt: Date.now() });
        }
      } catch (error) {
        if (quiet) {
          this.state = previousState;
          this.emit();
        } else
          this.patch({
            status: 'error',
            error: error instanceof Error ? error.message : String(error),
          });
      }
      return this.snapshot();
    })();
    this.checkInFlight = operation;
    try {
      return await operation;
    } finally {
      if (this.checkInFlight === operation) this.checkInFlight = null;
    }
  }

  async download(): Promise<UpdateState> {
    if (!this.updatesSupported() || this.checkInFlight || this.state.status !== 'available')
      return this.snapshot();
    if (this.downloadInFlight) return this.downloadInFlight;
    const operation = (async () => {
      this.patch({
        status: 'downloading',
        progress: 0,
        transferredBytes: 0,
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        error: undefined,
      });
      try {
        await autoUpdater.downloadUpdate();
      } catch (error) {
        this.patch({
          status: 'error',
          error: error instanceof Error ? error.message : String(error),
        });
      }
      return this.snapshot();
    })();
    this.downloadInFlight = operation;
    try {
      return await operation;
    } finally {
      if (this.downloadInFlight === operation) this.downloadInFlight = null;
    }
  }

  dismiss(): UpdateState {
    if (this.state.status !== 'error') return this.snapshot();
    this.state = {
      status: 'idle',
      currentVersion: this.state.currentVersion,
      progress: 0,
      channel: this.state.channel,
      lastCheckedAt: this.state.lastCheckedAt,
    };
    this.emit();
    return this.snapshot();
  }

  /** Switches channel. Refused mid-download; clears any offer from the old channel. */
  setChannel(channel: UpdateChannel): UpdateState {
    if (
      this.state.channel === undefined ||
      channel === this.channel ||
      this.downloadInFlight ||
      ['checking', 'downloading', 'downloaded'].includes(this.state.status)
    )
      return this.snapshot();
    this.channel = channel;
    saveUpdateChannel(channel);
    if (!this.updatesSupported()) {
      this.patch({ channel });
      return this.snapshot();
    }
    this.state = {
      status: 'idle',
      currentVersion: this.state.currentVersion,
      progress: 0,
      channel,
    };
    this.emit();
    return this.snapshot();
  }

  install(): void {
    if (!this.updatesSupported() || this.state.status !== 'downloaded') return;
    autoUpdater.quitAndInstall(false, true);
  }

  private configureFeed(url: string, feedChannel: string): void {
    if (!this.updatesSupported()) return;
    // Feeds are resolved per release and read through the generic provider, so
    // electron-updater's own prerelease filtering never applies.
    autoUpdater.allowPrerelease = false;
    autoUpdater.channel = feedChannel;
    // electron-updater's channel setter silently enables downgrades; keep the
    // app monotonic.
    autoUpdater.allowDowngrade = false;
    // Explicit, so a build packaged with another feed URL follows the chosen channel.
    autoUpdater.setFeedURL({
      provider: 'generic',
      url,
      channel: feedChannel,
      // GitHub release downloads answer multi-range requests with HTTP 501, so
      // every differential (patch) download failed and fell back to the full
      // installer. Single ranges are served (206); electron-updater's own
      // GitHub provider disables multi-range for the same reason.
      useMultipleRangeRequest: false,
    });
  }

  private updatesSupported(): boolean {
    return this.unsupportedReason() === null;
  }

  private unsupportedReason() {
    return applicationUpdatesUnsupportedReason(app.isPackaged, this.distribution);
  }

  private patch(value: Partial<UpdateState>): void {
    this.state = { ...this.state, ...value };
    this.emit();
  }

  private emit(): void {
    const state = this.snapshot();
    for (const listener of this.listeners) listener(state);
  }
}

export function registerUpdateIpc(
  updater: DesktopUpdater,
  getMainWindow: () => BrowserWindow | null,
  beforeInstall: () => Promise<void>,
): () => void {
  const trusted = (event: Electron.IpcMainInvokeEvent) => {
    const owner = getMainWindow();
    if (
      !owner ||
      event.sender !== owner.webContents ||
      event.senderFrame !== owner.webContents.mainFrame ||
      !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
    )
      throw new Error('Untrusted update request');
  };
  ipcMain.handle(UPDATE_CHANNELS.getState, (event) => {
    trusted(event);
    return updater.snapshot();
  });
  ipcMain.handle(UPDATE_CHANNELS.check, (event) => {
    trusted(event);
    return updater.check();
  });
  ipcMain.handle(UPDATE_CHANNELS.download, (event) => {
    trusted(event);
    return updater.download();
  });
  ipcMain.handle(UPDATE_CHANNELS.dismiss, (event) => {
    trusted(event);
    return updater.dismiss();
  });
  ipcMain.handle(UPDATE_CHANNELS.install, async (event) => {
    trusted(event);
    if (updater.snapshot().status !== 'downloaded') return;
    await beforeInstall();
    updater.install();
  });
  ipcMain.handle(UPDATE_CHANNELS.setChannel, (event, channel: unknown) => {
    trusted(event);
    if (channel !== 'stable' && channel !== 'beta') throw new Error('Unknown update channel');
    return updater.setChannel(channel);
  });
  ipcMain.handle(UPDATE_CHANNELS.listReleases, (event) => {
    trusted(event);
    return listDesktopReleases();
  });
  const unsubscribe = updater.subscribe((state) => {
    sendToLiveWindow(getMainWindow(), UPDATE_CHANNELS.state, state);
  });
  return () => {
    unsubscribe();
    Object.values(UPDATE_CHANNELS)
      .filter((channel) => channel !== UPDATE_CHANNELS.state)
      .forEach((channel) => ipcMain.removeHandler(channel));
  };
}
