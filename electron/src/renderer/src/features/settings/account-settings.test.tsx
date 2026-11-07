import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>) =>
      vars ? `${key} ${JSON.stringify(vars)}` : key,
  }),
}));

vi.mock('./cloud-sync-panel', () => ({ CloudSyncPanel: () => <div>sync-panel</div> }));

import { AccountSettings } from './account-settings';

const base = {
  available: true,
  accountEnabled: true,
  syncEnabled: false,
  prompted: true,
  remembered: true,
  signedIn: false,
  signingIn: false,
  notice: null as string | null,
  profile: null as unknown,
};
function setup(state: Partial<typeof base>) {
  let push: (s: unknown) => void = () => {};
  const account = {
    get: vi.fn(async () => ({ ...base, ...state })),
    refresh: vi.fn(async () => ({ ...base, ...state })),
    onState: vi.fn((cb: (s: unknown) => void) => ((push = cb), () => {})),
    setSwitches: vi.fn(async (p: object) => ({ ...base, ...state, ...p })),
    signIn: vi.fn(async () => {}),
    cancelSignIn: vi.fn(async () => {}),
    signOut: vi.fn(async () => {}),
  };
  mock.bridge = { account };
  render(<AccountSettings />);
  return { account, push: (s: Partial<typeof base>) => act(() => push({ ...base, ...s })) };
}
afterEach(() => {
  cleanup();
  mock.bridge = null;
});

it('turning account features off applies at once, with no confirm step', async () => {
  const { account } = setup({});
  fireEvent.click(await screen.findByRole('switch', { name: 'account.features' }));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: false });
});

