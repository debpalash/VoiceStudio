import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ open: vi.fn().mockResolvedValue(undefined), navigate: vi.fn() }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mock.open } }),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useSearch: () => ({}),
  useNavigate: () => mock.navigate,
}));
import { SupportSettings } from './support-settings';
vi.mock('./donation-goal', () => ({ DonationGoal: () => null }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it('opens only the explicit destination and applies selected amounts only to PayPal', () => {
  render(<SupportSettings />);
  expect(mock.open).not.toHaveBeenCalled();
  const paypal = screen.getByRole('link', { name: 'PayPal' });
  expect(paypal).toHaveAttribute('href', 'https://paypal.me/palashCoder');
  fireEvent.click(screen.getByRole('button', { name: '$20' }));
  expect(paypal).toHaveAttribute('href', 'https://paypal.me/palashCoder/20');
  expect(screen.getByRole('link', { name: 'Ko-fi' })).toHaveAttribute(
    'href',
    'https://ko-fi.com/debpalash',
  );
  const sponsors = screen.getAllByRole('link', { name: 'GitHub Sponsors' });
  expect(sponsors).toHaveLength(2);
  for (const link of sponsors)
    expect(link).toHaveAttribute('href', 'https://github.com/sponsors/debpalash');
  expect(
    within(screen.getByRole('group', { name: 'donate.choose_method' })).getAllByRole('link')[0],
  ).toBe(sponsors[0]);
  fireEvent.click(paypal);
  expect(mock.open).toHaveBeenCalledOnce();
  expect(mock.open).toHaveBeenCalledWith('https://paypal.me/palashCoder/20');
  expect(screen.getByRole('link', { name: 'contact.security_cta' })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio/security/advisories/new',
  );
  expect(screen.getByRole('link', { name: 'support.sponsors_become' })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio/issues/new?template=sponsor.yml',
  );
  fireEvent.click(screen.getByRole('button', { name: 'supportPlans.get_pro' }));
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/pro' });
});

it('keeps contact and Pro actions visible and lets the donor clear an amount', () => {
  render(<SupportSettings />);
  expect(screen.getByRole('button', { name: 'supportPlans.get_pro' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'contact.security_cta' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'donate.custom' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
  const amount = screen.getByRole('button', { name: '$50' });
  fireEvent.click(amount);
  expect(amount).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(amount);
  expect(amount).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('link', { name: 'PayPal' })).toHaveAttribute(
    'href',
    'https://paypal.me/palashCoder',
  );
  fireEvent.click(screen.getByRole('button', { name: 'donate.custom' }));
  expect(screen.getByRole('link', { name: 'PayPal' })).toHaveAttribute(
    'href',
    'https://paypal.me/palashCoder',
  );
});
