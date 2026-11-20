import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown }));
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
  };
  render(<SupportShortcut />);
  await act(async () => {});
}
afterEach(() => {
  cleanup();
  mock.bridge = null;
});

it('shows Get Pro for a Free account', async () => {
  await show({ signedIn: true, plan: plan({}) });
  expect(screen.getAllByText('supportPlans.get_pro').length).toBeGreaterThan(0);
});

it('hides Get Pro for a lifetime Pro purchase, with or without entitlements', async () => {
  await show({
    signedIn: true,
    plan: plan({ planCode: 'pro_lifetime', planOrigin: 'purchase' }),
  });
  expect(screen.queryByText('supportPlans.get_pro')).toBeNull();
  cleanup();
  await show({ signedIn: true, plan: plan({ proDesktop: true }) });
  expect(screen.queryByText('supportPlans.get_pro')).toBeNull();
});
