import { app, BrowserWindow, ipcMain } from 'electron';
import updaterPackage, { type ProgressInfo, type UpdateInfo } from 'electron-updater';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { isTrustedRenderer } from './trusted-renderer';
import type { UpdateErrorKind, UpdateReleaseInfo, UpdateState } from '../preload/index.d';
import { isLiveWindow, sendToLiveWindow } from './window-safety';
import { applicationUpdatesUnsupportedReason, CURRENT_DISTRIBUTION } from './distribution';
import type { DistributionTarget } from '../../../config/distributions.mjs';
import { GitHubAssetProvider, releaseAssets, type ReleaseAssets } from './github-asset-provider';
import { cleanInstalledUpdateDownload } from './updater-cache';

// electron-updater is CommonJS. Electron executes our main bundle as ESM, so
// named runtime imports fail before the app can create a window.
const { autoUpdater, CancellationToken } = updaterPackage;

// Two update channels, both fed by tagged GitHub releases cut from main:
// Stable follows `vX.Y.Z` (the `latest` release); Beta follows `vX.Y.Z-beta.N`
// prereleases and rolls onto the next stable once it passes them. GitHub's
// `latest/download` never serves a prerelease, so Beta resolves its release
// through the releases API (see resolveBetaFeed).
export type UpdateChannel = 'stable' | 'beta';
const STABLE_FEED = 'https://github.com/debpalash/VoiceStudio/releases/latest/download';
const RELEASES_API = 'https://api.github.com/repos/debpalash/VoiceStudio/releases?per_page=30';
const LATEST_RELEASE_API = 'https://api.github.com/repos/debpalash/VoiceStudio/releases/latest';
const GITHUB_API_HEADERS = {
  Accept: 'application/vnd.github+json',
  'User-Agent': 'VoiceStudio',
} as const;

/**
 * A feed electron-updater reads its channel manifest from. `tag` and `assets`
 * are known when the release was resolved through the API, so a fallback to
 * the asset route needs no further lookup.
 */
