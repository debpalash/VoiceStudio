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
}

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

/** Converts uv's human output into stable, renderer-safe progress data. */
export class SetupProgressTracker {
  private readonly planned = new Map<string, number>();
  private readonly received = new Map<string, number>();
  private readonly completed = new Set<string>();
  private readonly samples: Array<{ at: number; bytes: number }> = [];
  private progress: SetupProgress = { completedDownloads: 0 };

  reset(): void {
    this.planned.clear();
    this.received.clear();
    this.completed.clear();
    this.samples.length = 0;
    this.progress = { completedDownloads: 0 };
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
      const name = starting[1].trim();
      this.planned.set(name, parseByteSize(starting[2], starting[3]));
      this.completed.delete(name);
      this.progress.downloadsComplete = false;
      this.refreshDownloadState();
      changed = true;
    }

    const finished = line.match(/^Downloaded\s+(.+?)\s*$/i);
    if (finished) {
      const name = finished[1].trim();
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
      // Match complete package identifiers, so torchvision cannot update torch.
      const identifiers = line.slice(0, bytePair.index).match(/[a-zA-Z0-9][a-zA-Z0-9_.-]*/g) ?? [];
      const name = identifiers.reverse().find((candidate) => this.planned.has(candidate));
      if (name) {
        this.planned.set(name, total);
        this.received.set(name, Math.min(received, total));
        this.progress.activePackage = name;
        changed = true;
      }
    }

    if (!changed) return null;
    this.progress.completedDownloads = this.completed.size;
    const totalBytes = [...this.planned.values()].reduce((sum, value) => sum + value, 0);
    const downloadedBytes = [...this.received.values()].reduce((sum, value) => sum + value, 0);
    if (totalBytes > 0) {
      this.progress.totalBytes = totalBytes;
      this.progress.downloadedBytes = downloadedBytes;
      this.progress.estimatedBytes = true;
      this.updateRate(now, downloadedBytes, totalBytes);
    }
    return { ...this.progress };
  }

  private refreshDownloadState(): void {
    const pending = [...this.planned.entries()]
      .filter(([name]) => !this.completed.has(name))
      .sort((left, right) => right[1] - left[1]);
    if (pending[0]) this.progress.activePackage = pending[0][0];
    else delete this.progress.activePackage;
    if (this.planned.size > 0) this.progress.downloadsComplete = pending.length === 0;
    else delete this.progress.downloadsComplete;
  }

  snapshot(): SetupProgress {
    return { ...this.progress };
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
