export interface SetupProgress {
  resolvedPackages?: number;
  preparedPackages?: number;
  installedPackages?: number;
  completedDownloads: number;
  downloadedBytes?: number;
  totalBytes?: number;
  bytesPerSecond?: number;
  etaSeconds?: number;
  transferUpdatedAt?: number;
  activityUpdatedAt?: number;
  activePackage?: string;
  downloadsComplete?: boolean;
  /** uv only announces sizeable artifacts, so byte totals are approximate. */
  estimatedBytes?: boolean;
  /** Host serving `activePackage`, from the wheel URLs pinned in uv.lock (#3411). */
  downloadHost?: string;
  /** Pending downloads received no bytes for `stalledSeconds` (#3411). */
  stalled?: boolean;
  stalledSeconds?: number;
}

/**
 * Silence on a slow-but-moving link never reaches this: uv streams every
 * in-flight wheel into its cache, so growth there counts as progress even
 * while a multi-gigabyte wheel prints nothing for minutes.
 */
export const DOWNLOAD_STALL_MS = 45_000;

const ANSI_ESCAPE = /\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))/g;
const SIZE = '(\\d+(?:\\.\\d+)?)\\s*(B|KB|KiB|MB|MiB|GB|GiB)';

export function cleanProcessLine(line: string): string {
  return line.replace(ANSI_ESCAPE, '').trim();
}

export function parseByteSize(value: string, unit: string): number {
  const powers: Record<string, number> = {
    B: 1,
    KB: 1000,
    KiB: 1024,
    MB: 1000 ** 2,
    MiB: 1024 ** 2,
    GB: 1000 ** 3,
    GiB: 1024 ** 3,
  };
  return Number(value) * (powers[unit] ?? 1);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * PEP 503 name normalization. uv may spell one distribution as
 * `pydantic_core` on one line and `pydantic-core` on the next; both must
 * land on the same planned entry.
 */
export function normalizePackageName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[-_.]+/g, '-');
}

/** Converts uv's human output into stable, renderer-safe progress data. */
export class SetupProgressTracker {
  /** Keyed by normalized package name; `labels` keeps the announced spelling. */
  private readonly planned = new Map<string, number>();
  private readonly labels = new Map<string, string>();
  private readonly received = new Map<string, number>();
  private readonly completed = new Set<string>();
  private readonly samples: Array<{ at: number; bytes: number }> = [];
  private progress: SetupProgress = { completedDownloads: 0 };
  private hosts = new Map<string, string>();
  /** Last time any download made progress: an announcement, a completion, or in-flight bytes. */
  private advancedAt: number | undefined;
  private inFlightBytes = 0;

  reset(): void {
    this.planned.clear();
    this.labels.clear();
    this.received.clear();
    this.completed.clear();
    this.samples.length = 0;
    this.progress = { completedDownloads: 0 };
    this.advancedAt = undefined;
    this.inFlightBytes = 0;
  }

  /** Package (any spelling) to the host its locked wheel downloads from. */
  setDownloadHosts(hosts: ReadonlyMap<string, string>): void {
    this.hosts = new Map([...hosts].map(([name, host]) => [normalizePackageName(name), host]));
  }

  /**
   * Bytes uv has streamed into its cache for downloads that have not finished.
   * Any growth is progress; a drop is a finished wheel moving into the cache.
   */
  observeInFlight(bytes: number, now = Date.now()): void {
    if (bytes > this.inFlightBytes) this.advancedAt = now;
    this.inFlightBytes = bytes;
  }

  ingest(rawLine: string, now = Date.now()): SetupProgress | null {
    const line = cleanProcessLine(rawLine);
    if (!line) return null;
    this.progress.activityUpdatedAt = now;
    let changed = false;

    const count = line.match(/^(Resolved|Prepared|Installed)\s+(\d+)\s+packages?\b/i);
    if (count) {
      const field = `${count[1].toLowerCase()}Packages` as
        | 'resolvedPackages'
        | 'preparedPackages'
        | 'installedPackages';
      this.progress[field] = Number(count[2]);
      if (field === 'preparedPackages' || field === 'installedPackages') {
        this.progress.downloadsComplete = true;
        this.progress.activePackage = undefined;
      }
      changed = true;
    }

    const starting = line.match(new RegExp(`^Downloading\\s+(.+?)\\s+\\(${SIZE}\\)`, 'i'));
    if (starting) {
      const name = normalizePackageName(starting[1]);
      if (!this.planned.has(name) || this.completed.has(name)) this.advancedAt = now;
      if (!this.labels.has(name)) this.labels.set(name, starting[1].trim());
      this.planned.set(name, parseByteSize(starting[2], starting[3]));
      this.completed.delete(name);
      this.progress.downloadsComplete = false;
      this.refreshDownloadState();
      changed = true;
    }

    const finished = line.match(/^Downloaded\s+(.+?)\s*$/i);
    if (finished) {
      const name = normalizePackageName(finished[1]);
      if (!this.completed.has(name)) this.advancedAt = now;
      this.completed.add(name);
      const size = this.planned.get(name);
      if (size !== undefined) this.received.set(name, size);
      this.refreshDownloadState();
      changed = true;
    }

    // Interactive uv progress uses "received/total" and carriage returns.
    const bytePair = line.match(new RegExp(`${SIZE}\\s*/\\s*${SIZE}`, 'i'));
    if (bytePair) {
      const received = parseByteSize(bytePair[1], bytePair[2]);
      const total = parseByteSize(bytePair[3], bytePair[4]);
      const name = this.packageOnLine(line);
      if (name) {
        if (received > (this.received.get(name) ?? 0)) this.advancedAt = now;
        this.planned.set(name, total);
        this.received.set(name, Math.min(received, total));
        this.progress.activePackage = this.labels.get(name) ?? name;
        changed = true;
      }
    }

    if (!changed) return null;
    return this.recompute(now);
  }

