/**
 * Multi-connection download of the largest locked wheels (#3411, #3444).
 *
 * `uv sync --frozen` downloads each artifact over a single HTTP connection.
 * Networks and CDNs that cap the rate of each connection (common behind ISP
 * shaping, VPNs and on long international routes) make a speed test, which
 * opens many connections, look fast while setup crawls at about 1 MB/s. On
 * Windows the CUDA `torch` wheel is one ~3 GB file, so that one connection
 * sets the total time; uv's file-level parallelism cannot help it.
 *
 * Before the sync, the wheels uv would actually download (it says which ones
 * in `--dry-run -v` output, cache hits excluded) that are large enough to
 * matter are fetched here in byte ranges over several connections, verified
 * against the sha256 pinned in uv.lock, and handed to uv through `file://`
 * URLs in the staged lock. uv still checks every file against the lock.
 *
 * Every step is an optimisation: a server without range support, an
 * unexpected dry-run format or any download error falls back to uv's own
 * download of that wheel. Unfinished downloads are kept beside the runtime
 * and resumed by the next attempt; Cancel and Retry no longer start a 3 GB
 * wheel from zero. No new hosts are contacted: the URLs come from uv.lock
 * (or the regional mirror the user chose or the Auto speed check picked).
 */

import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, open, readFile, readdir, rm, stat, statfs, writeFile } from 'node:fs/promises';
import { get as httpsGet } from 'node:https';
import type { IncomingMessage } from 'node:http';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ProxyAgent } from 'proxy-agent';
import { setupProxyForUrl } from './runtime-download';
import { PYPI_FILES_ORIGIN } from './pypi-mirror';
import { FILE_RENAME, renameWithRetry } from './rename-retry';

/** Wheels smaller than this gain little from extra connections; uv fetches them. */
export const PREFETCH_MIN_BYTES = 64 * 1024 ** 2;
export const PREFETCH_CHUNK_BYTES = 8 * 1024 ** 2;
export const DEFAULT_PREFETCH_CONNECTIONS = 8;
const MAX_PREFETCH_CONNECTIONS = 16;
const CHUNK_ATTEMPTS = 4;
const IDLE_TIMEOUT_MS = 30_000;
const PROGRESS_INTERVAL_MS = 500;

/** Connections per wheel from `OMNIVOICE_SETUP_CONNECTIONS`; 0 or 1 turns the prefetch off. */
export function prefetchConnections(value = process.env.OMNIVOICE_SETUP_CONNECTIONS): number {
  if (value === undefined || value.trim() === '') return DEFAULT_PREFETCH_CONNECTIONS;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) return DEFAULT_PREFETCH_CONNECTIONS;
  return Math.min(parsed, MAX_PREFETCH_CONNECTIONS);
}

export interface LockedWheel {
  name: string;
  version: string;
  url: string;
  filename: string;
  sha256: string;
  /** Bytes, or 0 when the lock does not say (PyTorch's index publishes no sizes). */
  size: number;
}

export interface WheelHost {
  platform: NodeJS.Platform;
  arch: string;
  /** CPython minor version, e.g. 11 for 3.11. */
  pythonMinor: number;
}

function normalizeName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[-_.]+/g, '-');
}

/** Every wheel artifact in a uv.lock, with the name and version of its package. */
export function lockedWheels(lockText: string): LockedWheel[] {
  const wheels: LockedWheel[] = [];
  for (const block of lockText.split(/^\[\[package\]\]\s*$/m).slice(1)) {
    const name = /^name = "([^"]+)"$/m.exec(block)?.[1];
    const version = /^version = "([^"]+)"$/m.exec(block)?.[1];
    if (!name || !version) continue;
    for (const match of block.matchAll(
      /\{ url = "([^"]+\.whl)", hash = "sha256:([0-9a-f]{64})"(?:, size = (\d+))?/g,
    )) {
      const url = match[1]!;
      let filename: string;
      try {
        filename = decodeURIComponent(new URL(url).pathname.split('/').at(-1) ?? '');
      } catch {
        continue;
      }
      wheels.push({ name, version, url, filename, sha256: match[2]!, size: Number(match[3] ?? 0) });
    }
  }
  return wheels;
}

