import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AnchorHTMLAttributes } from 'react';
const mock = vi.hoisted(() => ({
  examples: [] as { name: string; logoUrl: string; url: string }[],
  sponsors: [] as { name: string; logoUrl: string; url: string; tier: string }[],
  open: vi.fn().mockResolvedValue(undefined),
  navigate: vi.fn(),
}));
vi.mock('../../../../../../frontend/src/config/voice-ai-directory', () => ({
  VOICE_AI_DIRECTORY: mock.examples,
}));
vi.mock('../../../../../../frontend/src/config/sponsors', () => ({
  SPONSORS: mock.sponsors,
  SPONSOR_TIERS: ['gold'],
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mock.open } }),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mock.navigate,
  Link: ({ to, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) => (
    <a href={to} {...props} />
  ),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: { name?: string }) => (params?.name ? `${key} ${params.name}` : key),
  }),
}));
import { SponsorFooter } from './sponsor-footer';
afterEach(() => {
  cleanup();
  mock.sponsors.length = 0;
  mock.examples.length = 0;
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
it('shows a labeled preview and opens the booking form without launching email', () => {
  render(<SponsorFooter />);
  expect(screen.getByRole('button', { name: 'sponsorSlot.book' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.book' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(document.querySelectorAll('img')).toHaveLength(0);
  expect(mock.open).not.toHaveBeenCalled();
});
it('opens a sponsor tile in the app, never an outside link, and shows a themed tooltip on focus', async () => {
  mock.sponsors.push({
    name: 'Example sponsor',
    logoUrl: '/sponsor.svg',
    url: 'https://example.org',
    tier: 'gold',
  });
  render(<SponsorFooter />);
  const tile = screen.getByRole('button', { name: 'support.sponsors_logo_aria Example sponsor' });
  expect(tile).not.toHaveAttribute('href');
  expect(tile.querySelector('img')).toHaveAttribute('src', '/sponsor.svg');
  fireEvent.focus(tile);
  await waitFor(() => expect(screen.getByText('support.sponsors_tier_gold')).toBeVisible());
  fireEvent.click(tile);
  // Not in the integration catalog: the catalog page, still in the app.
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/integrations' });
  expect(mock.open).not.toHaveBeenCalled();
  fireEvent.error(tile.querySelector('img')!);
  expect(tile).toHaveTextContent('Example sponsor');
});

it('opens a catalog integration on its in-app detail page', () => {
  mock.examples.push({ name: 'Twilio', url: 'https://www.twilio.com', logoUrl: '/twilio.ico' });
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'support.sponsors_logo_aria Twilio' }));
  expect(mock.navigate).toHaveBeenCalledWith({
    to: '/integrations/$slug',
    params: { slug: 'twilio' },
  });
  expect(mock.open).not.toHaveBeenCalled();
});

it('encodes the message into an email draft and copies only the partner address', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.book' }));
  fireEvent.click(screen.getByRole('tab', { name: 'sponsorSlot.email' }));
  const body = 'Studio & Co\nhttps://example.org/?a=1&b=2\nA logo + a link — hello!';
  fireEvent.change(screen.getByRole('textbox', { name: 'sponsorSlot.message' }), {
    target: { value: body },
  });
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.copy_email' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('partner@voicestudio.sh'));
  expect(mock.open).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole('button', { name: 'sponsorSlot.email_app' }).at(-1)!);
  await waitFor(() => expect(mock.open).toHaveBeenCalledOnce());
  const url = new URL(mock.open.mock.calls[0][0]);
  expect(url.protocol).toBe('mailto:');
  expect(url.pathname).toBe('partner@voicestudio.sh');
  expect(url.searchParams.get('body')).toBe(body);
  expect(screen.getByRole('textbox')).toHaveValue(body);
});

it('prefills an editable sponsor brief for the email fallback', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.book' }));
  fireEvent.click(screen.getByRole('tab', { name: 'sponsorSlot.email' }));
  const value = (screen.getByRole('textbox') as HTMLTextAreaElement).value;
  expect(value).toBe('sponsorSlot.email_template');
});

it('opens the Google Form in the browser from the form tab', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.book' }));
  fireEvent.click(screen.getByRole('button', { name: 'network.open_in_browser' }));
  expect(mock.open).toHaveBeenCalledWith('https://forms.gle/2PYCvd39hbwijzX37');
});

it('opens the booking modal from the tooltip call to action', async () => {
  render(<SponsorFooter />);
  const trigger = screen.getByRole('button', { name: 'sponsorSlot.book' });
  fireEvent.focus(trigger);
  const cta = await screen.findByRole('button', { name: 'sponsorSlot.footer_book' });
  fireEvent.click(cta);
  expect(screen.getByRole('dialog')).toBeVisible();
});

it('keeps the message available if the email app cannot open', async () => {
  mock.open.mockRejectedValueOnce(new Error('no handler'));
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.book' }));
  fireEvent.click(screen.getByRole('tab', { name: 'sponsorSlot.email' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'My proposal' } });
  fireEvent.click(screen.getByRole('button', { name: 'sponsorSlot.email_app' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('common.error'));
  expect(screen.getByRole('textbox')).toHaveValue('My proposal');
  expect(screen.getByRole('button', { name: 'sponsorSlot.copy_email' })).toBeEnabled();
});

it('routes removal to the plan comparison while activation is unavailable', () => {
  render(<SponsorFooter />);
  fireEvent.click(screen.getByRole('button', { name: 'supportPlans.remove' }));
  expect(mock.navigate).toHaveBeenCalledWith({
    to: '/settings/support',
    search: { compare: true },
  });
});

it('opens the full integrations workspace from the footer', () => {
  mock.sponsors.push(
    { name: 'Acme', logoUrl: '/acme.svg', url: 'https://acme.example', tier: 'gold' },
    { name: 'Orbit', logoUrl: '/orbit.svg', url: 'https://orbit.example', tier: '' },
  );
  render(<SponsorFooter />);
  const toggle = screen.getByRole('button', { name: 'integrationCatalog.title' });
  fireEvent.click(toggle);
  expect(mock.navigate).toHaveBeenCalledWith({ to: '/integrations' });
  expect(mock.open).not.toHaveBeenCalled();
});

it('labels company examples without presenting them as featured sponsors', () => {
  mock.examples.push({
    name: 'ElevenLabs',
    url: 'https://elevenlabs.io',
    logoUrl: '/elevenlabs.ico',
  });
  render(<SponsorFooter />);
  expect(screen.getByRole('button', { name: 'support.sponsors_logo_aria ElevenLabs' })).toBeVisible();
  expect(mock.open).not.toHaveBeenCalled();
});
