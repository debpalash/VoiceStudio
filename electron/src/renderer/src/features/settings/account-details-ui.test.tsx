import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import '@/i18n';
import type { AccountPublicState } from '../../../../preload/index.d';
import { AccountDetails } from './account-details';

const bridge = vi.hoisted(() => ({ openExternal: vi.fn() }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: bridge.openExternal } }),
}));

const state = (email: string, orgId: string, extra: object = {}): AccountPublicState =>
  ({
    signedIn: true,
    profile: {
      email,
      displayName: 'Ana',
      avatarUrl: null,
      personalOrganizationId: orgId,
      organizations: [],
    },
    profileError: null,
    plan: null,
    links: {
      profile: 'https://x.test/p',
      plan: 'https://x.test/b',
      deletion: 'https://x.test/d',
    },
    ...extra,
  }) as unknown as AccountPublicState;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('drops an open name edit when a different account signs in', () => {
  const onRename = vi.fn();
  const { rerender } = render(
    <AccountDetails state={state('a@x.com', 'o1')} onRename={onRename} />,
  );
  fireEvent.click(screen.getByRole('button', { name: /edit/i }));
  expect(screen.getByRole('textbox')).toBeTruthy();
  rerender(
    <AccountDetails state={{ ...state('a@x.com', 'o1'), signedIn: false }} onRename={onRename} />,
  );
  rerender(<AccountDetails state={state('b@x.com', 'o2')} onRename={onRename} />);
  expect(screen.queryByRole('textbox')).toBeNull();
});

it('Escape during IME composition keeps the editor open', () => {
  render(<AccountDetails state={state('a@x.com', 'o1')} onRename={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /edit/i }));
  const input = screen.getByRole('textbox');
  fireEvent.keyDown(input, { key: 'Escape', isComposing: true });
  expect(screen.getByRole('textbox')).toBeTruthy();
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(screen.queryByRole('textbox')).toBeNull();
});

it('shows an error when opening the photo page fails', async () => {
  bridge.openExternal.mockRejectedValue(new Error('no'));
  render(<AccountDetails state={state('a@x.com', 'o1')} />);
  fireEvent.click(screen.getByTestId('account-avatar'));
  await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
});
