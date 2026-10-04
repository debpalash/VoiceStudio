import { createHash, randomUUID } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, rename, rm, stat } from 'node:fs/promises';
import type { IncomingMessage } from 'node:http';
import { get } from 'node:https';
import { basename, dirname, join } from 'node:path';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { ProxyAgent } from 'proxy-agent';

/** Resolve the setup environment without changing the desktop process environment. */
export function setupProxyForUrl(raw: string, env: NodeJS.ProcessEnv): string {
  const url = new URL(raw);
  const value = (key: string) => env[key.toLowerCase()] || env[key.toUpperCase()] || '';
  const port = url.port || (url.protocol === 'https:' ? '443' : '80');
  for (const entry of value('no_proxy')
    .toLowerCase()
    .split(/[,\s]+/)
    .filter(Boolean)) {
    if (entry === '*') return '';
    const match = /^(.*):(\d+)$/.exec(entry);
    if (match && match[2] !== port) continue;
    const host = match?.[1] ?? entry;
    const suffix = host.replace(/^\*/, '');
    if (
      url.hostname === host ||
      (host.startsWith('.') && url.hostname === host.slice(1)) ||
      (/^[.*]/.test(host) && url.hostname.endsWith(suffix))
    )
      return '';
  }
  const proxy = value(`${url.protocol.slice(0, -1)}_proxy`) || value('all_proxy');
  if (!proxy) return '';
  const candidate = proxy.includes('://') ? proxy : `http://${proxy}`;
  try {
    const parsed = new URL(candidate);
    if (
      !['http:', 'https:', 'socks:', 'socks4:', 'socks4a:', 'socks5:', 'socks5h:'].includes(
        parsed.protocol,
      ) ||
      !parsed.hostname
    ) {
      throw new Error('unsupported');
    }
  } catch {
    // proxy-agent includes the entire URL in unsupported-protocol errors.
    // Validate before handing it credentials that could reach setup logs.
    throw new Error('Invalid or unsupported proxy URL; use HTTP, HTTPS, or SOCKS.');
  }
  return candidate;
}

const INSTALLER_ATTEMPTS = 3;
const INSTALLER_ATTEMPT_TIMEOUT_MS = 60_000;
/** Marks failures a fresh attempt can plausibly cure (resets, DNS blips, 5xx, 429). */
class TransientDownloadError extends Error {}

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

/** Download executable installer text only over HTTPS, including redirects.
 * First-run setup must survive a flaky network, so transient failures get a
 * short bounded retry; HTTP 4xx, size/redirect violations and cancellation do not. */
export async function downloadRuntimeInstaller(
  url: string,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal,
  backoffMs = 1_000,
): Promise<string> {
  const agent = new ProxyAgent({ getProxyForUrl: (target) => setupProxyForUrl(target, env) });
  try {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await downloadOnce(url, agent, signal);
      } catch (error) {
        signal.throwIfAborted();
        if (attempt >= INSTALLER_ATTEMPTS || !isTransient(error)) throw error;
        await pause(backoffMs * attempt, signal);
      }
    }
  } finally {
    agent.destroy();
  }
}

function isTransient(error: unknown): boolean {
  if (error instanceof TransientDownloadError) return true;
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  // The per-attempt timeout surfaces as an AbortError/TimeoutError; user cancel was
  // already rethrown by the caller.
  const name = (error as Error | undefined)?.name;
  return (
    name === 'TimeoutError' ||
    name === 'AbortError' ||
    [
      'ECONNRESET',
      'ECONNREFUSED',
      'ETIMEDOUT',
      'EAI_AGAIN',
      'ENOTFOUND',
      'EPIPE',
      'ECONNABORTED',
    ].includes(code ?? '')
  );
}

async function downloadOnce(url: string, agent: ProxyAgent, signal: AbortSignal): Promise<string> {
  const bounded = AbortSignal.any([signal, AbortSignal.timeout(INSTALLER_ATTEMPT_TIMEOUT_MS)]);
  async function download(target: string, redirects = 0): Promise<string> {
    if (new URL(target).protocol !== 'https:') throw new Error('uv installer requires HTTPS');
    bounded.throwIfAborted();
    return new Promise((resolve, reject) => {
      const request = get(target, { agent, signal: bounded }, (response) => {
        const status = response.statusCode ?? 0;
        if ([301, 302, 303, 307, 308].includes(status)) {
          response.resume();
          if (!response.headers.location || redirects >= 5) {
            reject(new Error('uv installer redirect limit exceeded'));
          } else {
            try {
              resolve(download(new URL(response.headers.location, target).href, redirects + 1));
            } catch (error) {
              reject(error);
            }
          }
          return;
        }
        if (status !== 200) {
          response.resume();
          const message = `uv installer download failed (${status})`;
          reject(
            status >= 500 || status === 429
              ? new TransientDownloadError(message)
              : new Error(message),
          );
          return;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > 2 * 1024 * 1024) {
            response.destroy(new Error('uv installer exceeds size limit'));
          } else chunks.push(chunk);
        });
        response.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
        response.on('error', reject);
      });
      request.on('error', reject);
    });
  }
  return download(url);
}

