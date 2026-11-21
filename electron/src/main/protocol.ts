import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { Readable } from 'node:stream';
import { net, protocol } from 'electron';

export const APP_SCHEME = 'app';
export const APP_HOST = 'voicestudio';
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;
const API_PREFIX = '/api';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.flac': 'audio/flac',
  '.ogg': 'audio/ogg',
  '.webm': 'video/webm',
  '.mp4': 'video/mp4',
  '.txt': 'text/plain; charset=utf-8',
  '.wasm': 'application/wasm',
};

/**
 * Request headers not forwarded to the backend. `origin`/`host`/`referer`
 * would mislead it (and trip CORS); the rest are Fetch's forbidden request
 * headers — Chromium rejects a net.fetch that sets any of them
 * (`sec-fetch-mode` alone yields net::ERR_INVALID_ARGUMENT) and supplies its
 * own hop-by-hop values. Everything else (content-type, range, accept*, x-*)
 * passes through untouched.
 */
const DROPPED_REQUEST_HEADERS = new Set([
  'origin',
  'x-voicestudio-desktop-origin',
  'host',
  'referer',
  'accept-charset',
  'accept-encoding',
  'connection',
  'content-length',
  'cookie',
  'cookie2',
  'date',
  'dnt',
  'expect',
  'keep-alive',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'via',
]);
const DROPPED_REQUEST_HEADER_PREFIXES = ['sec-', 'proxy-', 'access-control-request-'];

function isDroppedRequestHeader(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    DROPPED_REQUEST_HEADERS.has(lower) ||
    DROPPED_REQUEST_HEADER_PREFIXES.some((prefix) => lower.startsWith(prefix))
  );
}

