import { expect, it, vi } from 'vitest';
import { startAccountTokenBridge } from './account-token-bridge';

const token = { access_token: 'acc', expires_at: 123 };
async function bridge(over: Partial<Parameters<typeof startAccountTokenBridge>[0]> = {}) {
  return startAccountTokenBridge({
    getToken: vi.fn(async () => token),
    isSyncAllowed: () => true,
    isRemoteBackend: () => false,
    context: async () => ({
      organization_id: 'Or5tG7hJ9kL1zX3c',
      sync_api: 'https://appapi.voicestudio.sh/v1/sync',
    }),
    invalidate: vi.fn(),
    ...over,
  });
}
const get = (b: { url: string; token: string }, headers: Record<string, string> = {}) =>
  fetch(`${b.url}/token`, { headers: { authorization: `Bearer ${b.token}`, ...headers } });

it('hands the backend an access token when sync is on and local', async () => {
  const b = await bridge();
  expect(b.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  expect(await (await get(b)).json()).toEqual({
    signed_in: true,
    access_token: 'acc',
    expires_at: 123,
    organization_id: 'Or5tG7hJ9kL1zX3c',
    sync_api: 'https://appapi.voicestudio.sh/v1/sync',
  });
  b.close();
});

it('answers signed_in:false when sync is off, without asking the session', async () => {
  const getToken = vi.fn();
  const b = await bridge({ isSyncAllowed: () => false, getToken });
  expect(await (await get(b)).json()).toEqual({ signed_in: false });
  expect(getToken).not.toHaveBeenCalled();
  b.close();
});

it('remote mode never sends a token', async () => {
  const getToken = vi.fn();
  const b = await bridge({ isRemoteBackend: () => true, getToken });
  expect(await (await get(b)).json()).toEqual({ signed_in: false });
  expect(getToken).not.toHaveBeenCalled();
  b.close();
});

it('refuses a wrong bearer and any browser Origin', async () => {
  const b = await bridge();
  expect(
    (await fetch(`${b.url}/token`, { headers: { authorization: 'Bearer nope' } })).status,
  ).toBe(403);
  expect((await get(b, { origin: 'https://evil.example' })).status).toBe(403);
  b.close();
});

it('a transient refresh failure reads as temporarily signed out, not an error page', async () => {
  const b = await bridge({
    getToken: vi.fn(async () => {
      throw new Error('offline');
    }),
  });
  const res = await get(b);
  expect(res.status).toBe(503);
  expect(await res.json()).toEqual({ signed_in: false, retry: true });
  b.close();
});

it('fresh=1 drops the rejected token before handing out a new one', async () => {
  const order: string[] = [];
  const b = await bridge({
    invalidate: () => order.push('invalidate'),
    getToken: vi.fn(async () => (order.push('get'), token)),
  });
  await fetch(`${b.url}/token?fresh=1`, { headers: { authorization: `Bearer ${b.token}` } });
  expect(order).toEqual(['invalidate', 'get']);
  expect(
    (await fetch(`${b.url}/other`, { headers: { authorization: `Bearer ${b.token}` } })).status,
  ).toBe(404);
  b.close();
});

it('refuses any Origin header, even an empty one, so no web page can reach the token', async () => {
  const { request } = await import('node:http');
  const getToken = vi.fn(async () => token);
  const b = await bridge({ getToken });
  const status = (origin: string) =>
    new Promise<number>((resolve, reject) => {
      const req = request(`${b.url}/token`, {
        headers: { authorization: `Bearer ${b.token}`, origin },
      });
      req.on('response', (res) => {
        res.resume();
        resolve(res.statusCode ?? 0);
      });
      req.on('error', reject);
      req.end();
    });
  try {
    expect(await status('')).toBe(403);
    expect(await status('https://example.com')).toBe(403);
    expect(getToken).not.toHaveBeenCalled();
  } finally {
    b.close();
  }
});