export interface UpdateFeed {
  url: string;
  channel: string;
  tag?: string;
  assets?: ReleaseAssets;
}

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
): Promise<Required<UpdateFeed>> {
  const response = await fetcher(RELEASES_API, {
    headers: GITHUB_API_HEADERS,
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
      ? [
          {
            tag,
            rank,
            channel: feedChannelName(platform, arch, 'direct', kind),
            assets: releaseAssets(item.assets),
          },
        ]
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
  return {
    url: `${RELEASE_DOWNLOAD}/${encodeURIComponent(newest.tag)}`,
    channel: newest.channel,
    tag: newest.tag,
    assets: newest.assets,
  };
}

/** The latest stable release and its assets, in one API request. */
export async function resolveLatestRelease(
  fetcher: typeof fetch = fetch,
): Promise<{ tag: string; assets: ReleaseAssets }> {
  const response = await fetcher(LATEST_RELEASE_API, {
    headers: GITHUB_API_HEADERS,
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Latest release lookup failed (${response.status})`);
  const release: unknown = await response.json();
  const tag =
    release && typeof release === 'object' ? (release as Record<string, unknown>).tag_name : null;
  if (typeof tag !== 'string' || !RELEASE_TAG.test(tag))
    throw new Error('Latest release lookup returned invalid data');
  return { tag, assets: releaseAssets((release as Record<string, unknown>).assets) };
}

const PREFS_FILE = 'updates.json';

/** Update preferences and bookkeeping, kept together in `updates.json`. */
interface UpdatePrefs {
  channel?: string;
  /** Absent means on: a found update downloads in the background. */
  autoDownload?: boolean;
  /** The version the user chose "Later" for. */
  dismissedVersion?: string;
  /** Set just before an install; the next launch checks it took effect. */
  pendingVersion?: string;
}

function readUpdatePrefs(userData: string = app.getPath('userData')): UpdatePrefs {
  try {
    const saved: unknown = JSON.parse(readFileSync(join(userData, PREFS_FILE), 'utf8'));
    if (!saved || typeof saved !== 'object') return {};
    const item = saved as Record<string, unknown>;
    const text = (value: unknown) => (typeof value === 'string' && value ? value : undefined);
    return {
      channel: text(item.channel),
      autoDownload: typeof item.autoDownload === 'boolean' ? item.autoDownload : undefined,
      dismissedVersion: text(item.dismissedVersion),
      pendingVersion: text(item.pendingVersion),
    };
  } catch {
    return {};
  }
}

/** Merges `patch` into the saved preferences; an undefined field is removed. */
function saveUpdatePrefs(patch: UpdatePrefs, userData: string = app.getPath('userData')) {
  try {
    writeFileSync(
      join(userData, PREFS_FILE),
      JSON.stringify({ ...readUpdatePrefs(userData), ...patch }),
    );
  } catch {
    // Best effort: the choice still applies for this session.
  }
}

/** Anything but an explicit "beta" (including the retired "preview") is Stable. */
export function readSavedUpdateChannel(userData: string = app.getPath('userData')): UpdateChannel {
  return readUpdatePrefs(userData).channel === 'beta' ? 'beta' : 'stable';
}

/**
 * Whether quitting installs a downloaded update. macOS (Squirrel), Windows
 * (NSIS) and the AppImage replace themselves silently on quit. A .deb install
 * needs an administrator prompt, which must not appear after the window is
 * gone, so it installs only from an explicit restart.
 */
export function installsOnQuit(
  platform: NodeJS.Platform = process.platform,
  appImage: string | undefined = process.env.APPIMAGE,
): boolean {
  return platform === 'darwin' || platform === 'win32' || (platform === 'linux' && !!appImage);
}

/** -1, 0 or 1; versions this app never tags compare by equality only. */
export function compareAppVersions(left: string, right: string): number {
  const a = releaseRank(`v${left.replace(/^v/, '')}`);
  const b = releaseRank(`v${right.replace(/^v/, '')}`);
  if (!a || !b) return left === right ? 0 : -1;
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
  }
  return 0;
}

// electron-updater's sha512 check, the Windows publisher check and Squirrel's
// code-signature check all fail with one of these words.
const VERIFY_FAILURE =
  /sha-?512|sha-?256|checksum|signature|not signed|signed by|code ?sign|verif|integrity/i;

function errorText(error: unknown): string {
  const parts: string[] = [];
  for (let current = error, depth = 0; current && depth < 4; depth += 1) {
    const message = (current as { message?: unknown }).message;
    parts.push(typeof message === 'string' ? message : String(current));
    current = (current as { cause?: unknown }).cause;
  }
  return parts.join(' ');
}

/** Which step an update failure belongs to, so the UI can say what went wrong. */
export function classifyUpdateError(
  error: unknown,
  phase: 'check' | 'download' | 'install',
): UpdateErrorKind {
  if (phase !== 'check' && VERIFY_FAILURE.test(errorText(error))) return 'verify';
  return phase;
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
        ? [
            {
              release: item,
              version: [Number(version[1]), Number(version[2]), Number(version[3])],
            },
          ]
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
  later: 'updates:later',
  setAutoDownload: 'updates:setAutoDownload',
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

/** The installer extension electron-updater downloads on this install. */
export function updatePayloadExtension(
  platform: NodeJS.Platform = process.platform,
  appImage: string | undefined = process.env.APPIMAGE,
): string | undefined {
  return platform === 'linux'
    ? appImage
      ? '.AppImage'
      : '.deb'
    : UPDATE_PAYLOAD_EXTENSION[platform];
}

export function releaseSize(
  info: Pick<UpdateInfo, 'files' | 'path'>,
  platform: NodeJS.Platform = process.platform,
  appImage: string | undefined = process.env.APPIMAGE,
): number | undefined {
  const files = info.files ?? [];
  const extension = updatePayloadExtension(platform, appImage);
  const payload =
    (extension && files.find((file) => file.url.endsWith(extension))) ||
    files.find((file) => file.url === info.path) ||
    files[0];
  return payload?.size && payload.size > 0 ? payload.size : undefined;
}

const TRANSIENT_NETWORK_CODES = [
  'ETIMEDOUT',
  'ECONNRESET',
  'ENETUNREACH',
  'EAI_AGAIN',
  'ENOTFOUND',
];
/** Waits between attempts of a transient update failure (2 s, then 6 s). */
export const UPDATE_RETRY_DELAYS_MS: readonly number[] = [2_000, 6_000];

/**
 * GitHub's release CDN answers 5xx/429 for hours at a time and home networks
 * drop; those are worth another try. A 404, bad signature or checksum is not.
 */
export function isTransientUpdateError(error: unknown): boolean {
  const seen: unknown[] = [];
  for (let current = error; current && seen.length < 4;) {
    seen.push(current);
    const item = current as {
      name?: unknown;
      message?: unknown;
      code?: unknown;
      statusCode?: unknown;
    };
    // AbortSignal.timeout() rejects with a DOMException named TimeoutError; a
    // user abort (AbortError) is deliberate and is not retried.
    if (item.name === 'TimeoutError') return true;
    const status = Number(item.statusCode);
    if (status === 429 || (status >= 500 && status <= 599)) return true;
    if (typeof item.code === 'string' && TRANSIENT_NETWORK_CODES.includes(item.code)) return true;
    const message = typeof item.message === 'string' ? item.message : '';
    if (
      /(?:^|\s)(?:HttpError:\s*)?(?:429|5\d\d)\b|\((?:429|5\d\d)\)/.test(message) ||
      TRANSIENT_NETWORK_CODES.some((code) => message.includes(code))
    )
      return true;
    current = (current as { cause?: unknown }).cause;
  }
  return false;
}

/** A download that receives no bytes for this long is cancelled and retried. */
export const UPDATE_STALL_TIMEOUT_MS = 45_000;

/** Classified as transient (TimeoutError), so the usual retry and Retry UI apply. */
function downloadStalledError(timeoutMs: number): Error {
  const error = new Error(
    `The update download stopped receiving data for ${Math.round(timeoutMs / 1_000)} seconds`,
  );
  error.name = 'TimeoutError';
  return error;
}

export class DesktopUpdater {
  private listeners = new Set<(state: UpdateState) => void>();
  private state: UpdateState;
  private checkInFlight: Promise<UpdateState> | null = null;
  private downloadInFlight: Promise<UpdateState> | null = null;
  private channel: UpdateChannel = 'stable';
  private operating = false;
  // The feed electron-updater reads now; `viaApi` once it uses the asset route.
  private feed: (UpdateFeed & { viaApi: boolean }) | null = null;
  // Set while a download re-reads the manifest through the asset route, so
  // that hidden check does not flip the visible state back to "available".
  private silentCheck = false;
  private lastCheckFailed = false;
  private lastDownloadFailed = false;
  private autoDownload = true;
  private installOnQuit = false;
  private installing = false;
  private differentialDisabledByDefault = false;
  // Removes an already-installed download; checks wait so it never races one.
  private cacheCleanup: Promise<void> | null = null;

  constructor(
    private readonly distribution: DistributionTarget = CURRENT_DISTRIBUTION,
    private readonly feedFetcher: typeof fetch = fetch,
    private readonly retryDelaysMs: readonly number[] = UPDATE_RETRY_DELAYS_MS,
    private readonly stallTimeoutMs: number = UPDATE_STALL_TIMEOUT_MS,
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
    this.cacheCleanup = cleanInstalledUpdateDownload(
      autoUpdater,
      this.state.currentVersion,
      compareAppVersions,
    );
    void this.cacheCleanup?.then(() => {
      this.cacheCleanup = null;
    });

    const prefs = readUpdatePrefs();
    this.autoDownload = prefs.autoDownload !== false;
    this.installOnQuit = installsOnQuit();
    this.state = {
      ...this.state,
      autoDownload: this.autoDownload,
      installOnQuit: this.installOnQuit,
      dismissedVersion: prefs.dismissedVersion,
    };
    // An install was started last session: it either took effect or did not.
    if (prefs.pendingVersion) {
      saveUpdatePrefs({ pendingVersion: undefined });
      if (compareAppVersions(this.state.currentVersion, prefs.pendingVersion) < 0)
        this.state = {
          ...this.state,
          status: 'error',
          errorKind: 'not-applied',
          availableVersion: prefs.pendingVersion,
        };
    }

    // DesktopUpdater decides when to download (see check()); electron-updater
    // only installs a downloaded update when the app quits.
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = this.installOnQuit;
    autoUpdater.allowDowngrade = false;
    // The NSIS installer is not a web installer; this also silences the
    // per-download warning electron-updater logs otherwise.
    autoUpdater.disableWebInstaller = true;
    this.differentialDisabledByDefault = autoUpdater.disableDifferentialDownload === true;
    autoUpdater.on('checking-for-update', () => {
      if (this.silentCheck) return;
      this.patch({
        status: 'checking',
        progress: 0,
        transferredBytes: undefined,
        totalBytes: undefined,
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        error: undefined,
        errorKind: undefined,
      });
    });
    autoUpdater.on('update-available', (info) => {
      if (this.silentCheck) return;
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
        errorKind: undefined,
      });
    });
    autoUpdater.on('update-not-available', () => {
      if (this.silentCheck) return;
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
        errorKind: undefined,
      });
    });
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
        errorKind: undefined,
      });
    });
    autoUpdater.on('error', (error) => {
      // A running check/download reports (or retries) its own failure.
      if (this.operating) return;
      const phase =
        this.installing || this.state.status === 'downloaded'
          ? 'install'
          : this.state.status === 'downloading'
            ? 'download'
            : 'check';
      this.installing = false;
      this.patch({
        status: 'error',
        error: error?.message || String(error),
        errorKind: classifyUpdateError(error, phase),
      });
    });
    if (this.distribution !== 'microsoft-store')
      this.configureFeed({ url: STABLE_FEED, channel: feedChannelName() });
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
      ['downloading', 'downloaded'].includes(this.state.status) ||
      // An update that did not apply waits for the user, so a background
      // check cannot loop it through download and install again.
      (quiet && this.state.errorKind === 'not-applied')
    )
      return this.snapshot();
    if (this.checkInFlight) return this.checkInFlight;
    const previousState = this.snapshot();
    this.operating = true;
    this.patch({
      status: 'checking',
      progress: 0,
      transferredBytes: undefined,
      totalBytes: undefined,
      bytesPerSecond: undefined,
      etaSeconds: undefined,
      error: undefined,
      errorKind: undefined,
    });
    let offered = false;
    const operation = (async () => {
      if (this.cacheCleanup) await this.cacheCleanup;
      try {
        // Set once the feed is resolved, so only a failure of the release
        // download host itself (not of the API lookup) uses the asset route.
        const attempt: { feed?: UpdateFeed } = {};
        try {
          await this.withRetry(this.retryDelaysMs, async () => {
            attempt.feed = undefined;
            const feed: UpdateFeed =
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
            this.configureFeed(feed);
            attempt.feed = feed;
            await autoUpdater.checkForUpdates();
          });
        } catch (error) {
          if (!attempt.feed || !this.canUseAssetRoute(error)) throw error;
          const release = await this.apiRelease(attempt.feed);
          await this.withRetry(this.retryDelaysMs.slice(0, 1), async () => {
            this.configureFeed(release, true);
            await autoUpdater.checkForUpdates();
          });
        }
        if (this.state.status === 'checking') {
          this.patch({ status: 'idle', lastCheckedAt: Date.now() });
        }
        offered = this.state.status === 'available';
      } catch (error) {
        this.lastCheckFailed = true;
        if (quiet) {
          this.state = previousState;
          this.emit();
        } else
          this.patch({
            status: 'error',
            error: error instanceof Error ? error.message : String(error),
            errorKind: 'check',
          });
      }
      return this.snapshot();
    })();
    this.checkInFlight = operation;
    let result: UpdateState;
    try {
      result = await operation;
    } finally {
      this.operating = false;
      if (this.checkInFlight === operation) this.checkInFlight = null;
    }
    // A found update downloads and verifies in the background; a background
    // check keeps a network failure of that download quiet, too.
    if (offered && this.autoDownload && this.state.status === 'available')
      void this.download({ quiet });
    return result;
  }

  /**
   * Downloads the offered update. `quiet` (background) downloads return to
   * "available" on a network failure instead of showing an error; the check
   * schedule retries them later.
   */
  async download({ quiet = false }: { quiet?: boolean } = {}): Promise<UpdateState> {
    if (!this.updatesSupported() || this.checkInFlight || this.state.status !== 'available')
      return this.snapshot();
    if (this.downloadInFlight) return this.downloadInFlight;
    this.operating = true;
    const operation = (async () => {
      this.patch({
        status: 'downloading',
        progress: 0,
        transferredBytes: 0,
        bytesPerSecond: undefined,
        etaSeconds: undefined,
        error: undefined,
        errorKind: undefined,
      });
      // A network failure stays quiet even when its asset-route fallback then
      // fails another way (an API rate limit, say).
      let networkFailure = false;
      try {
        try {
          await this.withRetry(this.retryDelaysMs.slice(0, 1), () => this.watchedDownload());
        } catch (error) {
          networkFailure = isTransientUpdateError(error);
          if (!this.feed || !this.canUseAssetRoute(error)) throw error;
          await this.downloadThroughAssetRoute(this.feed);
        }
      } catch (error) {
        this.lastDownloadFailed = true;
        const errorKind = classifyUpdateError(error, 'download');
        // A failed verification always shows, even after a network fallback.
        if (quiet && errorKind !== 'verify' && (networkFailure || isTransientUpdateError(error)))
          this.patch({
            status: 'available',
            progress: 0,
            transferredBytes: 0,
            bytesPerSecond: undefined,
            etaSeconds: undefined,
          });
        else
          this.patch({
            status: 'error',
            error: error instanceof Error ? error.message : String(error),
            errorKind,
          });
      }
      return this.snapshot();
    })();
    this.downloadInFlight = operation;
    try {
      return await operation;
    } finally {
      this.operating = false;
      if (this.downloadInFlight === operation) this.downloadInFlight = null;
    }
  }

  /**
   * A background check, and the download it starts; resolves false when
   * either failed, so the check schedule retries sooner.
   */
  async checkInBackground(): Promise<boolean> {
    this.lastCheckFailed = false;
    this.lastDownloadFailed = false;
    await this.check(true);
    if (this.downloadInFlight) await this.downloadInFlight;
    return !this.lastCheckFailed && !this.lastDownloadFailed;
  }

  /**
   * GitHub's release-download host has failed for hours while its API kept
   * serving the same assets. Transient failures there retry through the API;
   * the asset route is never used when the normal feed works.
   */
  private canUseAssetRoute(error: unknown): boolean {
    return (
      this.distribution !== 'microsoft-store' &&
      this.feed?.viaApi !== true &&
      isTransientUpdateError(error)
    );
  }

  /** The feed's release with asset ids; Stable spends its one API request here. */
  private async apiRelease(feed: UpdateFeed): Promise<Required<UpdateFeed>> {
    if (feed.tag && feed.assets) return { ...feed, tag: feed.tag, assets: feed.assets };
    const latest = await resolveLatestRelease(this.feedFetcher);
    return {
      url: `${RELEASE_DOWNLOAD}/${encodeURIComponent(latest.tag)}`,
      channel: feed.channel,
      ...latest,
    };
  }

  /** Re-reads the offered release through the asset route, then downloads it. */
  private async downloadThroughAssetRoute(feed: UpdateFeed): Promise<void> {
    const offered = this.state.availableVersion;
    const release = await this.apiRelease(feed);
    this.configureFeed(release, true);
    // electron-updater downloads from the provider of its last check.
    this.silentCheck = true;
    let result: Awaited<ReturnType<typeof autoUpdater.checkForUpdates>>;
    try {
      result = await autoUpdater.checkForUpdates();
    } finally {
      this.silentCheck = false;
    }
    if (!result?.isUpdateAvailable || result.updateInfo.version !== offered)
      throw new Error('The offered update changed; check for updates again');
    await this.withRetry(this.retryDelaysMs.slice(0, 1), () => this.watchedDownload());
  }

  /** Downloads, cancelling when no bytes arrive for `stallTimeoutMs`. */
  private async watchedDownload(): Promise<void> {
    const token = new CancellationToken();
    let transferred = -1;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stalled!: (error: Error) => void;
    const stall = new Promise<never>((_resolve, reject) => (stalled = reject));
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        token.cancel();
        stalled(downloadStalledError(this.stallTimeoutMs));
      }, this.stallTimeoutMs);
    };
    let received = false;
    const onProgress = (progress: ProgressInfo) => {
      const bytes = Number(progress?.transferred) || 0;
      if (bytes > transferred && !received) {
        transferred = bytes;
        arm();
      }
    };
    // Every byte is in and verified; on macOS the download then hands the file
    // to Squirrel.Mac (install on quit) without progress events, so the
    // watchdog stops here.
    const onDownloaded = () => {
      received = true;
      clearTimeout(timer);
    };
    autoUpdater.on('download-progress', onProgress);
    autoUpdater.on('update-downloaded', onDownloaded);
    arm();
    try {
      const download = autoUpdater.downloadUpdate(token);
      // Settles the cancelled download quietly once the stall error has won.
      download.catch(() => undefined);
      await Promise.race([download, stall]);
    } finally {
      clearTimeout(timer);
      autoUpdater.removeListener('download-progress', onProgress);
      autoUpdater.removeListener('update-downloaded', onDownloaded);
    }
  }

  /** Runs `operation`, retrying transient failures after each delay in turn. */
  private async withRetry<T>(delays: readonly number[], operation: () => Promise<T>): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await operation();
      } catch (error) {
        if (attempt >= delays.length || !isTransientUpdateError(error)) throw error;
        await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
      }
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
      autoDownload: this.state.autoDownload,
      installOnQuit: this.state.installOnQuit,
      dismissedVersion: this.state.dismissedVersion,
    };
    this.emit();
    return this.snapshot();
  }

  /** "Later": stop prompting for this version. It still installs on quit. */
  later(): UpdateState {
    const version = this.state.availableVersion;
    if (!this.updatesSupported() || !version) return this.snapshot();
    saveUpdatePrefs({ dismissedVersion: version });
    this.patch({ dismissedVersion: version });
    return this.snapshot();
  }

  /** Turning automatic downloads on starts one for an update already offered. */
  setAutoDownload(enabled: boolean): UpdateState {
    if (!this.updatesSupported()) return this.snapshot();
    this.autoDownload = enabled;
    saveUpdatePrefs({ autoDownload: enabled });
    this.patch({ autoDownload: enabled });
    if (enabled && this.state.status === 'available') void this.download({ quiet: true });
    return this.snapshot();
  }

  /**
   * Called as the app quits. Records the version a quit is about to install,
   * so the next launch can tell whether it applied; `install` false (a
   * relaunch or uninstall exit) skips the install-on-quit for this quit.
   */
  prepareForQuit(install = true): void {
    if (!this.updatesSupported()) return;
    if (!install) {
      autoUpdater.autoInstallOnAppQuit = false;
      return;
    }
    if (this.installOnQuit && this.state.status === 'downloaded' && this.state.availableVersion)
      saveUpdatePrefs({ pendingVersion: this.state.availableVersion });
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
    saveUpdatePrefs({ channel });
    if (!this.updatesSupported()) {
      this.patch({ channel });
      return this.snapshot();
    }
    this.state = {
      status: 'idle',
      currentVersion: this.state.currentVersion,
      progress: 0,
      channel,
      autoDownload: this.state.autoDownload,
      installOnQuit: this.state.installOnQuit,
      dismissedVersion: this.state.dismissedVersion,
    };
    this.emit();
    return this.snapshot();
  }

  install(): void {
    if (!this.updatesSupported() || this.state.status !== 'downloaded') return;
    if (this.state.availableVersion)
      saveUpdatePrefs({ pendingVersion: this.state.availableVersion });
    this.installing = true;
    try {
      autoUpdater.quitAndInstall(false, true);
    } catch (error) {
      this.installing = false;
      saveUpdatePrefs({ pendingVersion: undefined });
      this.patch({
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
        errorKind: classifyUpdateError(error, 'install'),
      });
    }
  }

  private configureFeed(feed: UpdateFeed, viaApi = false): void {
    if (!this.updatesSupported()) return;
    const { url, channel: feedChannel } = feed;
    // Feeds are resolved per release and read through the generic provider, so
    // electron-updater's own prerelease filtering never applies.
    autoUpdater.allowPrerelease = false;
    autoUpdater.channel = feedChannel;
    // electron-updater's channel setter silently enables downgrades; keep the
    // app monotonic.
    autoUpdater.allowDowngrade = false;
    // A patch download makes many ranged requests, each an API call against
    // GitHub's 60-an-hour limit: the asset route downloads in full.
    autoUpdater.disableDifferentialDownload = viaApi || this.differentialDisabledByDefault;
    // Explicit, so a build packaged with another feed URL follows the chosen channel.
    if (viaApi)
      autoUpdater.setFeedURL({
        provider: 'custom',
        updateProvider: GitHubAssetProvider,
        url,
        channel: feedChannel,
        assets: feed.assets ?? {},
        payloadExtension: updatePayloadExtension(),
      });
    else
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
    this.feed = { ...feed, viaApi };
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
  ipcMain.handle(UPDATE_CHANNELS.later, (event) => {
    trusted(event);
    return updater.later();
  });
  ipcMain.handle(UPDATE_CHANNELS.setAutoDownload, (event, enabled: unknown) => {
    trusted(event);
    if (typeof enabled !== 'boolean') throw new Error('Invalid automatic download setting');
    return updater.setAutoDownload(enabled);
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
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    const removeHandlers = () => {
      Object.values(UPDATE_CHANNELS)
        .filter((channel) => channel !== UPDATE_CHANNELS.state)
        .forEach((channel) => ipcMain.removeHandler(channel));
    };
    // Teardown awaits the backend while the renderer is still mounted. Keep
    // its update requests valid until Electron has closed every window.
    if (isLiveWindow(getMainWindow())) app.once('will-quit', removeHandlers);
    else removeHandlers();
  };
}