/** Must run before `app.whenReady()` — Chromium freezes the scheme registry at startup. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: APP_SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: true,
        stream: true,
        bypassCSP: false,
      },
    },
  ]);
}

export function installAppProtocol(
  getBackendBase: () => string,
  rendererDir: string,
  getBackendHeaders: () => Record<string, string> = () => ({}),
): void {
  const root = resolve(rendererDir);
  protocol.handle(APP_SCHEME, async (request) => {
    const url = new URL(request.url);
    if (url.hostname !== APP_HOST) return new Response('Not found', { status: 404 });
    if (url.pathname === API_PREFIX || url.pathname.startsWith(`${API_PREFIX}/`)) {
      return proxyToBackend(request, getBackendBase(), url, getBackendHeaders());
    }
    return serveStatic(root, url.pathname);
  });
}

// Large file uploads: dubbing videos and voice-changer video clips.
const STREAMED_UPLOAD_PATHS = new Set(['/dub/upload', '/convert']);

async function proxyToBackend(
  request: Request,
  backendBase: string,
  url: URL,
  injectedHeaders: Record<string, string>,
): Promise<Response> {
  const rest = url.pathname.slice(API_PREFIX.length) || '/';
  const target = `${backendBase}${rest}${url.search}`;
  const headers = new Headers();
  request.headers.forEach((value, name) => {
    if (!isDroppedRequestHeader(name)) headers.append(name, value);
  });
  // The renderer can use the scoped session selected by main, but can never
  // replace it with a master credential of its own.
  headers.delete('authorization');
  for (const [name, value] of Object.entries(injectedHeaders)) headers.set(name, value);
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  try {
    const options = {
      method: request.method,
      headers,
      body: hasBody ? request.body : null,
      duplex: 'half' as const,
      signal: request.signal,
    };
    if (request.method === 'POST' && STREAMED_UPLOAD_PATHS.has(rest) && options.body) {
      const destination = new URL(target);
      if (destination.protocol === 'http:' && LOOPBACK_HOSTS.has(destination.hostname)) {
        // Electron net.fetch buffers the whole request before sending it.
        // Node fetch streams the multipart body with backpressure instead.
        // Keep routing and credentials in main, and never replay an upload
        // at a redirect destination.
        headers.set('accept-encoding', 'identity');
        return await fetch(destination, { ...options, redirect: 'error' });
      }
    }
    // The backend Response is returned untouched: status, headers and the
    // streamed body all pass through (no re-encoding, so content-length stays valid).
    return await net.fetch(target, {
      ...options,
      bypassCustomProtocolHandlers: true,
    });
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return new Response(JSON.stringify({ detail: `Backend unreachable: ${detail}` }), {
      status: 503,
      headers: { 'content-type': 'application/json; charset=utf-8' },
    });
  }
}

async function serveStatic(root: string, pathname: string): Promise<Response> {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return new Response('Bad request', { status: 400 });
  }
  if (decoded.includes('\0')) return new Response('Bad request', { status: 400 });
  const relative = normalize(decoded).replace(/^[/\\]+/, '');
  const candidate = resolve(root, relative || 'index.html');
  if (candidate !== root && !candidate.startsWith(root + sep)) {
    return new Response('Forbidden', { status: 403 });
  }
  const file = await fileResponse(candidate, pathname);
  if (file) return file;
  // SPA routes have no extension; asset misses must 404 so a broken build is visible.
  if (extname(candidate) === '') {
    const index = await fileResponse(join(root, 'index.html'), '/index.html');
    if (index) return index;
  }
  return new Response('Not found', { status: 404 });
}

const SMALL_STATIC_FILE_BYTES = 256 * 1024;
const IMMUTABLE_CACHE_MAX_FILE_BYTES = 512 * 1024;
const IMMUTABLE_CACHE_MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const IMMUTABLE_CACHE_MAX_ENTRIES = 64;

interface CachedAsset {
  bytes: Buffer;
  mime: string;
  cacheControl: string;
}

const immutableAssetCache = new Map<string, CachedAsset>();
let immutableCacheBytes = 0;

function rememberImmutableAsset(key: string, entry: CachedAsset): void {
  if (entry.bytes.byteLength > IMMUTABLE_CACHE_MAX_FILE_BYTES) return;
  const existing = immutableAssetCache.get(key);
  if (existing) {
    immutableCacheBytes -= existing.bytes.byteLength;
    immutableAssetCache.delete(key);
  }
  while (
    immutableAssetCache.size >= IMMUTABLE_CACHE_MAX_ENTRIES ||
    immutableCacheBytes + entry.bytes.byteLength > IMMUTABLE_CACHE_MAX_TOTAL_BYTES
  ) {
    const oldest = immutableAssetCache.keys().next().value;
    if (!oldest) break;
    const evicted = immutableAssetCache.get(oldest)!;
    immutableCacheBytes -= evicted.bytes.byteLength;
    immutableAssetCache.delete(oldest);
  }
  immutableAssetCache.set(key, entry);
  immutableCacheBytes += entry.bytes.byteLength;
}

function staticCacheControl(pathname: string, mime: string): string {
  if (mime.startsWith('text/html')) return 'no-cache';
  // Vite emits content-hashed bundles under /assets/; immutable caching lets
  // Chromium skip app:// protocol round-trips on subsequent launches.
  if (pathname.startsWith('/assets/')) return 'public, max-age=31536000, immutable';
  return 'public, max-age=3600';
}

async function fileResponse(path: string, pathname = ''): Promise<Response | null> {
  const isImmutable = pathname.startsWith('/assets/');
  if (isImmutable) {
    const cached = immutableAssetCache.get(path);
    if (cached) {
      return new Response(new Uint8Array(cached.bytes.buffer, cached.bytes.byteOffset, cached.bytes.byteLength), {
        status: 200,
        headers: {
          'content-type': cached.mime,
          'content-length': String(cached.bytes.byteLength),
          'cache-control': cached.cacheControl,
        },
      });
    }
  }
  let size: number;
  try {
    const info = await stat(path);
    if (!info.isFile()) return null;
    size = info.size;
  } catch {
    return null;
  }
  const mime = MIME[extname(path).toLowerCase()] ?? 'application/octet-stream';
  const cacheControl = staticCacheControl(pathname, mime);
  const headers = {
    'content-type': mime,
    'content-length': String(size),
    'cache-control': cacheControl,
  };
  if (size <= SMALL_STATIC_FILE_BYTES || (isImmutable && size <= IMMUTABLE_CACHE_MAX_FILE_BYTES)) {
    try {
      const data = await readFile(path);
      if (isImmutable) rememberImmutableAsset(path, { bytes: data, mime, cacheControl });
      return new Response(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), {
        status: 200,
        headers,
      });
    } catch {
      return null;
    }
  }
  const body = Readable.toWeb(createReadStream(path)) as ReadableStream<Uint8Array>;
  return new Response(body, { status: 200, headers });
}
