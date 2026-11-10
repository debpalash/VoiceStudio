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

vi.mock('./cloud-sync-panel', () => ({
  CloudSyncPanel: ({ planUrl }: { planUrl?: string | null }) => (
    <div data-plan-url={planUrl ?? ''}>sync-panel</div>
  ),
}));

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
  links: null as { profile: string; plan: string; deletion: string } | null,
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
    updateProfile: vi.fn(async (p: { display_name: string }) => ({
      ...base,
      ...state,
      profile: { ...(state.profile as object), displayName: p.display_name },
    })),
  };
  mock.bridge = { account, files: { openExternal: vi.fn(async () => {}) } };
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
  fireEvent.click(await screen.findByRole('button', { name: 'account.sign_out' }));
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

it('with a profile, the account card is the one place for the identity and Sign out', async () => {
  setup({
    signedIn: true,
    profile: { email: 'ana@example.com', displayName: null, avatarUrl: null, organizations: [] },
  });
  expect(await screen.findByText('ana@example.com')).toBeInTheDocument();
  expect(screen.getAllByText('ana@example.com')).toHaveLength(1);
  expect(screen.getAllByRole('button', { name: 'account.sign_out' })).toHaveLength(1);
  expect(screen.queryByText(/account\.signed_in_as/)).toBeNull();
});

it('signed in before the profile loads, Sign out stays reachable in the session row', async () => {
  const { account } = setup({ signedIn: true, profile: null });
  expect(await screen.findByText(/account\.signed_in_as/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('account.sign_out'));
  expect(account.signOut).toHaveBeenCalled();
});

it('notices render as padded callouts outside the settings panel', async () => {
  setup({ notice: 'session_expired', remembered: false });
  const notice = await screen.findByRole('status');
  expect(notice.closest('section')).toBeNull();
  expect(notice.className).toMatch(/px-4/);
  expect(screen.getByText('account.not_remembered').closest('section')).toBeNull();
});

it('collapses the sync list while Cloud sync is off and passes the plan link when on', async () => {
  const { push } = setup({ signedIn: true, syncEnabled: false });
  expect(await screen.findByRole('switch', { name: 'account.sync' })).not.toBeChecked();
  expect(screen.queryByText('sync-panel')).toBeNull();
  push({
    signedIn: true,
    syncEnabled: true,
    links: { profile: 'p', plan: 'https://plan.example', deletion: 'd' },
  });
  expect(await screen.findByText('sync-panel')).toHaveAttribute(
    'data-plan-url',
    'https://plan.example',
  );
});

const ana = {
  email: 'ana@example.com',
  displayName: 'Ana Lima',
  avatarUrl: null as string | null,
  organizations: [],
};
const links = {
  profile: 'https://cloud.example/profile',
  plan: 'https://cloud.example/plan',
  deletion: 'd',
};
const planOf = (pro: boolean) => ({
  planCode: pro ? 'pro' : 'free',
  planName: pro ? 'Pro' : 'Free',
  storageUsedBytes: 0,
  storageLimitBytes: 1024 ** 3,
  settingsSync: true,
  proDesktop: pro,
});

it.each([
  [false, 'free'],
  [true, 'pro'],
])('shows the plan as a badge (Pro: %s)', async (pro, kind) => {
  setup({ signedIn: true, profile: ana, plan: planOf(pro), links } as never);
  const badge = await screen.findByTestId('account-plan-badge');
  expect(badge).toHaveAttribute('data-plan', kind);
  expect(badge).toHaveTextContent(pro ? 'Pro' : 'Free');
  expect(badge.querySelector('svg')).not.toBeNull();
});

it('shows the photo when there is one, and initials otherwise', async () => {
  setup({ signedIn: true, profile: { ...ana, avatarUrl: 'data:image/png;base64,AAAA' } } as never);
  const photo = (await screen.findByTestId('account-avatar')).querySelector('img');
  expect(photo).toHaveAttribute('src', 'data:image/png;base64,AAAA');
  cleanup();
  setup({ signedIn: true, profile: ana } as never);
  expect(await screen.findByTestId('account-avatar')).toHaveTextContent('AL');
});

it('the avatar opens the profile page to change the photo, so it syncs', async () => {
  setup({ signedIn: true, profile: ana, links } as never);
  fireEvent.click(await screen.findByRole('button', { name: 'account.change_photo' }));
  const files = (mock.bridge as { files: { openExternal: ReturnType<typeof vi.fn> } }).files;
  expect(files.openExternal).toHaveBeenCalledWith('https://cloud.example/profile');
});

it('without a profile link the avatar is not a button', async () => {
  setup({ signedIn: true, profile: ana } as never);
  expect(await screen.findByTestId('account-avatar')).not.toHaveRole('button');
  expect(screen.queryByRole('button', { name: 'account.change_photo' })).toBeNull();
});

it('edits the display name inline and saves it to the account', async () => {
  const { account } = setup({ signedIn: true, profile: ana, links } as never);
  fireEvent.click(await screen.findByRole('button', { name: 'account.edit_name' }));
  const input = screen.getByRole('textbox', { name: 'account.name_label' });
  expect(input).toHaveValue('Ana Lima');
  fireEvent.change(input, { target: { value: '  Ana   Souza ' } });
  fireEvent.submit(input.closest('form')!);
  await waitFor(() =>
    expect(account.updateProfile).toHaveBeenCalledWith({ display_name: 'Ana Souza' }),
  );
  expect(await screen.findByText('Ana Souza')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'account.name_label' })).toBeNull();
});

it('Escape cancels editing, an empty name cannot be saved, and a failure says so', async () => {
  const { account } = setup({ signedIn: true, profile: ana, links } as never);
  fireEvent.click(await screen.findByRole('button', { name: 'account.edit_name' }));
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'account.name_label' }), {
    key: 'Escape',
  });
  expect(screen.queryByRole('textbox', { name: 'account.name_label' })).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'account.edit_name' }));
  const input = screen.getByRole('textbox', { name: 'account.name_label' });
  fireEvent.change(input, { target: { value: '   ' } });
  expect(screen.getByRole('button', { name: 'common.save' })).toBeDisabled();

  account.updateProfile.mockRejectedValueOnce(new Error('422'));
  fireEvent.change(input, { target: { value: 'Ana' } });
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('account.rename_failed');
  expect(screen.getByRole('textbox', { name: 'account.name_label' })).toHaveValue('Ana');
});
