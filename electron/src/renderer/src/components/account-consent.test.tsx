import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown }));
vi.mock('./bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { AccountConsent } from './account-consent';

const base = {
  available: true,
  accountEnabled: false,
  syncEnabled: false,
  prompted: false,
  remembered: true,
  signedIn: false,
  signingIn: false,
  notice: null,
  profile: null,
};
function bridgeWith(state: Partial<typeof base>) {
  const account = {
    get: vi.fn(async () => ({ ...base, ...state })),
    onState: vi.fn(() => () => {}),
    setSwitches: vi.fn(async (p: object) => ({ ...base, ...state, ...p, prompted: true })),
    signIn: vi.fn(),
    cancelSignIn: vi.fn(),
    signOut: vi.fn(),
  };
  mock.bridge = { account };
  return account;
}
afterEach(() => {
  cleanup();
  mock.bridge = null;
});

it('asks with two equal Yes/No buttons when unprompted', async () => {
  bridgeWith({});
  const required = vi.fn();
  render(<AccountConsent onRequirementChange={required} />);
  const yes = await screen.findByText('account.use_yes');
  const no = screen.getByText('account.use_no');
  expect(yes.className).toBe(no.className);
  await waitFor(() => expect(required).toHaveBeenLastCalledWith(true));
});

it('No turns everything off', async () => {
  const account = bridgeWith({});
  render(<AccountConsent />);
  fireEvent.click(await screen.findByText('account.use_no'));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: false, syncEnabled: false });
});

it('Yes turns account features on and leaves sync off', async () => {
  const account = bridgeWith({});
  render(<AccountConsent />);
  fireEvent.click(await screen.findByText('account.use_yes'));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: true });
});

it('requires nothing when unavailable or already answered', async () => {
  for (const state of [{ available: false }, { prompted: true }]) {
    bridgeWith(state);
    const required = vi.fn();
    const { container } = render(<AccountConsent onRequirementChange={required} />);
    await waitFor(() => expect(required).toHaveBeenLastCalledWith(false));
    expect(container).toBeEmptyDOMElement();
    cleanup();
  }
});

it('requires nothing in the browser build (no bridge)', async () => {
  const required = vi.fn();
  render(<AccountConsent onRequirementChange={required} />);
  await waitFor(() => expect(required).toHaveBeenLastCalledWith(false));
});
