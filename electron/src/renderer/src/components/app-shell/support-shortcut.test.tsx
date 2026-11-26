import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown, open: vi.fn() }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, ...rest }: { children?: unknown }) => (
    <a {...rest}>{children as never}</a>
  ),
}));
vi.mock('./github-star', () => ({ GithubStar: () => null }));

import { SupportShortcut } from './support-shortcut';

function plan(over: Record<string, unknown>) {
  return {
    planCode: 'free',
    planName: 'Free',
    planOrigin: 'default',
    planEndsAt: null,
    storageUsedBytes: 0,
    storageLimitBytes: 0,
    settingsSync: null,
    proDesktop: null,
    ...over,
  };
}
async function show(state: Record<string, unknown>) {
  mock.bridge = {
    account: { get: async () => state, onState: () => () => {} },
    files: { openExternal: mock.open },
  };
  render(<SupportShortcut />);
  await act(async () => {});
}
afterEach(() => {
  cleanup();
  mock.bridge = null;
  mock.open.mockReset();
});

const links = { proDownload: 'https://app.voicestudio.sh/pro/download' };
const proPlan = plan({ planCode: 'pro_lifetime', planOrigin: 'purchase' });

it('offers Get Pro to a signed-out user, linking to the plans page', async () => {
  await show({ available: true, accountEnabled: true, signedIn: false, plan: null });
  expect(screen.getAllByText('supportPlans.get_pro').length).toBeGreaterThan(0);
  expect(screen.queryByText('supportPlans.get_pro_app')).toBeNull();
});

it('offers Upgrade to Pro to a signed-in Free account', async () => {
  await show({ available: true, accountEnabled: true, signedIn: true, plan: plan({}), links });
  expect(screen.getAllByText('supportPlans.upgrade_pro').length).toBeGreaterThan(0);
  expect(screen.queryByText('supportPlans.get_pro')).toBeNull();
});

it('offers Get Pro app to Pro accounts and opens the dashboard download page', async () => {
  await show({ available: true, accountEnabled: true, signedIn: true, plan: proPlan, links });
  expect(screen.queryByText('supportPlans.get_pro')).toBeNull();
  fireEvent.click(screen.getAllByRole('button', { name: 'supportPlans.get_pro_app' })[0]);
  await act(async () => {});
  expect(mock.open).toHaveBeenCalledWith('https://app.voicestudio.sh/pro/download');
  cleanup();
  await show({ available: true, accountEnabled: true, signedIn: true, plan: plan({ proDesktop: true }), links });
  expect(screen.getAllByText('supportPlans.get_pro_app').length).toBeGreaterThan(0);
});

it('falls back to Upgrade to Pro when the links lack a Pro download', async () => {
  await show({ available: true, accountEnabled: true, signedIn: true, plan: proPlan, links: null });
  expect(screen.getAllByText('supportPlans.upgrade_pro').length).toBeGreaterThan(0);
  expect(screen.queryByRole('button')).toBeNull();
});