/** The interpreter and the uncached distributions a `uv -v sync --dry-run` reported. */
export function parseDryRun(output: string): {
  pythonMinor: number | null;
  uncached: Array<{ name: string; version: string }>;
} {
  const python = /Using CPython 3\.(\d+)/.exec(output);
  const uncached = [
    ...output.matchAll(/Identified uncached distribution: ([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)/g),
  ].map((match) => ({ name: match[1]!, version: match[2]! }));
  return { pythonMinor: python ? Number(python[1]) : null, uncached };
}

function platformTagMatches(tag: string, host: WheelHost): boolean {
  if (tag === 'any') return true;
  if (host.platform === 'win32') return tag === 'win_amd64';
  if (host.platform === 'linux') {
    const machine = host.arch === 'arm64' ? 'aarch64' : host.arch === 'x64' ? 'x86_64' : null;
    if (!machine) return false;
    return new RegExp(`^(manylinux(_\\d+_\\d+|1|2010|2014)|linux)_${machine}$`).test(tag);
  }
  if (host.platform === 'darwin') {
    return host.arch === 'arm64' && /^macosx_\d+_\d+_(arm64|universal2)$/.test(tag);
  }
  return false;
}

/**
 * Whether a wheel filename installs on this host. Only the common tag shapes
 * are recognised; anything else counts as "not this host", which leaves that
 * wheel to uv.
 */
export function wheelMatchesHost(filename: string, host: WheelHost): boolean {
  const parts = filename.replace(/\.whl$/, '').split('-');
  if (parts.length < 5) return false;
  const [pythonTags, abiTags, platformTags] = parts.slice(-3).map((part) => part.split('.'));
  const cp = `cp3${host.pythonMinor}`;
  const abiOk = abiTags!.some((abi) => abi === 'none' || abi === 'abi3' || abi === cp);
  const pythonOk = pythonTags!.some((tag) => {
    if (tag === cp || tag === 'py3' || tag === `py3${host.pythonMinor}`) return true;
    const stable = /^cp3(\d+)$/.exec(tag);
    return Boolean(stable && abiTags!.includes('abi3') && Number(stable[1]) <= host.pythonMinor);
  });
  return abiOk && pythonOk && platformTags!.some((tag) => platformTagMatches(tag, host));
}

/**
 * The large wheels uv is about to download. A distribution qualifies only when
 * exactly one of its locked wheels fits this host: an ambiguous choice (two
 * manylinux levels, say) is uv's to make, and a wrong guess would download
 * the same package twice.
 */
export function planWheelPrefetch(
  lockText: string,
  dryRunOutput: string,
  host: Omit<WheelHost, 'pythonMinor'> & { pythonMinor?: number },
  minBytes = PREFETCH_MIN_BYTES,
): LockedWheel[] {
  const { pythonMinor, uncached } = parseDryRun(dryRunOutput);
  const minor = pythonMinor ?? host.pythonMinor;
  if (minor === undefined || !uncached.length) return [];
  const resolvedHost: WheelHost = { ...host, pythonMinor: minor };
  const wheels = lockedWheels(lockText);
  const plan: LockedWheel[] = [];
  for (const { name, version } of uncached) {
    const key = normalizeName(name);
    const candidates = wheels.filter(
      (wheel) =>
        normalizeName(wheel.name) === key &&
        wheel.version === version &&
        wheelMatchesHost(wheel.filename, resolvedHost),
    );
    // An unknown size (0) is measured before downloading.
    const [only] = candidates;
    if (candidates.length === 1 && (only!.size === 0 || only!.size >= minBytes)) plan.push(only!);
  }
  return plan;
}

/** Point the given locked artifact URLs at local files; nothing else changes. */
export function localizeLockUrls(lock: string, local: ReadonlyMap<string, string>): string {
  let result = lock;
  for (const [url, fileUrl] of local) {
    result = result.split(`url = "${url}"`).join(`url = "${fileUrl}"`);
  }
  return result;
}

/** A readable body for bytes `start..end` (inclusive) of `url`, or a rejection. */
export type RangeTransport = (
  url: string,
  start: number,
  end: number,
  signal: AbortSignal,
) => Promise<AsyncIterable<Buffer>>;

/** The total size of `url` in bytes. */
export type SizeProbe = (url: string, signal: AbortSignal) => Promise<number>;

/** The server answered a range request with the whole file: segmenting cannot work. */
export class RangeUnsupportedError extends Error {
  constructor(url: string) {
    super(`${new URL(url).host} does not serve byte ranges`);
  }
}

class HttpStatusError extends Error {
  constructor(
    readonly status: number,
    host: string,
  ) {
    super(`${host} answered HTTP ${status}`);
  }
}

/** HTTPS range requests through the same proxy settings uv receives. */
export function httpsRangeTransport(
  env: NodeJS.ProcessEnv,
  request: typeof httpsGet = httpsGet,
): { transport: RangeTransport; probeSize: SizeProbe; close: () => void } {
  const agent = new ProxyAgent({ getProxyForUrl: (target) => setupProxyForUrl(target, env) });
  const ranged = (
    url: string,
    start: number,
    end: number,
    signal: AbortSignal,
  ): Promise<{ body: IncomingMessage; total: number }> => {
    const fetchRange = (
      target: string,
      redirects: number,
    ): Promise<{ body: IncomingMessage; total: number }> =>
      new Promise((resolve, reject) => {
        const parsed = new URL(target);
        if (parsed.protocol !== 'https:') {
          reject(new Error('Wheel downloads require HTTPS'));
          return;
        }
        const req = request(
          target,
          {
            agent,
            signal,
            // Some mirrors (mirrors.aliyun.com) refuse requests without a User-Agent.
            headers: { Range: `bytes=${start}-${end}`, 'User-Agent': 'VoiceStudio-setup' },
          },
          (response) => {
            const status = response.statusCode ?? 0;
            if ([301, 302, 303, 307, 308].includes(status) && response.headers.location) {
              response.resume();
              if (redirects >= 5) reject(new Error('Too many redirects'));
              else
                resolve(fetchRange(new URL(response.headers.location, target).href, redirects + 1));
              return;
            }
            if (status === 200) {
              response.destroy();
              reject(new RangeUnsupportedError(target));
              return;
            }
            if (status !== 206) {
              response.resume();
              reject(new HttpStatusError(status, parsed.host));
              return;
            }
            const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(
              String(response.headers['content-range']).trim(),
            );
            if (!range || Number(range[1]) !== start || Number(range[2]) !== end) {
              response.destroy();
              reject(new RangeUnsupportedError(target));
              return;
            }
            response.setTimeout(IDLE_TIMEOUT_MS, () =>
              response.destroy(new Error(`No data from ${parsed.host} for 30 s`)),
            );
            resolve({ body: response, total: Number(range[3]) });
          },
        );
        req.setTimeout(IDLE_TIMEOUT_MS, () =>
          req.destroy(new Error(`No answer from ${parsed.host} for 30 s`)),
        );
        req.on('error', reject);
      });
    return fetchRange(url, 0);
  };
  return {
    transport: async (url, start, end, signal) => (await ranged(url, start, end, signal)).body,
    probeSize: async (url, signal) => {
      const { body, total } = await ranged(url, 0, 0, signal);
      body.resume();
      return total;
    },
    close: () => agent.destroy(),
  };
}

interface PartState {
  url: string;
  sha256: string;
  size: number;
  chunkBytes: number;
  done: number[];
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/** SHA-256 of a file on disk as lowercase hex, read chunk by chunk rather than all at once. */
export async function fileSha256(path: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const digest = createHash('sha256');
    const source = createReadStream(path);
    source.on('data', (chunk) => digest.update(chunk));
    source.once('error', reject);
    source.once('end', () => resolve(digest.digest('hex')));
  });
}

