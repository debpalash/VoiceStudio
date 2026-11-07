import { describe, expect, it, vi } from 'vitest';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AccountApi } from './account-api';
import { AvatarCache } from './avatar-cache';
import { CloudActivity, type FetchImpl } from './cloud-activity';
import { cloudOrigins, resolveCloudEnvironment } from './cloud-config';

const env = resolveCloudEnvironment({ packaged: true });
const account = {
  user_id: 'Us3nM8qWe2RtY6uI',
  email: 'ana@example.com',
  email_verified: true,
  display_name: 'Ana',
  avatar_url: null,
  personal_organization_id: 'Or5tG7hJ9kL1zX3c',
  organizations: [
    { id: 'Or5tG7hJ9kL1zX3c', slug: 'ana', display_name: 'Ana', role: 'owner', kind: 'personal' },
  ],
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
function setup(responses: Response[]) {
  const fetchImpl = vi.fn<FetchImpl>(async () => responses.shift()!);
  const session = {
    identityGeneration: vi.fn(() => 7),
    bindIdentity: vi.fn(() => true),
    getAccessToken: vi.fn(async () => ({ access_token: 'tok', expires_at: 9e15 })),
    invalidateAccessToken: vi.fn(),
    endSession: vi.fn(),
  };
  const activity = new CloudActivity({
    isAllowed: () => true,
    origins: cloudOrigins(env),
    fetchImpl,
  });
  return { api: new AccountApi(env, session as never, activity), fetchImpl, session };
}

it('maps the account to the renderer-safe profile', async () => {
  const { api, fetchImpl, session } = setup([json(account)]);
  expect(await api.get()).toEqual({
    email: 'ana@example.com',
    displayName: 'Ana',
    avatarUrl: null,
    avatarArtifactId: null,
    personalOrganizationId: 'Or5tG7hJ9kL1zX3c',
    summary: null,
    organizations: [
      { id: 'Or5tG7hJ9kL1zX3c', slug: 'ana', displayName: 'Ana', role: 'owner', kind: 'personal' },
    ],
  });
  expect(fetchImpl.mock.calls[0][0]).toBe('https://appapi.voicestudio.sh/v1/account');
  expect((fetchImpl.mock.calls[0][1] as RequestInit).headers).toMatchObject({
    authorization: 'Bearer tok',
  });
  expect(session.bindIdentity).toHaveBeenCalledWith(account.user_id, 7);
});

it('does not return a profile whose session changed during the authenticated request', async () => {
  const { api, session } = setup([json(account)]);
  session.identityGeneration.mockReturnValueOnce(7).mockReturnValue(8);
  expect(await api.get()).toBeNull();
  expect(session.bindIdentity).not.toHaveBeenCalled();
});

it('ends a session when an authenticated account response conflicts with its bound identity', async () => {
  const { api, session } = setup([json(account)]);
  session.bindIdentity.mockReturnValueOnce(false);
  expect(await api.get()).toBeNull();
  expect(session.endSession).toHaveBeenCalledWith('signed_out_expired');
});

it('refreshes once on 401, then signs out on a second 401', async () => {
  const { api, session } = setup([json({}, 401), json({}, 401)]);
  expect(await api.get()).toBeNull();
  expect(session.invalidateAccessToken).toHaveBeenCalledOnce();
  expect(session.endSession).toHaveBeenCalledWith('signed_out_expired');
});

it('PATCH reuses its Idempotency-Key when retrying after 401', async () => {
  const { api, fetchImpl } = setup([json({}, 401), json(account)]);
  await api.patch({ display_name: 'Ana B' });
  const keys = fetchImpl.mock.calls.map(
    (c) => ((c[1] as RequestInit).headers as Record<string, string>)['idempotency-key'],
  );
  expect(keys[0]).toMatch(/^[0-9a-f]{32}$/);
  expect(keys[1]).toBe(keys[0]);
});

it('returns null without a request when signed out', async () => {
  const { api, fetchImpl, session } = setup([]);
  session.getAccessToken.mockResolvedValueOnce(null as never);
  expect(await api.get()).toBeNull();
  expect(fetchImpl).not.toHaveBeenCalled();
});

describe('profile photo', () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
  const asData = `data:image/png;base64,${Buffer.from(png).toString('base64')}`;
  const cachePath = () => join(mkdtempSync(join(tmpdir(), 'vs-avatar-')), 'account-avatar.json');
  function photoApi(response: () => Response, path = cachePath()) {
    const fetchImpl = vi.fn<FetchImpl>(async () => response());
    const session = {
      getAccessToken: vi.fn(),
      invalidateAccessToken: vi.fn(),
      endSession: vi.fn(),
    };
    const activity = new CloudActivity({
      isAllowed: () => true,
      origins: cloudOrigins(env),
      fetchImpl,
    });
    const cache = new AvatarCache(path);
    return { api: new AccountApi(env, session as never, activity, cache), fetchImpl, cache, path };
  }
  const image = () => new Response(png, { headers: { 'content-type': 'image/png' } });
  const URL_A = 'https://app-s3.voicestudio.sh/avatars/a?X-Amz-Signature=one';

  it('downloads the photo once per avatar_artifact_id, even across restarts', async () => {
    const first = photoApi(image);
    expect(await first.api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: URL_A })).toBe(asData);
    expect(await first.api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: URL_A + 'x' })).toBe(
      asData,
    );
    const restarted = photoApi(image, first.path);
    expect(await restarted.api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: URL_A })).toBe(asData);
    expect(first.fetchImpl).toHaveBeenCalledTimes(1);
    expect(restarted.fetchImpl).not.toHaveBeenCalled();
  });

  it('a new avatar_artifact_id downloads again; a cleared one drops the photo', async () => {
    const { api, fetchImpl, path } = photoApi(image);
    await api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: URL_A });
    await api.avatar({ avatarArtifactId: 'Ar2', avatarUrl: URL_A });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(await api.avatar({ avatarArtifactId: null, avatarUrl: null })).toBeNull();
    expect(existsSync(path)).toBe(false);
  });

  it('never stores the download link, only the picture', async () => {
    const { api, path } = photoApi(image);
    await api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: URL_A });
    const stored = readFileSync(path, 'utf8');
    expect(stored).not.toContain('X-Amz-Signature');
    expect(stored).not.toContain('app-s3');
  });

  it('refuses anything that is not a small raster image over https', async () => {
    const photo = (response: () => Response, url = 'https://x.example/a') =>
      photoApi(response).api.avatar({ avatarArtifactId: 'Ar1', avatarUrl: url });
    expect(
      await photo(() => new Response('<svg/>', { headers: { 'content-type': 'image/svg+xml' } })),
    ).toBeNull();
    expect(
      await photo(() => new Response('hi', { headers: { 'content-type': 'text/html' } })),
    ).toBeNull();
    const big = new Uint8Array(2 * 1024 * 1024 + 1);
    expect(
      await photo(() => new Response(big, { headers: { 'content-type': 'image/jpeg' } })),
    ).toBeNull();
    expect(await photo(image, 'http://x.example/a.png')).toBeNull();
  });
});

