import { createServer, request as httpRequest, type IncomingHttpHeaders } from 'node:http';
import { request as httpsRequest } from 'node:https';

const STRIP_HEADERS = new Set([
  'authorization',
  'connection',
  'cookie',
  'forwarded',
  'host',
  'origin',
  'proxy-authorization',
  'referer',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
]);

export function proxyRequestHeaders(
  incoming: IncomingHttpHeaders,
  trusted: Record<string, string>,
): IncomingHttpHeaders {
  const headers: IncomingHttpHeaders = {};
  for (const [name, value] of Object.entries(incoming)) {
    if (!STRIP_HEADERS.has(name.toLowerCase()) && value !== undefined) headers[name] = value;
  }
  for (const [name, value] of Object.entries(trusted)) headers[name] = value;
  return headers;
}

export interface DevBackendProxy {
  url: string;
  close: () => Promise<void>;
}

/** Dev-only same-origin bridge whose target and scoped credentials remain main-owned. */
export async function startDevBackendProxy(
  getBaseUrl: () => string,
  getTrustedHeaders: () => Record<string, string>,
  port = 3903,
): Promise<DevBackendProxy> {
  const server = createServer((incoming, response) => {
    let base: URL;
    let target: URL;
    try {
      base = new URL(`${getBaseUrl().replace(/\/+$/, '')}/`);
      // Resolve relative to the base: a leading slash would drop a reverse-proxy
      // path prefix, and `//host` would leave the configured backend entirely.
      target = new URL((incoming.url || '/').replace(/^\/+/, ''), base);
      if (target.origin !== base.origin) throw new Error('cross-origin target');
    } catch {
      response.writeHead(502, { 'content-type': 'application/json' });
      response.end('{"detail":"Invalid backend target"}');
      return;
    }
    const send = base.protocol === 'https:' ? httpsRequest : httpRequest;
    // Host and port come only from the main-owned base; the renderer picks the path.
    const upstream = send(
      {
        protocol: base.protocol,
        hostname: base.hostname.replace(/^\[|\]$/g, ''),
        port: base.port,
        path: `${target.pathname}${target.search}`,
        method: incoming.method,
        headers: proxyRequestHeaders(incoming.headers, getTrustedHeaders()),
      },
      (result) => {
        response.writeHead(result.statusCode || 502, result.headers);
        result.pipe(response);
      },
    );
    upstream.on('error', () => {
      if (response.headersSent) response.destroy();
      else {
        response.writeHead(502, { 'content-type': 'application/json' });
        response.end('{"detail":"Backend unavailable"}');
      }
    });
    incoming.on('aborted', () => upstream.destroy());
    response.on('close', () => {
      if (!response.writableEnded) upstream.destroy();
    });
    incoming.pipe(upstream);
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      server.off('error', reject);
      resolve();
    });
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Dev backend proxy did not bind');
  let closing: Promise<void> | null = null;
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => {
      if (closing) return closing;
      closing = new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
        // A renderer can hold a streaming response or keep-alive socket while the app quits.
        // Stop accepting first, then retire those connections so native shutdown cannot hang.
        server.closeAllConnections();
      });
      return closing;
    },
  };
}
