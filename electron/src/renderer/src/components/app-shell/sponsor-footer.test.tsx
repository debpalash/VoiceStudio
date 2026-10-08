import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  open: vi.fn().mockResolvedValue(undefined),
  navigate: vi.fn(),
}));

vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mock.open } }),
}));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mock.navigate }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { SponsorFooter } from './sponsor-footer';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it('places Discord before X and donate before the collapse control', async () => {
  render(<SponsorFooter />);
  const buttons = screen.getByRole('contentinfo').querySelectorAll('button');
  expect(buttons).toHaveLength(5);
  expect(buttons[2]).toHaveAccessibleName('contact.follow_cta');
  expect(buttons[3]).toHaveAccessibleName('donate.title');
  expect(buttons[4]).toHaveAccessibleName('homeUi.collapseFooter');
  expect(screen.queryByRole('link', { name: 'support.star_github' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'contact.website' })).toHaveAttribute(
    'href',
    'https://voicestudio.sh',
  );
  expect(screen.queryByRole('img')).toBeNull();
  const discord = screen.getByRole('link', { name: 'support.join_discord' });
  expect(discord).toHaveAttribute('href', 'https://discord.gg/bzQavDfVV9');
  expect(discord.nextElementSibling).toBe(buttons[2]);
  fireEvent.click(discord);
  await waitFor(() => expect(mock.open).toHaveBeenCalledWith('https://discord.gg/bzQavDfVV9'));
});

it.each(['idebpalash', 'voicestudiosh'])('opens @%s in the external browser', async (handle) => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'contact.follow_cta' }));
  expect(await screen.findByText('Palash')).toBeVisible();
  expect(screen.getByText('VoiceStudio')).toBeVisible();
  const link = screen.getByRole('link', { name: `contact.follow_cta · @${handle}` });
  expect(link).toHaveAttribute('href', `https://x.com/${handle}`);
  fireEvent.click(link);
  await waitFor(() => expect(mock.open).toHaveBeenCalledWith(`https://x.com/${handle}`));
});

it('opens compact support links from the heart and links to the full page', async () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'donate.title' }));
  expect(await screen.findByRole('link', { name: 'Ko-fi' })).toHaveAttribute(
    'href',
    'https://ko-fi.com/debpalash',
  );
  expect(screen.getByRole('link', { name: 'PayPal' })).toHaveAttribute(
    'href',
    'https://paypal.me/palashCoder',
  );
  expect(screen.getByRole('link', { name: 'support.star_github' })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio',
  );
  expect(screen.getByRole('link', { name: 'support.join_discord' })).toHaveAttribute(
    'href',
    'https://discord.gg/bzQavDfVV9',
  );
  expect(document.querySelector('.sponsor-support-popup a[href*="discord"]')).toBeNull();
  fireEvent.click(screen.getByRole('link', { name: 'Ko-fi' }));
  await waitFor(() => expect(mock.open).toHaveBeenCalledWith('https://ko-fi.com/debpalash'));
  fireEvent.click(screen.getAllByRole('button', { name: 'donate.title' })[1]);
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/settings/support' });
});

it('opens Integrations from the first button', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'integrationCatalog.title' }));
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/integrations' });
});

it('opens the booking form from Become a Sponsor', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.footer_brand' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(mock.open).not.toHaveBeenCalled();
});

it('shows sourced audience details on sponsor focus', async () => {
  render(<SponsorFooter />);
  fireEvent.focus(screen.getByRole('button', { name: 'sponsorSlot.footer_brand' }));
  expect(await screen.findByText('sponsorSlot.footer_promo')).toBeVisible();
  expect(screen.getByText('22,678')).toBeVisible();
  expect(screen.getByText('207,236')).toBeVisible();
  expect(screen.getByText('35,336')).toBeVisible();
  expect(screen.getByText('sponsorSlot.footer_stats_note')).toBeVisible();
});

it('collapses and restores the footer without navigating to Pro', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'homeUi.collapseFooter' }));
  expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();
  expect(mock.navigate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'homeUi.expandFooter' }));
  expect(screen.getByRole('contentinfo')).toBeVisible();
  expect(screen.getByRole('button', { name: 'homeUi.collapseFooter' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
});

it('keeps the email fallback available from the booking form', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.footer_brand' }));
  fireEvent.click(screen.getByRole('tab', { name: 'sponsorSlot.email' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'sponsorSlot.message' }), {
    target: { value: 'My brand and website' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.copy_email' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('partner@voicestudio.sh'));
  expect(screen.getByRole('textbox')).toHaveValue('My brand and website');
});