it('reads the plan, storage and what the plan includes for an Organization', async () => {
  const entitlements = {
    organization_id: 'Or5tG7hJ9kL1zX3c',
    plan: { code: 'pro', name: 'Pro', source: 'subscription' },
    values: [
      { key: 'storage.bytes', type: 'bytes', value: 1073741824, sources: [] },
      { key: 'sync.settings', type: 'flag', value: 1, sources: [] },
      { key: 'pro.desktop', type: 'flag', value: 0, sources: [] },
    ],
    storage: { limit_bytes: 1073741824, used_bytes: 734003, reserved_bytes: 0 },
    grants: [],
    products: [],
    can_manage: true,
    resolved_at: '2026-10-16T09:00:00Z',
  };
  const { api, fetchImpl } = setup([json(entitlements)]);
  expect(await api.entitlements('Or5tG7hJ9kL1zX3c')).toEqual({
    planCode: 'pro',
    planName: 'Pro',
    storageUsedBytes: 734003,
    storageLimitBytes: 1073741824,
    settingsSync: true,
    proDesktop: false,
  });
  expect(fetchImpl.mock.calls[0][0]).toBe(
    'https://appapi.voicestudio.sh/v1/account/organizations/Or5tG7hJ9kL1zX3c/entitlements',
  );
});