export interface SegmentedDownloadOptions {
  /** Candidate URLs for the same file, tried in order (a mirror, then the origin). */
  urls: readonly string[];
  sha256: string;
  size: number;
  connections: number;
  transport: RangeTransport;
  signal: AbortSignal;
  chunkBytes?: number;
  onProgress?: (received: number) => void;
  /** Backoff base between chunk retries; tests shorten it. */
  retryDelayMs?: number;
}

/**
 * Download one file in byte ranges over `connections` connections into
 * `dest`, resuming the chunks a previous attempt finished, and verify its
 * sha256. Rejects (keeping finished chunks) when the file cannot be fetched.
 */
export async function downloadSegmented(
  dest: string,
  options: SegmentedDownloadOptions,
): Promise<void> {
  const { size, sha256, signal } = options;
  const chunkBytes = options.chunkBytes ?? PREFETCH_CHUNK_BYTES;
  const retryDelayMs = options.retryDelayMs ?? 1_000;
  const part = `${dest}.part`;
  const statePath = `${part}.json`;
  const chunkCount = Math.max(1, Math.ceil(size / chunkBytes));
  const key = options.urls.at(-1) ?? '';
  let state: PartState | null = null;
  try {
    const saved = JSON.parse(await readFile(statePath, 'utf8')) as PartState;
    const partInfo = await stat(part);
    if (
      saved.url === key &&
      saved.sha256 === sha256 &&
      saved.size === size &&
      saved.chunkBytes === chunkBytes &&
      Array.isArray(saved.done) &&
      partInfo.size === size
    )
      state = saved;
  } catch {
    state = null;
  }
  if (!state) {
    await rm(part, { force: true });
    const created = await open(part, 'w');
    try {
      await created.truncate(size);
    } finally {
      await created.close();
    }
    state = { url: key, sha256, size, chunkBytes, done: [] };
    await writeFile(statePath, JSON.stringify(state));
  }
  const done = new Set(state.done.filter((index) => index >= 0 && index < chunkCount));
  const inFlight = new Map<number, number>();
  const chunkLength = (index: number) => Math.min(chunkBytes, size - index * chunkBytes);
  let reportedAt = 0;
  const report = (force = false) => {
    const now = Date.now();
    if (!force && now - reportedAt < PROGRESS_INTERVAL_MS) return;
    reportedAt = now;
    let received = 0;
    for (const index of done) received += chunkLength(index);
    for (const bytes of inFlight.values()) received += bytes;
    options.onProgress?.(received);
  };
  report(true);

  const internal = new AbortController();
  const combined = AbortSignal.any([signal, internal.signal]);
  const queue = Array.from({ length: chunkCount }, (_, index) => index).filter(
    (index) => !done.has(index),
  );
  let urlIndex = 0;
  let saving = Promise.resolve();
  const saveState = () => {
    const snapshot = JSON.stringify({ ...state, done: [...done].sort((a, b) => a - b) });
    saving = saving.then(() => writeFile(statePath, snapshot)).catch(() => undefined);
    return saving;
  };

  const handle = await open(part, 'r+');
  try {
    const fetchChunk = async (index: number) => {
      const start = index * chunkBytes;
      const end = start + chunkLength(index) - 1;
      for (let attempt = 1; ; attempt += 1) {
        combined.throwIfAborted();
        const url = options.urls[urlIndex]!;
        let position = start;
        inFlight.set(index, 0);
        try {
          const body = await options.transport(url, start, end, combined);
          for await (const piece of body) {
            const buffer = Buffer.isBuffer(piece) ? piece : Buffer.from(piece);
            if (position + buffer.length > end + 1) throw new Error('Server sent too many bytes');
            await handle.write(buffer, 0, buffer.length, position);
            position += buffer.length;
            inFlight.set(index, position - start);
            report();
          }
          if (position !== end + 1) throw new Error('Connection closed early');
          inFlight.delete(index);
          done.add(index);
          await saveState();
          report();
          return;
        } catch (error) {
          inFlight.delete(index);
          if (signal.aborted || internal.signal.aborted) throw error;
          // A mirror that cannot serve this file: move every worker to the next URL.
          // Workers that were already on their way to the old URL retry on the new one.
          const status = error instanceof HttpStatusError ? error.status : 0;
          const failedAt = options.urls.indexOf(url);
          if (
            (error instanceof RangeUnsupportedError || (status >= 400 && status < 500)) &&
            failedAt < options.urls.length - 1
          ) {
            urlIndex = Math.max(urlIndex, failedAt + 1);
            continue;
          }
          if (error instanceof RangeUnsupportedError) throw error;
          if (status >= 400 && status < 500 && status !== 408 && status !== 429) throw error;
          if (attempt >= CHUNK_ATTEMPTS) throw error;
          await pause(retryDelayMs * 2 ** (attempt - 1), combined);
        }
      }
    };
    const worker = async () => {
      for (let index = queue.shift(); index !== undefined; index = queue.shift()) {
        await fetchChunk(index);
      }
    };
    const workers = Array.from(
      { length: Math.max(1, Math.min(options.connections, queue.length || 1)) },
      () =>
        worker().catch((error: unknown) => {
          internal.abort(error);
          throw error;
        }),
    );
    const results = await Promise.allSettled(workers);
    const failed = results.find(
      (result): result is PromiseRejectedResult => result.status === 'rejected',
    );
    if (failed) {
      signal.throwIfAborted();
      throw internal.signal.reason ?? failed.reason;
    }
  } finally {
    await handle.close();
    await saving;
  }
  report(true);
  signal.throwIfAborted();
  if ((await fileSha256(part)) !== sha256) {
    await rm(part, { force: true });
    await rm(statePath, { force: true });
    throw new Error('The downloaded file does not match the hash pinned in uv.lock');
  }
  // An antivirus scanning the finished file briefly locks it on Windows.
  await renameWithRetry(part, dest, FILE_RENAME);
  await rm(statePath, { force: true });
}

