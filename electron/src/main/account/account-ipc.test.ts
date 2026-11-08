import { expect, it, vi } from 'vitest';

// account-ipc imports ../ipc, whose module graph reads Electron state at load.
const ipc = vi.hoisted(() => {
  const handlers = new Map<string, (...args: any[]) => unknown>();
  return {
    handlers,
    handle: vi.fn((channel: string, handler: (...args: any[]) => unknown) =>
      handlers.set(channel, handler),
    ),
    removeHandler: vi.fn(),
    windows: [{}],
    send: vi.fn(),
  };
});
vi.mock('electron', () => ({
  ipcMain: { handle: ipc.handle, removeHandler: ipc.removeHandler },
  BrowserWindow: { getAllWindows: () => ipc.windows },
}));
vi.mock('../ipc', () => ({ assertTrustedMainFrame: vi.fn() }));
vi.mock('../window-safety', () => ({ sendToLiveWindow: ipc.send }));
import { ACCOUNT_CHANNELS, AccountController, registerAccountIpc } from './account-ipc';

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
      accountId: () => 'Account000000001',
      state: () => ({ signedIn: true, signingIn: false, notice: null }),
      signOut: vi.fn(async () => {}),
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
    remembered: true,
    links: {
      profile: 'https://app.example/app/settings/profile',
      plan: 'https://app.example/app/settings/plan',
      deletion: 'https://app.example/app/settings/deletion-review',
    },
    ...over,
  });
}

it('the public state never carries a token', async () => {
  const s = await ctl().publicState();
  expect(JSON.stringify(s)).not.toContain(TOKEN);
  expect(Object.keys(s).sort()).toEqual([
    'accountEnabled',
    'accountId',
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

it('switches off at once without waiting on the network to sign out', async () => {
  const set = vi.fn();
  const signOut = vi.fn(() => new Promise<void>(() => {}));
  const c = ctl({
    switches: { get: () => ({ accountEnabled: true }), set } as never,
    session: { state: () => ({ signedIn: true }), signOut } as never,
  });
  await c.setSwitches({ accountEnabled: false });
  expect(set).toHaveBeenCalledWith({ accountEnabled: false, prompted: true });
  expect(signOut).not.toHaveBeenCalled();
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
    planOrigin: 'default',
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
    planOrigin: 'default',
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
    deletion: 'https://app.example/app/settings/deletion-review',
  });
  const signedOut = ctl({
    session: { state: () => ({ signedIn: false, signingIn: false, notice: null }) } as never,
  });
  expect((await signedOut.publicState()).links).toBeNull();
});

it('refresh IPC reloads account data and broadcasts it to every renderer', async () => {
  ipc.handlers.clear();
  ipc.send.mockClear();
  const get = vi.fn(async () => ({
    email: 'new@example.com',
    displayName: 'New Name',
    avatarUrl: 'https://app-s3.voicestudio.sh/new.png',
    avatarArtifactId: 'Ar2',
    personalOrganizationId: 'Or1',
    organizations: [],
  }));
  const controller = ctl({
    api: {
      get,
      avatar: async () => 'data:image/png;base64,NEW',
      entitlements: async () => null,
    } as never,
  });
  registerAccountIpc(controller, { onChange: () => () => {} } as never, () => null);
  const refresh = ipc.handlers.get(ACCOUNT_CHANNELS.refresh);
  expect(refresh).toBeTypeOf('function');
  const state = await refresh!({});
  expect(get).toHaveBeenCalledOnce();
  expect(state).toMatchObject({
    profile: {
      displayName: 'New Name',
      avatarUrl: 'data:image/png;base64,NEW',
    },
  });
  expect(ipc.send).toHaveBeenCalledWith(ipc.windows[0], ACCOUNT_CHANNELS.state, state);
});

it('drops a profile response started before the session signed out', async () => {
  let signedIn = true;
  let generation = 0;
  let finish!: (value: unknown) => void;
  const controller = ctl({
    session: {
      state: () => ({ signedIn, signingIn: false, notice: null }),
      identityGeneration: () => generation,
      accountId: () => 'Account000000001',
    } as never,
    api: {
      get: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
      avatar: async () => null,
    } as never,
  });
  const pending = controller.publicState();
  signedIn = false;
  generation++;
  finish({ displayName: 'First', organizations: [] });
  const state = await pending;
  expect(state.signedIn).toBe(false);
  expect(state.profile).toBeNull();
});

it('recomputes a delayed profile after switching to a different account', async () => {
  let generation = 0;
  let finish!: (value: unknown) => void;
  const get = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValue({ displayName: 'Second', organizations: [] });
  const controller = ctl({
    session: {
      state: () => ({ signedIn: true, signingIn: false, notice: null }),
      identityGeneration: () => generation,
      accountId: () => 'Account000000001',
    } as never,
    api: { get, avatar: async () => null } as never,
  });
  const pending = controller.publicState();
  generation++;
  finish({ displayName: 'First', organizations: [] });
  const state = await pending;
  expect(state.profile?.displayName).toBe('Second');
  expect(get).toHaveBeenCalledTimes(2);
});

it('never authenticates the renderer from a refresh-only record or an unresolved profile', async () => {
  const c = ctl({
    session: {
      state: () => ({ signedIn: true, signingIn: false, notice: null }),
      accountId: () => null,
    } as never,
  });
  expect(await c.publicState()).toMatchObject({
    signedIn: false,
    accountId: null,
    profile: null,
    plan: null,
  });
});
it('returns protected cached identity offline without waiting for profile or OAuth', async () => {
  const get = vi.fn(async () => {
    throw new Error('offline');
  });
  const c = ctl({ api: { get } as never });
  expect(await c.publicState(false, true)).toMatchObject({
    signedIn: true,
    accountId: 'Account000000001',
    profile: null,
  });
  expect(get).not.toHaveBeenCalled();
});
it('validates signup options before invoking the browser session', async () => {
  const signIn = vi.fn(async () => {});
  registerAccountIpc(ctl(), { signIn, onChange: () => () => {} } as never, () => null);
  const invoke = ipc.handlers.get(ACCOUNT_CHANNELS.signIn)!;
  await invoke({}, { intent: 'signup' });
  expect(signIn).toHaveBeenCalledWith({ intent: 'signup' });
  await invoke({}, undefined);
  expect(signIn).toHaveBeenCalledWith({ intent: 'login' });
  for (const options of [
    null,
    [],
    {},
    { intent: ['signup'] },
    { intent: 'signup', url: 'https://other.example' },
    { intent: 'create' },
  ])
    expect(() => invoke({}, options)).toThrow('Invalid sign-in options');
  expect(signIn).toHaveBeenCalledTimes(2);
});