it('refuses an Organization ID that could change the request path', async () => {
  const { api, fetchImpl } = setup([]);
  expect(await api.entitlements('../admin')).toBeNull();
  expect(fetchImpl).not.toHaveBeenCalled();
});

it('reads the plan and storage summary that /v1/account now carries', async () => {
  const { api } = setup([
    json({
      ...account,
      plan: { code: 'pro', name: 'Pro', origin: 'purchase', ends_at: '2027-10-16T00:00:00Z' },
      storage: { limit_bytes: 1073741824, used_bytes: 734003, reserved_bytes: 0 },
    }),
  ]);
  expect((await api.get())?.summary).toEqual({
    planCode: 'pro',
    planName: 'Pro',
    planOrigin: 'purchase',
    planEndsAt: '2027-10-16T00:00:00Z',
    storageUsedBytes: 734003,
    storageLimitBytes: 1073741824,
  });
});

it('without a display name or verified email, the personal workspace name is shown', async () => {
  const { api } = setup([json({ ...account, display_name: undefined, email: undefined })]);
  const profile = await api.get();
  expect(profile?.displayName).toBe('Ana');
  expect(profile?.summary).toBeNull();
});

it('logs a failed account call by status and path only', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const { api } = setup([json({ error: { code: 'forbidden' } }, 403)]);
  await expect(api.get()).rejects.toThrow();
  expect(warn).toHaveBeenCalledWith('[account] GET /v1/account -> 403');
  expect(JSON.stringify(warn.mock.calls)).not.toMatch(/Bearer|tok/);
  warn.mockRestore();
});

it.each([404, 501])(
  'backs off unavailable optional entitlements (%s), then retries without inventing rights',
  async (status) => {
    let now = 1_000;
    const clock = vi.spyOn(Date, 'now').mockImplementation(() => now);
    try {
      const { api, fetchImpl } = setup([json({}, status), json({ values: [] })]);
      const organization = 'Or5tG7hJ9kL1zX3c';
      expect(await api.entitlements(organization)).toBeNull();
      expect(await api.entitlements(organization)).toBeNull();
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      now += 60_001;
      expect(await api.entitlements(organization)).toMatchObject({
        settingsSync: false,
        proDesktop: false,
      });
      expect(fetchImpl).toHaveBeenCalledTimes(2);
    } finally {
      clock.mockRestore();
    }
  },
);

it('coalesces concurrent optional entitlement requests', async () => {
  const { api, fetchImpl } = setup([json({ values: [] })]);
  const results = await Promise.all([
    api.entitlements('Or5tG7hJ9kL1zX3c'),
    api.entitlements('Or5tG7hJ9kL1zX3c'),
  ]);
  expect(results[0]).toEqual(results[1]);
  expect(fetchImpl).toHaveBeenCalledTimes(1);
});

it('does not carry an unavailable entitlement lookup across organizations or accounts', async () => {
  const { api, fetchImpl, session } = setup([json({}, 404), json({}, 404), json({ values: [] })]);
  expect(await api.entitlements('Or5tG7hJ9kL1zX3c')).toBeNull();
  expect(await api.entitlements('Or9tG7hJ9kL1zX3c')).toBeNull();
  session.identityGeneration.mockReturnValue(8);
  expect(await api.entitlements('Or9tG7hJ9kL1zX3c')).not.toBeNull();
  expect(fetchImpl).toHaveBeenCalledTimes(3);
});

it.each([403, 500])('does not cache real entitlement failures (%s)', async (status) => {
  const { api, fetchImpl } = setup([json({}, status), json({ values: [] })]);
  await expect(api.entitlements('Or5tG7hJ9kL1zX3c')).rejects.toMatchObject({ status });
  expect(await api.entitlements('Or5tG7hJ9kL1zX3c')).not.toBeNull();
  expect(fetchImpl).toHaveBeenCalledTimes(2);
});