export interface PrefetchOptions {
  /** Where finished and unfinished wheels live between attempts. */
  dir: string;
  connections: number;
  transport: RangeTransport;
  /** Measures wheels the lock lists without a size. */
  probeSize?: SizeProbe;
  signal: AbortSignal;
  /** Regional PyPI file mirror prefix, tried before files.pythonhosted.org. */
  mirror?: string | null;
  /** Free space the rest of the install still needs after the prefetch. */
  reserveBytes?: number;
  log?: (line: string) => void;
  transfer?: (name: string, received: number, total: number) => void;
  chunkBytes?: number;
  retryDelayMs?: number;
  freeBytes?: (dir: string) => Promise<number>;
}

function sizeText(bytes: number): string {
  return bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
    : `${Math.round(bytes / 1024 ** 2)} MB`;
}

function reason(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.split(/\r?\n/)[0]!.slice(0, 200);
}

async function defaultFreeBytes(dir: string): Promise<number> {
  const info = await statfs(dir);
  return info.bavail * info.bsize;
}

/**
 * Fetch `wheels` with {@link downloadSegmented} and return the lock URL to
 * `file://` URL map for the ones that arrived intact. Wheels that fail are
 * left out (uv downloads them); a cancellation rethrows.
 */
export async function prefetchWheels(
  planned: readonly LockedWheel[],
  options: PrefetchOptions,
): Promise<Map<string, string>> {
  const local = new Map<string, string>();
  const log = options.log ?? (() => {});
  if (!planned.length || options.connections < 2) return local;
  const wheels: LockedWheel[] = [];
  for (const wheel of planned) {
    if (wheel.size > 0) {
      wheels.push(wheel);
      continue;
    }
    const size = await options.probeSize?.(wheel.url, options.signal).catch(() => 0);
    options.signal.throwIfAborted();
    if (size && size >= PREFETCH_MIN_BYTES) wheels.push({ ...wheel, size });
  }
  if (!wheels.length) return local;
  await mkdir(options.dir, { recursive: true });
  const folder = (wheel: LockedWheel) => wheel.sha256.slice(0, 16);
  // Leftovers from a lock this install no longer uses only take space.
  const keep = new Set(wheels.map(folder));
  for (const entry of await readdir(options.dir).catch(() => [] as string[])) {
    if (!keep.has(entry)) await rm(join(options.dir, entry), { recursive: true, force: true });
  }
  const total = wheels.reduce((sum, wheel) => sum + wheel.size, 0);
  const free = await (options.freeBytes ?? defaultFreeBytes)(options.dir).catch(() => Infinity);
  if (free < total + (options.reserveBytes ?? 0)) {
    log(
      'Skipping multi-connection downloads: not enough free space to keep the wheels until uv installs them.',
    );
    return local;
  }
  for (const wheel of wheels) {
    options.signal.throwIfAborted();
    const target = join(options.dir, folder(wheel));
    await mkdir(target, { recursive: true });
    const dest = join(target, wheel.filename);
    const urls =
      options.mirror && wheel.url.startsWith(PYPI_FILES_ORIGIN)
        ? [options.mirror + wheel.url.slice(PYPI_FILES_ORIGIN.length), wheel.url]
        : [wheel.url];
    const fileUrl = `${pathToFileURL(target).href}/${encodeURIComponent(wheel.filename)}`;
    const ready = await stat(dest).then(
      (info) => info.isFile() && info.size === wheel.size,
      () => false,
    );
    if (ready && (await fileSha256(dest)) === wheel.sha256) {
      log(`Reusing the ${wheel.name} wheel downloaded by an earlier attempt.`);
      options.transfer?.(wheel.name, wheel.size, wheel.size);
      local.set(wheel.url, fileUrl);
      continue;
    }
    const host = new URL(urls[0]!).host;
    // Never start a line with uv's `Downloading`/`Downloaded`: the progress tracker parses those.
    log(
      `Fetching ${wheel.name} (${sizeText(wheel.size)}) from ${host} over ${options.connections} connections.`,
    );
    const started = Date.now();
    try {
      await downloadSegmented(dest, {
        urls,
        sha256: wheel.sha256,
        size: wheel.size,
        connections: options.connections,
        transport: options.transport,
        signal: options.signal,
        chunkBytes: options.chunkBytes,
        retryDelayMs: options.retryDelayMs,
        onProgress: (received) => options.transfer?.(wheel.name, received, wheel.size),
      });
      const seconds = Math.max(1, (Date.now() - started) / 1000);
      log(
        `Fetched ${wheel.name} in ${Math.round(seconds)} s (${sizeText(wheel.size / seconds)}/s).`,
      );
      local.set(wheel.url, fileUrl);
    } catch (error) {
      options.signal.throwIfAborted();
      log(
        `Multi-connection download of ${wheel.name} failed (${reason(error)}); uv will download it instead.`,
      );
      // Report the wheel as handled so the progress total does not wait on it;
      // uv announces its own download of it next.
      options.transfer?.(wheel.name, 0, 0);
    }
  }
  return local;
}

/** Remove prefetched wheels once uv has installed them; they are in uv's cache now. */
export async function clearPrefetchedWheels(dir: string): Promise<void> {
  await rm(dir, { recursive: true, force: true }).catch(() => undefined);
}
