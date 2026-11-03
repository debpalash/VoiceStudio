import { get as httpsGet } from 'node:https';
import type { IncomingMessage } from 'node:http';
import { ProxyAgent } from 'proxy-agent';
import { setupProxyForUrl } from './runtime-download';
import { lockedWheels, wheelMatchesHost, type WheelHost } from './wheel-prefetch';
import { AUTO_PYPI_MIRROR, PYPI_FILES_ORIGIN } from './pypi-mirror';
import { scrubSetupText } from './setup-log';
import type { SetupProgress } from './setup-progress';
import type { SetupConnectionCheck } from '../preload/index.d';

export const SETUP_CHECK_BYTES = 256 * 1024;
export const SETUP_CHECK_TIMEOUT_MS = 15_000;

/** Select only an artifact from the bundled lock, never a renderer-supplied URL. */
export function setupProbeUrl(
  lock: string,
  progress: SetupProgress,
  host: WheelHost & { torchVariant?: 'default' | 'cpu' | 'rocm' },
): string | null {
  if (!progress.activePackage || !progress.downloadHost) return null;
  const normalized = (name: string) => name.toLowerCase().replace(/[-_.]+/g, '-');
  const wheel = lockedWheels(lock).find((item) => {
    if (
      normalized(item.name) !== normalized(progress.activePackage!) ||
      !wheelMatchesHost(item.filename, host)
    )
      return false;
    const url = new URL(item.url);
    if (host.torchVariant && /(^|\.)pytorch\.org$/.test(url.hostname)) {
      const path =
        host.torchVariant === 'cpu'
          ? '/whl/cpu/'
          : host.torchVariant === 'rocm'
            ? '/whl/rocm'
            : '/whl/cu';
      if (!url.pathname.startsWith(path)) return false;
    }
    return (
      url.protocol === 'https:' &&
      (url.host === progress.downloadHost ||
        (item.url.startsWith(PYPI_FILES_ORIGIN) &&
          progress.downloadHost === new URL(AUTO_PYPI_MIRROR).host))
    );
  });
  if (!wheel) return null;
  return wheel.url.startsWith(PYPI_FILES_ORIGIN) &&
    progress.downloadHost === new URL(AUTO_PYPI_MIRROR).host
    ? AUTO_PYPI_MIRROR + wheel.url.slice(PYPI_FILES_ORIGIN.length)
    : wheel.url;
}

/** A user-requested sample through setup's proxy; bounded even before a socket connects. */
export async function checkSetupConnection(
  url: string,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal = AbortSignal.timeout(SETUP_CHECK_TIMEOUT_MS),
  request: typeof httpsGet = httpsGet,
): Promise<SetupConnectionCheck> {
  const host = new URL(url).host;
  const started = performance.now();
  const agent = new ProxyAgent({ getProxyForUrl: (target) => setupProxyForUrl(target, env) });
  let bytes = 0;
  const responses = new Set<IncomingMessage>();
  const sample = (target: string, redirects = 0): Promise<void> =>
    new Promise((resolve, reject) => {
      if (new URL(target).protocol !== 'https:')
        return reject(new Error('Download check requires HTTPS'));
      if (signal.aborted) return reject(signal.reason);
      const req = request(
        target,
        {
          agent,
          signal,
          headers: { Range: `bytes=0-${SETUP_CHECK_BYTES - 1}`, 'User-Agent': 'VoiceStudio-setup' },
        },
        (response) => {
          responses.add(response);
          response.on('error', reject);
          const status = response.statusCode ?? 0;
          if ([301, 302, 303, 307, 308].includes(status) && response.headers.location) {
            response.destroy();
            if (redirects >= 5) reject(new Error('Too many redirects'));
            else {
              try {
                resolve(sample(new URL(response.headers.location, target).href, redirects + 1));
              } catch (error) {
                reject(error);
              }
            }
            return;
          }
          if (status !== 206 && status !== 200) {
            response.destroy();
            reject(new Error(`Download server answered HTTP ${status}`));
            return;
          }
          response.on('data', (chunk: Buffer) => {
            bytes += Math.min(chunk.length, SETUP_CHECK_BYTES - bytes);
            if (bytes >= SETUP_CHECK_BYTES) {
              resolve();
              response.destroy();
              req.destroy();
            }
          });
          response.on('end', () => {
            if (bytes < SETUP_CHECK_BYTES)
              reject(new Error('Download server stopped before the sample completed'));
          });
          response.on('aborted', () =>
            reject(new Error('Download connection closed during the sample')),
          );
        },
      );
      req.on('error', reject);
    });
  try {
    await sample(url);
    const elapsedMs = Math.max(1, Math.round(performance.now() - started));
    return {
      status: 'ok',
      host,
      bytes,
      elapsedMs,
      bytesPerSecond: Math.round((bytes * 1000) / elapsedMs),
      checkedAt: Date.now(),
    };
  } catch (error) {
    return {
      status: 'failed',
      host,
      bytes,
      elapsedMs: Math.round(performance.now() - started),
      error: scrubSetupText(
        signal.aborted ? 'Connection check timed out or was cancelled' : String(error),
      ),
      checkedAt: Date.now(),
    };
  } finally {
    for (const response of responses) response.destroy();
    agent.destroy();
  }
}
