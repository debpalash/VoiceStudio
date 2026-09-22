import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import '@/i18n';
import { IntegrationDetailPage } from './integration-detail-page';
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
  expect(screen.getByText(/OpenAI Agents SDK voice pipeline/)).toBeInTheDocument();
  expect(screen.getByText('Works with VoiceStudio')).toBeInTheDocument();
  expect(screen.getByText('Local language model')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
  expect(copy.mock.calls[0][0]).toContain('base_url="http://127.0.0.1:3912/v1"');
  expect(screen.queryByRole('button', { name: 'Save as…' })).not.toBeInTheDocument();
});
