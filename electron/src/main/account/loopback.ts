import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { timingSafeEqual } from 'node:crypto';

export class LoopbackError extends Error {
  constructor(readonly reason: 'denied' | 'timeout' | 'closed') {
    super(`Sign-in ${reason}`);
    this.name = 'LoopbackError';
  }
}

function sameState(supplied: string, expected: string): boolean {
  // Compare bytes, not UTF-16 lengths: timingSafeEqual throws on a byte-length mismatch.
  const a = Buffer.from(supplied);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** RFC 8252 loopback receiver: one /callback within the timeout, then gone. */
export async function startLoopback(opts: {
  expectedState: string;
  signedInPage: string;
  timeoutMs?: number;
}): Promise<{ redirectUri: string; code: Promise<string>; close(): void }> {
  let settle!: (result: { code?: string; error?: LoopbackError }) => void;
  const outcome = new Promise<{ code?: string; error?: LoopbackError }>((r) => (settle = r));
  let done = false;
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    if (done || req.method !== 'GET' || url.pathname !== '/callback') {
      res.writeHead(404).end();
      return;
    }
    if (!sameState(url.searchParams.get('state') ?? '', opts.expectedState)) {
      res.writeHead(400).end();
      return;
    }
    const code = url.searchParams.get('code');
    const result =
      url.searchParams.has('error') || !code ? { error: new LoopbackError('denied') } : { code };
    // Claim the one callback now; tear the listener down only once the 302 is out.
    done = true;
    res.on('finish', () => finish(result, true));
    const landing = new URL(opts.signedInPage);
    if (result.error) landing.searchParams.set('error', 'access_denied');
    res.writeHead(302, { location: landing.toString(), connection: 'close' }).end();
  });
  const timer = setTimeout(
    () => finish({ error: new LoopbackError('timeout') }),
    opts.timeoutMs ?? 300_000,
  );
  let settled = false;
  function finish(result: { code?: string; error?: LoopbackError }, claimed = false) {
    if (settled || (done && !claimed)) return;
    settled = true;
    done = true;
    clearTimeout(timer);
    server.close();
    server.closeAllConnections?.();
    settle(result);
  }
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  const code = outcome.then((r) => (r.error ? Promise.reject(r.error) : (r.code as string)));
  // The browser can answer before anyone awaits; still rejects for every awaiter.
  code.catch(() => {});
  return {
    redirectUri: `http://127.0.0.1:${port}/callback`,
    code,
    close: () => finish({ error: new LoopbackError('closed') }),
  };
}