const MAX_ARCHIVE_BYTES = 200 * 1024 * 1024;

export async function downloadRuntimeArchive(
  url: string,
  destination: string,
  expectedSha256: string,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal,
): Promise<void> {
  function archiveUrl(raw: string, base?: URL): URL {
    let parsed: URL;
    try {
      parsed = new URL(raw, base);
    } catch {
      throw new Error('Invalid runtime archive URL');
    }
    if (parsed.protocol !== 'https:') throw new Error('Runtime archive requires HTTPS');
    if (parsed.username || parsed.password)
      throw new Error('Runtime archive URL cannot contain credentials');
    return parsed;
  }

  archiveUrl(url);
  if (!/^[a-f\d]{64}$/i.test(expectedSha256)) throw new Error('Invalid runtime archive SHA-256');
  signal.throwIfAborted();

  const cached = await stat(destination).catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  if (cached?.isFile() && cached.size <= MAX_ARCHIVE_BYTES) {
    const digest = createHash('sha256');
    let size = 0;
    for await (const chunk of createReadStream(destination, { signal })) {
      size += chunk.length;
      if (size > MAX_ARCHIVE_BYTES) break;
      digest.update(chunk);
    }
    signal.throwIfAborted();
    if (size <= MAX_ARCHIVE_BYTES && digest.digest('hex') === expectedSha256.toLowerCase()) return;
  }

  const agent = new ProxyAgent({ getProxyForUrl: (target) => setupProxyForUrl(target, env) });
  async function responseFor(raw: string, base?: URL, redirects = 0): Promise<IncomingMessage> {
    const target = archiveUrl(raw, base);
    signal.throwIfAborted();
    return new Promise((resolve, reject) => {
      try {
        const request = get(target, { agent, signal }, (response) => {
          const status = response.statusCode ?? 0;
          if ([301, 302, 303, 307, 308].includes(status)) {
            response.destroy();
            if (!response.headers.location || redirects >= 5) {
              reject(new Error('Runtime archive redirect limit exceeded'));
            } else {
              resolve(responseFor(response.headers.location, target, redirects + 1));
            }
            return;
          }
          if (status !== 200) {
            response.destroy();
            reject(new Error(`Runtime archive download failed (${status})`));
            return;
          }
          resolve(response);
        });
        request.on('error', () => {
          reject(signal.aborted ? signal.reason : new Error('Runtime archive request failed'));
        });
      } catch {
        reject(new Error('Runtime archive request failed'));
      }
    });
  }

  try {
    const response = await responseFor(url);
    if (Number(response.headers['content-length']) > MAX_ARCHIVE_BYTES) {
      response.destroy();
      throw new Error('Runtime archive exceeds size limit');
    }
    await mkdir(dirname(destination), { recursive: true });
    const temporary = join(dirname(destination), `.${basename(destination)}.${randomUUID()}.tmp`);
    const digest = createHash('sha256');
    const limitError = new Error('Runtime archive exceeds size limit');
    let size = 0;
    try {
      try {
        await pipeline(
          response,
          new Transform({
            transform(chunk: Buffer, _encoding, callback) {
              size += chunk.length;
              if (size > MAX_ARCHIVE_BYTES) {
                callback(limitError);
              } else {
                digest.update(chunk);
                callback(null, chunk);
              }
            },
          }),
          createWriteStream(temporary, { flags: 'wx' }),
          { signal },
        );
      } catch (error) {
        if (error === limitError) throw error;
        signal.throwIfAborted();
        throw new Error('Runtime archive transfer failed');
      }
      signal.throwIfAborted();
      if (digest.digest('hex') !== expectedSha256.toLowerCase()) {
        throw new Error('Runtime archive SHA-256 mismatch');
      }
      await rename(temporary, destination);
    } finally {
      await rm(temporary, { force: true });
    }
  } finally {
    agent.destroy();
  }
}
