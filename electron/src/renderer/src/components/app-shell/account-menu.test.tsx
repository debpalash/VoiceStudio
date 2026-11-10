import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown, navigate: vi.fn() }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mock.navigate }));

import { AccountMenu } from './account-menu';

const signedIn = {
  available: true,
  accountEnabled: true,
  syncEnabled: false,
  prompted: true,
  remembered: true,
  signedIn: true,
  signingIn: false,
  notice: null as string | null,
  plan: null,
  profile: {
    email: 'ana@example.com',
    displayName: 'Ana Lima' as string | null,
    avatarUrl: null as string | null,
    personalOrganizationId: 'Or1',
    organizations: [],
  },
};

function bridgeFor(state: Record<string, unknown>) {
  const account = {
    get: vi.fn(async () => state),
    onState: vi.fn(() => () => {}),
    signIn: vi.fn(async () => {}),
    signOut: vi.fn(async () => {}),
  };
  const files = { openExternal: vi.fn(async () => {}) };
  mock.bridge = { account, files };
  return Object.assign(account, { files });
}
async function show(state: Record<string, unknown>) {
  const account = bridgeFor(state);
  const view = render(<AccountMenu />);
  await act(async () => {});
  return { ...view, account };
}
afterEach(() => {
  cleanup();
  mock.bridge = null;
  mock.navigate.mockReset();
});

it('signed in: an icon-only avatar whose label, tooltip and menu carry the name', async () => {
  await show(signedIn);
  const trigger = screen.getByRole('button', { name: 'Ana Lima' });
  expect(trigger).toHaveTextContent(/^AL$/);
  expect(trigger).toHaveAttribute('title', 'Ana Lima');
  expect(screen.queryByText(/signed_in_as/)).toBeNull();
  fireEvent.click(trigger);
  expect(await screen.findByText('Ana Lima')).toBeInTheDocument();
});

it('shows the uploaded profile photo instead of initials', async () => {
  await show({
    ...signedIn,
    profile: { ...signedIn.profile, avatarUrl: 'data:image/png;base64,AAAA' },
  });
  expect(document.querySelector('img')).toHaveAttribute('src', 'data:image/png;base64,AAAA');
  expect(screen.queryByText('AL')).toBeNull();
});

it('the menu opens Account settings in the app, or signs out', async () => {
  const { account } = await show(signedIn);
  fireEvent.click(screen.getByRole('button', { name: /Ana Lima/ }));
  fireEvent.click(await screen.findByText('account.menu_settings'));
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/settings/account' });
  fireEvent.click(screen.getByRole('button', { name: /Ana Lima/ }));
  fireEvent.click(await screen.findByText('account.sign_out'));
  expect(account.signOut).toHaveBeenCalled();
});

const withPlan = {
  ...signedIn,
  plan: {
    planCode: 'free',
    planName: 'Free',
    planOrigin: 'default',
    planEndsAt: null,
    storageUsedBytes: 25 * 1024 * 1024,
    storageLimitBytes: 100 * 1024 * 1024,
    settingsSync: true,
    proDesktop: false,
  },
  links: {
    profile: 'https://voicestudio.example/settings/profile',
    plan: 'https://voicestudio.example/settings/plan',
    deletion: 'https://voicestudio.example/settings/delete',
  },
};

it('the menu opens with an account card: photo, name, email, plan and storage', async () => {
  await show({
    ...withPlan,
    profile: { ...withPlan.profile, avatarUrl: 'data:image/png;base64,AAAA' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Ana Lima' }));
  const card = await screen.findByText('ana@example.com');
  expect(card).toHaveAttribute('data-slot', 'account-email');
  // The trigger and the card both show the cloud photo.
  expect(document.querySelectorAll('img[src="data:image/png;base64,AAAA"]')).toHaveLength(2);
  expect(screen.getByText('Free').closest('[data-plan]')).toHaveAttribute('data-plan', 'free');
  expect(screen.getByText('account.storage_used')).toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: 'account.storage' })).toHaveAttribute(
    'aria-valuenow',
    '25',
  );
});

it('Manage plan opens the plan page in the browser', async () => {
  const { account } = await show(withPlan);
  fireEvent.click(screen.getByRole('button', { name: 'Ana Lima' }));
  fireEvent.click(await screen.findByText('account.manage_plan'));
  expect(account.files.openExternal).toHaveBeenCalledWith(withPlan.links.plan);
});

it('without plan details the card shows only the identity, and no Manage plan', async () => {
  await show(signedIn);
  fireEvent.click(screen.getByRole('button', { name: 'Ana Lima' }));
  await screen.findByText('ana@example.com');
  expect(document.querySelector('[data-slot="account-plan"]')).toBeNull();
  expect(screen.queryByText('account.manage_plan')).toBeNull();
});

it('signed out: an icon-only Sign in button that starts the browser sign-in', async () => {
  const { account } = await show({ ...signedIn, signedIn: false, profile: null });
  const button = screen.getByRole('button', { name: 'account.sign_in' });
  expect(button).toHaveTextContent(/^$/);
  expect(button).toHaveAttribute('title', 'account.sign_in');
  fireEvent.click(button);
  expect(account.signIn).toHaveBeenCalled();
});

it('while signing in it says to continue in the browser', async () => {
  await show({ ...signedIn, signedIn: false, signingIn: true, profile: null });
  const button = screen.getByRole('button', { name: 'account.continue_in_browser' });
  expect(button).toBeDisabled();
  // The pending instruction is announced, not only left in a tooltip.
  expect(screen.getByRole('status')).toHaveTextContent('account.continue_in_browser');
  expect(button.querySelector('.animate-spin')).not.toBeNull();
});

it.each([
  ['account features off', { ...signedIn, accountEnabled: false }],
  ['the build has no accounts', { ...signedIn, available: false }],
])('shows nothing when %s', async (_name, state) => {
  const { container } = await show(state);
  expect(container).toBeEmptyDOMElement();
});
