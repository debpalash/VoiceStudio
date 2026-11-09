import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { AccessToken } from './account-session';

/**
 * Backend-only capability (guide §2 "Giving tokens to the Python backend").
 * The refresh token never leaves main; the backend gets a short-lived access
 * token only while sync is on and the backend is the local one.
 */
export async function startAccountTokenBridge(deps: {
  getToken: () => Promise<AccessToken | null>;
  isSyncAllowed: () => boolean;
  isRemoteBackend: () => boolean;
  /** Where the backend syncs: the personal Organization and the sync API base. */
  context: () => Promise<{ organization_id: string | null; sync_api: string }>;
  /** The backend saw a 401: drop the cached access token so the next one is fresh. */
  invalidate: () => void;
}): Promise<{ url: string; token: string; close(): void }> {
  const token = randomBytes(32).toString('hex');
  const expected = Buffer.from('Bearer ' + token);
  const server = createServer(async (req, res) => {
    const send = (status: number, data: unknown) => {
      res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
      res.end(JSON.stringify(data));
    };
    const supplied = Buffer.from(req.headers.authorization || '');
    if (
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected) ||
      req.headers.origin !== undefined
    )
      return send(403, { error: 'Forbidden' });
    const fresh = req.url === '/token?fresh=1';
    if (req.method !== 'GET' || (req.url !== '/token' && !fresh))
      return send(404, { error: 'Not found' });
    if (!deps.isSyncAllowed() || deps.isRemoteBackend()) return send(200, { signed_in: false });
    try {
      if (fresh) deps.invalidate();
      const access = await deps.getToken();
      if (!access) return send(200, { signed_in: false });
      return send(200, { signed_in: true, ...access, ...(await deps.context()) });
    } catch {
      return send(503, { signed_in: false, retry: true });
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  return { url: `http://127.0.0.1:${port}`, token, close: () => server.close() };
}