it('the sync switch is disabled until signed in', async () => {
  setup({});
  expect(await screen.findByRole('switch', { name: 'account.sync' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
});

it('signing in shows the browser hint with a working Cancel', async () => {
  const { account, push } = setup({});
  fireEvent.click(await screen.findByText('account.sign_in'));
  expect(account.signIn).toHaveBeenCalled();
  push({ signingIn: true });
  expect(screen.getByText('account.continue_in_browser')).toBeInTheDocument();
  fireEvent.click(screen.getByText('account.cancel'));
  expect(account.cancelSignIn).toHaveBeenCalled();
});

it.each(['signed_out_expired', 'sign_in_failed', 'offline'])(
  'shows the %s notice',
  async (notice) => {
    setup({ notice });
    expect(await screen.findByText(`account.${notice}`)).toBeInTheDocument();
  },
);

it('explains a session that will not survive a restart', async () => {
  setup({ remembered: false });
  expect(await screen.findByText('account.not_remembered')).toBeInTheDocument();
});

it('signed in: shows the name and Sign out, and never any token-like text', async () => {
  const { account } = setup({
    signedIn: true,
    profile: { email: 'ana@example.com', displayName: 'Ana', avatarUrl: null, organizations: [] },
  });
  fireEvent.click(await screen.findByText('account.sign_out'));
  expect(account.signOut).toHaveBeenCalled();
  expect(document.body.textContent).not.toMatch(/Bearer|eyJ/);
});

it('renders nothing when the build has accounts unavailable', async () => {
  setup({ available: false });
  await act(async () => {});
  expect(screen.queryByRole('switch')).toBeNull();
});

it('a switch change that fails to save re-reads the real state instead of staying stale', async () => {
  const { account } = setup({});
  account.setSwitches.mockRejectedValueOnce(new Error('ENOSPC'));
  account.get.mockResolvedValueOnce({ ...base, accountEnabled: false });
  fireEvent.click(await screen.findByRole('switch', { name: 'account.features' }));
  await vi.waitFor(() => expect(screen.queryByRole('switch', { name: 'account.sync' })).toBeNull());
  expect(account.get).toHaveBeenCalledTimes(2);
});

it('sync can always be turned off, even while signed out', async () => {
  const { account } = setup({ syncEnabled: true, signedIn: false });
  const sync = await screen.findByRole('switch', { name: 'account.sync' });
  expect(sync).not.toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(sync);
  expect(account.setSwitches).toHaveBeenCalledWith({ syncEnabled: false });
});

it('shows what to sync only when signed in with sync on', async () => {
  const { push } = setup({ signedIn: true, syncEnabled: false });
  await screen.findByRole('switch', { name: 'account.sync' });
  expect(screen.queryByText('sync-panel')).toBeNull();
  push({ signedIn: true, syncEnabled: true });
  expect(await screen.findByText('sync-panel')).toBeInTheDocument();
});

it('signed in, shows the account: name, email, plan, storage and what the plan includes', async () => {
  setup({
    signedIn: true,
    profile: {
      email: 'ana@example.com',
      displayName: 'Ana Lima',
      avatarUrl: null,
      organizations: [],
    },
    plan: {
      planCode: 'free',
      planName: 'Free',
      storageUsedBytes: 2 * 1024 * 1024,
      storageLimitBytes: 10 * 1024 * 1024,
      settingsSync: true,
      proDesktop: false,
    },
  } as never);
  expect(await screen.findByText('Ana Lima')).toBeInTheDocument();
  expect(screen.getByText('ana@example.com')).toBeInTheDocument();
  expect(screen.getByText('Free')).toBeInTheDocument();
  expect(screen.getByText(/account\.storage_used/)).toHaveTextContent('2.0 MB');
  expect(screen.getByText(/account\.storage_used/)).toHaveTextContent('10.0 MB');
  expect(screen.getByText('account.feature_settings_sync').closest('li')).toHaveTextContent(
    'account.included',
  );
  expect(screen.getByText('account.feature_pro').closest('li')).toHaveTextContent(
    'account.not_included',
  );
});

it('without plan details yet, still shows who is signed in', async () => {
  setup({
    signedIn: true,
    profile: { email: 'ana@example.com', displayName: null, avatarUrl: null, organizations: [] },
    plan: null,
  } as never);
  expect(await screen.findByText('ana@example.com')).toBeInTheDocument();
  expect(screen.queryByText('account.plan')).toBeNull();
});

it('shows when the plan ends, and hides what it includes while that is unknown', async () => {
  setup({
    signedIn: true,
    profile: {
      email: 'ana@example.com',
      displayName: 'Ana Lima',
      avatarUrl: null,
      organizations: [],
    },
    plan: {
      planCode: 'pro',
      planName: 'Pro',
      planEndsAt: '2027-10-16T00:00:00Z',
      storageUsedBytes: 0,
      storageLimitBytes: 1073741824,
      settingsSync: null,
      proDesktop: null,
    },
  } as never);
  expect(await screen.findByText(/account\.plan_ends/)).toBeInTheDocument();
  expect(screen.queryByText('account.features_title')).toBeNull();
});

it('says when the account details could not be loaded, with the reason and a retry', async () => {
  const { account } = setup({
    signedIn: true,
    profile: null,
    plan: null,
    profileError: '403',
  } as never);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('account.details_failed');
  expect(alert).toHaveTextContent('403');
  fireEvent.click(screen.getByText('account.retry'));
  await waitFor(() => expect(account.refresh).toHaveBeenCalledTimes(1));
});

it('Refresh reloads the signed-in account and shows the new details', async () => {
  const signedIn = {
    signedIn: true,
    profile: {
      email: 'ana@example.com',
      displayName: 'Ana Lima',
      avatarUrl: null,
      organizations: [],
    },
    plan: null,
  };
  const { account } = setup(signedIn as never);
  let finish!: (s: unknown) => void;
  account.refresh.mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)) as never,
  );
  const button = await screen.findByRole('button', { name: 'account.refresh' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(account.refresh).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  await act(async () =>
    finish({
      ...base,
      ...signedIn,
      profile: { ...signedIn.profile, displayName: 'Ana Souza' },
    }),
  );
  expect(await screen.findByText('Ana Souza')).toBeInTheDocument();
  expect(button).not.toBeDisabled();
});

it('shows no Refresh button when signed out', async () => {
  setup({});
  await screen.findByText('account.sign_in');
  expect(screen.queryByRole('button', { name: 'account.refresh' })).toBeNull();
});
