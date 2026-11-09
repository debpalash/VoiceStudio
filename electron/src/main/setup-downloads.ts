import { readdir, rm, stat } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * First-run download observability (#3411).
 *
 * uv prints no byte progress when its output is piped, so a multi-gigabyte
 * wheel looks frozen for minutes. While a wheel downloads, uv streams it into
 * a `.tmp*` directory at the root of its cache and renames that directory into
 * the cache once the wheel is complete. Those directories are the only
 * cross-platform signal that bytes are still arriving.
 */

const PYPI_FILES = 'files.pythonhosted.org';

function isUvTemp(name: string): boolean {
  return name.startsWith('.tmp');
}

/**
 * Package name to every host its locked artifacts download from. `uv sync
 * --frozen` fetches the exact URLs in uv.lock; index and mirror settings
 * (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`) do not redirect them.
 */
export function lockArtifactHosts(lockText: string): Map<string, string[]> {
  const hosts = new Map<string, string[]>();
  let current: string | undefined;
  for (const line of lockText.split(/\r?\n/)) {
    if (line === '[[package]]') {
      current = undefined;
      continue;
    }
    const name = /^name = "([^"]+)"$/.exec(line);
    if (name) {
      current = name[1];
      continue;
    }
    if (!current) continue;
    for (const match of line.matchAll(/\burl = "https:\/\/([^/"]+)\//g)) {
      const list = hosts.get(current) ?? [];
      if (!list.includes(match[1]!)) list.push(match[1]!);
      hosts.set(current, list);
    }
  }
  return hosts;
}

/**
 * The single host each package downloads from on this machine. Only the
 * PyTorch family is locked to more than one host: PyTorch's own index on
 * Linux/Windows x64 (CUDA and CPU builds alike), PyPI everywhere else.
 */
export function setupDownloadHosts(
  lockText: string,
  options: { pytorchIndex: boolean; overrides?: Record<string, string> },
): Map<string, string> {
  const resolved = new Map<string, string>();
  for (const [name, hosts] of lockArtifactHosts(lockText)) {
    const off = hosts.filter((host) => host !== PYPI_FILES);
    const pick =
      hosts.length === 1
        ? hosts[0]
        : options.pytorchIndex
          ? (off[0] ?? hosts[0])
          : hosts.includes(PYPI_FILES)
            ? PYPI_FILES
            : hosts[0];
    if (pick) resolved.set(name, pick);
  }
  for (const [name, host] of Object.entries(options.overrides ?? {})) resolved.set(name, host);
  return resolved;
}

async function treeBytes(path: string): Promise<number> {
  let total = 0;
  let entries;
  try {
    entries = await readdir(path, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const entry of entries) {
    const child = join(path, entry.name);
    if (entry.isDirectory()) total += await treeBytes(child);
    else if (entry.isFile())
      total += await stat(child).then(
        (info) => info.size,
        () => 0,
      );
  }
  return total;
}

/** Bytes uv has written for downloads that are still in flight. */
export async function inFlightDownloadBytes(cacheDir: string): Promise<number> {
  let entries;
  try {
    entries = await readdir(cacheDir, { withFileTypes: true });
  } catch {
    return 0;
  }
  let total = 0;
  for (const entry of entries) {
    if (!isUvTemp(entry.name)) continue;
    const path = join(cacheDir, entry.name);
    total += entry.isDirectory()
      ? await treeBytes(path)
      : await stat(path).then(
          (info) => info.size,
          () => 0,
        );
  }
  return total;
}

/**
 * uv cannot resume a download, and a cancelled or killed uv leaves its partial
 * wheel behind as a `.tmp*` directory (gigabytes for a CUDA wheel). Remove
 * those before the next attempt so repeated Cancel/Retry cannot fill the disk.
 * Finished wheels live elsewhere in the cache and are reused untouched. Only
 * called while no uv process uses this app-private cache.
 */
export async function pruneInterruptedUvDownloads(cacheDir: string): Promise<number> {
  let entries;
  try {
    entries = await readdir(cacheDir);
  } catch {
    return 0;
  }
  let removed = 0;
  for (const name of entries.filter(isUvTemp)) {
    await rm(join(cacheDir, name), { recursive: true, force: true }).then(
      () => (removed += 1),
      () => undefined,
    );
  }
  return removed;
}

export function formatTransferBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(0, Math.round(bytes / 1024))} KB`;
}

export interface DownloadSnapshot {
  downloadedBytes?: number;
  totalBytes?: number;
  bytesPerSecond?: number;
  transferUpdatedAt?: number;
  downloadsComplete?: boolean;
  downloadHost?: string;
  stalled?: boolean;
  stalledSeconds?: number;
}

export const DOWNLOAD_SUMMARY_MS = 30_000;

/**
 * Setup-log lines that answer "which download is slow?" from a bug report:
 * each new source host once, a progress summary every 30 s, and the moment a
 * download stalls. Lines never start with uv's own `Downloading`/`Downloaded`
 * forms, which the progress tracker parses.
 */
export class SetupDownloadReporter {
  private readonly hosts = new Set<string>();
  private summaryAt: number | undefined;
  private stalled = false;

  lines(snapshot: DownloadSnapshot, now = Date.now()): string[] {
    const lines: string[] = [];
    const host = snapshot.downloadHost;
    if (host && !this.hosts.has(host)) {
      this.hosts.add(host);
      lines.push(`Download source: ${host}`);
    }
    const pending = snapshot.downloadsComplete === false;
    if (snapshot.stalled && !this.stalled) {
      lines.push(
        `Download stalled: no data from ${host ?? 'the download server'} for ${snapshot.stalledSeconds ?? 0} s`,
      );
    } else if (!snapshot.stalled && this.stalled && pending) {
      lines.push(`Download resumed${host ? ` from ${host}` : ''}`);
    }
    this.stalled = Boolean(snapshot.stalled);
    if (!pending || !snapshot.totalBytes) {
      this.summaryAt = undefined;
      return lines;
    }
    if (this.summaryAt === undefined) this.summaryAt = now;
    else if (now - this.summaryAt >= DOWNLOAD_SUMMARY_MS) {
      this.summaryAt = now;
      const rate =
        snapshot.bytesPerSecond && !snapshot.stalled
          ? ` at ${formatTransferBytes(snapshot.bytesPerSecond)}/s`
          : '';
      lines.push(
        `Download progress: ${formatTransferBytes(snapshot.downloadedBytes ?? 0)} of ~${formatTransferBytes(snapshot.totalBytes)}${rate}${host ? ` from ${host}` : ''}`,
      );
    }
    return lines;
  }
}