  /**
   * Byte progress of a download the shell runs itself (the multi-connection
   * wheel prefetch, #3411). `total <= 0` withdraws the package: its download
   * failed over to uv, which announces it again.
   */
  observeTransfer(name: string, received: number, total: number, now = Date.now()): SetupProgress {
    const key = normalizePackageName(name);
    this.progress.activityUpdatedAt = now;
    if (total <= 0) {
      this.planned.delete(key);
      this.received.delete(key);
      this.completed.delete(key);
    } else {
      if (!this.labels.has(key)) this.labels.set(key, name.trim());
      if (received > (this.received.get(key) ?? 0) || !this.planned.has(key)) this.advancedAt = now;
      this.planned.set(key, total);
      this.received.set(key, Math.min(received, total));
      if (received >= total) this.completed.add(key);
      else this.completed.delete(key);
    }
    this.refreshDownloadState();
    return this.recompute(now);
  }

  private recompute(now: number): SetupProgress {
    this.progress.completedDownloads = this.completed.size;
    const totalBytes = [...this.planned.values()].reduce((sum, value) => sum + value, 0);
    const downloadedBytes = [...this.received.values()].reduce((sum, value) => sum + value, 0);
    if (totalBytes > 0) {
      this.progress.totalBytes = totalBytes;
      this.progress.downloadedBytes = downloadedBytes;
      this.progress.estimatedBytes = true;
      this.updateRate(now, downloadedBytes, totalBytes);
    }
    return this.snapshot(now);
  }

  /**
   * The announced package a progress line belongs to. Names must match as a
   * whole token: `torch` is a prefix of `torchvision`, `nvidia-cublas` of
   * `nvidia-cublas-cu12`, `ruamel` of `ruamel.yaml`. A substring match would
   * credit one package's bytes to another, and an unannounced package to
   * whichever planned name it happens to contain.
   */
  private packageOnLine(line: string): string | undefined {
    let best: string | undefined;
    for (const candidate of this.planned.keys()) {
      const spelling = candidate.split('-').map(escapeRegExp).join('[-_.]+');
      const token = new RegExp(`(?:^|\\s)${spelling}(?=[\\s=@(]|$)`, 'i');
      if (token.test(line) && (!best || candidate.length > best.length)) best = candidate;
    }
    return best;
  }

  private refreshDownloadState(): void {
    const pending = [...this.planned.entries()]
      .filter(([name]) => !this.completed.has(name))
      .sort((left, right) => right[1] - left[1]);
    if (pending[0]) this.progress.activePackage = this.labels.get(pending[0][0]) ?? pending[0][0];
    else delete this.progress.activePackage;
    if (this.planned.size > 0) this.progress.downloadsComplete = pending.length === 0;
    else delete this.progress.downloadsComplete;
  }

  snapshot(now = Date.now()): SetupProgress {
    const snapshot: SetupProgress = { ...this.progress };
    if (snapshot.transferUpdatedAt === undefined || now - snapshot.transferUpdatedAt >= 15_000) {
      delete snapshot.bytesPerSecond;
      delete snapshot.etaSeconds;
    }
    const host = snapshot.activePackage
      ? this.hosts.get(normalizePackageName(snapshot.activePackage))
      : undefined;
    if (host) snapshot.downloadHost = host;
    const pending = this.planned.size > 0 && snapshot.downloadsComplete === false;
    const idleMs = this.advancedAt === undefined ? 0 : now - this.advancedAt;
    if (pending && idleMs >= DOWNLOAD_STALL_MS) {
      snapshot.stalled = true;
      snapshot.stalledSeconds = Math.floor(idleMs / 1000);
    }
    return snapshot;
  }

  private updateRate(now: number, bytes: number, totalBytes: number): void {
    const previous = this.samples.at(-1);
    if (!previous || bytes !== previous.bytes) {
      this.samples.push({ at: now, bytes });
      this.progress.transferUpdatedAt = now;
    }
    while (this.samples.length > 2 && now - this.samples[0].at > 15_000) this.samples.shift();
    const first = this.samples[0];
    const last = this.samples.at(-1);
    if (!first || !last || last.at <= first.at || last.bytes <= first.bytes) return;
    const bytesPerSecond = ((last.bytes - first.bytes) * 1000) / (last.at - first.at);
    this.progress.bytesPerSecond = bytesPerSecond;
    this.progress.etaSeconds = Math.max(0, Math.ceil((totalBytes - bytes) / bytesPerSecond));
  }
}
