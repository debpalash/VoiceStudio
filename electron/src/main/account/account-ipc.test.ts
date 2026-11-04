import { expect, it, vi } from 'vitest';

// account-ipc imports ../ipc, whose module graph reads Electron state at load.
vi.mock('electron', () => ({
  ipcMain: { handle: vi.fn(), removeHandler: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
}));
vi.mock('../ipc', () => ({ assertTrustedMainFrame: vi.fn() }));
import { AccountController } from './account-ipc';

const TOKEN = 'eyJ-super-secret-token';
function ctl(over: Partial<ConstructorParameters<typeof AccountController>[0]> = {}) {
  const switches = { accountEnabled: true, syncEnabled: false, prompted: true };
  return new AccountController({
    available: true,
    switches: {
      get: () => ({ ...switches }),
      set: vi.fn((p) => Object.assign(switches, p)),
    } as never,
    session: {
      state: () => ({ signedIn: true, signingIn: false, notice: null }),
      getAccessToken: async () => ({ access_token: TOKEN, expires_at: 1 }),
    } as never,
    api: {
      get: async () => ({
        email: 'ana@example.com',
        displayName: 'Ana',
        avatarUrl: null,
        organizations: [],
      }),
      avatar: async () => null,
      entitlements: async () => null,
    } as never,
    remembered: () => true,
    links: {
      profile: 'https://app.example/app/settings/profile',
      plan: 'https://app.example/app/settings/plan',
    },
    ...over,
  });
}

it('the public state never carries a token', async () => {
  const s = await ctl().publicState();
  expect(JSON.stringify(s)).not.toContain(TOKEN);
  expect(Object.keys(s).sort()).toEqual([
    'accountEnabled',
    'available',
    'links',
    'notice',
    'plan',
    'profile',
    'profileError',
    'prompted',
    'remembered',
    'signedIn',
    'signingIn',
    'syncEnabled',
  ]);
});

it('an unavailable build reports off and makes no account call', async () => {
  const get = vi.fn();
  const s = await ctl({ available: false, api: { get } as never }).publicState();
  expect(s).toMatchObject({ available: false, accountEnabled: false, signedIn: false });
  expect(get).not.toHaveBeenCalled();
});

it('does not fetch the profile while account features are off', async () => {
  const get = vi.fn();
  const c = ctl({ api: { get } as never });
  await c.setSwitches({ accountEnabled: false });
  await c.publicState();
  expect(get).not.toHaveBeenCalled();
});

it('setSwitches validates input and always marks the prompt as answered', async () => {
  const c = ctl();
  await expect(c.setSwitches({ accountEnabled: 'yes' } as never)).rejects.toThrow(/Invalid/);
  const s = await c.setSwitches({ accountEnabled: false });
  expect(s.prompted).toBe(true);
});

it('the renderer gets the photo as a data URL, never the signed download link', async () => {
  const c = ctl({
    api: {
      get: async () => ({
        email: 'ana@example.com',
        displayName: 'Ana',
        avatarUrl: 'https://app-s3.voicestudio.sh/a.png?X-Amz-Signature=secret',
        personalOrganizationId: 'Or1',
        organizations: [],
      }),
      avatar: async () => 'data:image/png;base64,AAAA',
      entitlements: async () => null,
    } as never,
  });
  const s = await c.publicState(true);
  expect(s.profile?.avatarUrl).toBe('data:image/png;base64,AAAA');
  expect(JSON.stringify(s)).not.toContain('X-Amz-Signature');
});

it('signed in, the state carries the plan and storage; signed out it carries none', async () => {
  const plan = {
    planCode: 'free',
    planName: 'Free',
    storageUsedBytes: 10,
    storageLimitBytes: 10485760,
    settingsSync: true,
    proDesktop: false,
  };
  const api = {
    get: async () => ({
      email: 'ana@example.com',
      displayName: 'Ana',
      avatarUrl: null,
      personalOrganizationId: 'Or1',
      organizations: [],
    }),
    avatar: async () => null,
    entitlements: vi.fn(async () => plan),
  };
  expect((await ctl({ api: api as never }).publicState(true)).plan).toEqual({
    ...plan,
    planEndsAt: null,
  });
  expect(api.entitlements).toHaveBeenCalledWith('Or1');
  const signedOut = ctl({
    api: api as never,
    session: {
      state: () => ({ signedIn: false, signingIn: false, notice: null }),
      getAccessToken: async () => null,
    } as never,
  });
  expect((await signedOut.publicState(true)).plan).toBeNull();
});

it('the plan comes from the account summary, with plan features when entitlements answer', async () => {
  const summary = {
    planCode: 'free',
    planName: 'Free',
    planEndsAt: null,
    storageUsedBytes: 5,
    storageLimitBytes: 10,
  };
  const profile = {
    email: 'a@b.c',
    displayName: 'Ana',
    avatarUrl: null,
    avatarArtifactId: null,
    personalOrganizationId: 'Or1',
    organizations: [],
    summary,
  };
  const offline = ctl({
    api: {
      get: async () => profile,
      avatar: async () => null,
      entitlements: async () => {
        throw new Error('503');
      },
    } as never,
  });
  expect((await offline.publicState(true)).plan).toEqual({
    ...summary,
    settingsSync: null,
    proDesktop: null,
  });
  const full = ctl({
    api: {
      get: async () => profile,
      avatar: async () => null,
      entitlements: async () => ({
        planCode: 'free',
        planName: 'Free',
        storageUsedBytes: 6,
        storageLimitBytes: 10,
        settingsSync: true,
        proDesktop: false,
      }),
    } as never,
  });
  expect((await full.publicState(true)).plan).toEqual({
    ...summary,
    settingsSync: true,
    proDesktop: false,
  });
});

it('when the account cannot be loaded, the state says why instead of showing nothing', async () => {
  const failing = ctl({
    api: {
      get: async () => {
        throw Object.assign(new Error('Account request failed (403)'), { status: 403 });
      },
      avatar: async () => null,
      entitlements: async () => null,
    } as never,
  });
  const s = await failing.publicState(true);
  expect(s.profile).toBeNull();
  expect(s.profileError).toBe('403');
  const ok = await ctl().publicState(true);
  expect(ok.profileError).toBeNull();
});

it('hands the renderer the dashboard profile and plan links only while signed in', async () => {
  expect((await ctl().publicState()).links).toEqual({
    profile: 'https://app.example/app/settings/profile',
    plan: 'https://app.example/app/settings/plan',
  });
  const signedOut = ctl({
    session: { state: () => ({ signedIn: false, signingIn: false, notice: null }) } as never,
  });
  expect((await signedOut.publicState()).links).toBeNull();
});

it('asks whether sign-in is remembered only with account features on', async () => {
  // The answer comes from safeStorage, which can raise a Keychain prompt on macOS.
  const remembered = vi.fn(() => false);
  const c = ctl({ remembered });
  await c.setSwitches({ accountEnabled: false });
  expect((await c.publicState()).remembered).toBe(true);
  expect(remembered).not.toHaveBeenCalled();
  await c.setSwitches({ accountEnabled: true });
  expect((await c.publicState()).remembered).toBe(false);
  expect(remembered).toHaveBeenCalled();
});
