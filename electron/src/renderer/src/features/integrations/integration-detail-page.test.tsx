import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import '@/i18n';
import { IntegrationDetailPage } from './integration-detail-page';
import { INTEGRATION_SETUPS } from './setup-registry';
import en from '@/i18n/locales/en.json';
const route = vi.hoisted(() => ({ slug: 'claude-code' }));
const save = vi.hoisted(() => vi.fn());
vi.mock('@/lib/local-export', () => ({ saveLocalFile: save }));
beforeEach(() => {
  route.slug = 'claude-code';
  vi.clearAllMocks();
});
afterEach(cleanup);
vi.mock('@tanstack/react-router', () => ({
  useParams: () => route,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ baseUrl: 'http://127.0.0.1:3912' }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast }));
vi.mock('./twilio-setup', () => ({
  TWILIO_DOCS: 'https://github.com/debpalash/VoiceStudio/blob/main/docs/integrations/twilio.md',
  TwilioSetup: ({
    hero,
    rail,
  }: {
    hero: (slots: { status?: ReactNode; action?: ReactNode }) => ReactNode;
    rail: ReactNode;
  }) => (
    <>
      {hero({ status: <span role="status">Ready</span> })}
      <div>Twilio phone setup panel</div>
      {rail}
    </>
  ),
}));
it('copies the shown live configuration only after the user requests it', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true });
  render(<IntegrationDetailPage />);
  expect(screen.getByText(/Merge this configuration into/)).toHaveTextContent('.mcp.json');
  expect(copy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
  expect(JSON.parse(copy.mock.calls[0][0]).mcpServers.voicestudio.url).toBe(
    'http://127.0.0.1:3912/mcp/',
  );
});

it('exports the n8n workflow only on request and handles canceled saves', async () => {
  route.slug = 'n8n';
  save.mockResolvedValue({ canceled: true });
  render(<IntegrationDetailPage />);
  expect(screen.getByText(/Import this workflow into n8n/)).toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save as…' }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][1]).toBe('voicestudio-n8n.json');
  expect(toast.success).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save as…' })).toBeEnabled());
  save.mockRejectedValue(new Error('disk full'));
  fireEvent.click(screen.getByRole('button', { name: 'Save as…' }));
  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('disk full')),
  );
});

it('shows the Codex TOML export and one heading per panel', () => {
  route.slug = 'codex-cli';
  render(<IntegrationDetailPage />);
  expect(screen.getByText(/\[mcp_servers\.voicestudio\]/)).toBeInTheDocument();
  expect(screen.getByText(/Merge this configuration into/)).toHaveTextContent(
    '~/.codex/config.toml',
  );
  expect(screen.getByRole('heading', { name: 'Set up Codex CLI' })).toBeInTheDocument();
  expect(screen.getByText('Works with VoiceStudio')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Details' })).toBeNull();
});

it('presents entries without a setup block as external links with no capability claims', () => {
  route.slug = 'zapier';
  render(<IntegrationDetailPage />);
  expect(screen.queryByRole('button', { name: 'Copy' })).toBeNull();
  expect(screen.queryByText('Works with VoiceStudio')).toBeNull();
  expect(screen.getByRole('heading', { name: 'Website' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Capabilities' })).toBeInTheDocument();
});

it('offers copyable API snippets for the current backend', () => {
  route.slug = 'voicestudio-api';
  render(<IntegrationDetailPage />);
  expect(screen.getAllByRole('button', { name: 'Copy' })).toHaveLength(4);
  expect(
    screen.getByText(/curl http:\/\/127\.0\.0\.1:3912\/v1\/audio\/speech/),
  ).toBeInTheDocument();
});

it('shows the OpenAI Agents snippet for the live backend', async () => {
  route.slug = 'openai-agents';
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true });
  render(<IntegrationDetailPage />);
  expect(screen.getByText(/Point the OpenAI Agents SDK voice pipeline/)).toBeInTheDocument();
  expect(screen.getByText('Works with VoiceStudio')).toBeInTheDocument();
  expect(screen.getByText('Local language model')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
  expect(copy.mock.calls[0][0]).toContain('base_url="http://127.0.0.1:3912/v1"');
  expect(screen.queryByRole('button', { name: 'Save as…' })).not.toBeInTheDocument();
});

it('renders Twilio from the registry as a working connector with its setup panel', () => {
  route.slug = 'twilio';
  render(<IntegrationDetailPage />);
  expect(screen.getByRole('heading', { name: 'Twilio', level: 2 })).toBeInTheDocument();
  expect(screen.getByText('Twilio phone setup panel')).toBeInTheDocument();
  expect(screen.getByText('Works with VoiceStudio')).toBeInTheDocument();
  expect(screen.getByText('Phone calls')).toBeInTheDocument();
  expect(screen.queryByText(/Directory examples only/)).toBeNull();
  expect(screen.queryByText(/Setup is unavailable/)).toBeNull();
});

it('labels Twilio with its own category and tagline, not borrowed dubbing or generic copy', () => {
  route.slug = 'twilio';
  render(<IntegrationDetailPage />);
  expect(screen.getByText('Calling & voice agents')).toBeInTheDocument();
  expect(screen.queryByText('Dubbing')).toBeNull();
  expect(screen.getByText('Make and answer phone calls in your own voice.')).toBeInTheDocument();
  expect(screen.queryByText(/Copy a ready-made setup/)).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('Ready');
});

it('gives every working integration its own tagline and exactly one docs link', () => {
  const taglines = new Set<string>();
  for (const [slug, setup] of Object.entries(INTEGRATION_SETUPS)) {
    route.slug = slug;
    const { unmount } = render(<IntegrationDetailPage />);
    const tagline = document.querySelector('.integration-detail-tagline')?.textContent ?? '';
    expect(tagline, slug).not.toBe('');
    expect(tagline, slug).not.toBe(setup.taglineKey);
    taglines.add(setup.taglineKey);
    const docs = screen.getAllByRole('link', { name: 'Learn more' });
    expect(docs, slug).toHaveLength(1);
    expect(docs[0]).toHaveAttribute('href', setup.docs);
    unmount();
  }
  // Clients of one kind may share a tagline; distinct kinds do not.
  expect(taglines.size).toBeGreaterThanOrEqual(7);
  const catalog = en.integrationCatalog.tagline as Record<string, string>;
  for (const key of taglines) expect(catalog[key.split('.').at(-1)!], key).toBeTruthy();
});

it('lays out setup beside a rail inside a full-width scroller', () => {
  route.slug = 'twilio';
  const { container } = render(<IntegrationDetailPage />);
  const scroller = container.querySelector('main.integrations-content')!;
  // The width cap is on an inner column, so the scrollbar sits at the window edge.
  expect(scroller.firstElementChild).toHaveClass('integrations-container');
  route.slug = 'n8n';
  cleanup();
  const { container: n8n } = render(<IntegrationDetailPage />);
  const rail = n8n.querySelector('.integration-detail-rail')!;
  expect(within(rail as HTMLElement).getByRole('heading', { name: 'Capabilities' })).toBeVisible();
  expect(within(rail as HTMLElement).getByRole('heading', { name: 'Website' })).toBeVisible();
  const main = n8n.querySelector('.integration-detail-main')!;
  expect(within(main as HTMLElement).getByRole('heading', { name: 'Set up n8n' })).toBeVisible();
});
