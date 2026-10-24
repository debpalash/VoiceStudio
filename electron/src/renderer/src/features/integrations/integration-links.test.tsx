import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import '@/i18n';
import { IntegrationsPage } from './integrations-page';
import { IntegrationDetailPage } from './integration-detail-page';

const navigate = vi.hoisted(() => vi.fn());
const route = vi.hoisted(() => ({ slug: 'codex-cli' }));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => route,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ baseUrl: 'http://127.0.0.1:3912' }),
}));
const toast = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock('sonner', () => ({ toast }));
const bridge = vi.hoisted(() => ({ openExternal: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: bridge.openExternal } }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

it('opens wired integration cards in-app, never the vendor website', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null);
  const { container } = render(<IntegrationsPage />);
  expect(container.querySelector('.lucide-external-link')).toBeNull();
  fireEvent.click(screen.getByRole('heading', { name: 'Twilio' }).closest('button')!);
  await waitFor(() =>
    expect(navigate).toHaveBeenLastCalledWith({
      to: '/integrations/$slug',
      params: { slug: 'twilio' },
    }),
  );
  expect(bridge.openExternal).not.toHaveBeenCalled();
  expect(open).not.toHaveBeenCalled();
});

it('keeps the vendor website action on the wired integration detail page', () => {
  render(<IntegrationDetailPage />);
  expect(screen.getByRole('heading', { name: 'Codex CLI', level: 2 })).toBeInTheDocument();
  expect(bridge.openExternal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  expect(bridge.openExternal).toHaveBeenCalledWith('https://github.com/openai/codex');
});

it('reports a refused vendor website open instead of leaving the rejection unhandled', async () => {
  const cause = new Error('Refusing to open ftp: URLs');
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  bridge.openExternal.mockRejectedValueOnce(cause);
  render(<IntegrationDetailPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      'Could not open the website. Check your default browser and try again.',
    ),
  );
  expect(toast.error).not.toHaveBeenCalledWith('Refusing to open ftp: URLs');
  expect(warning).toHaveBeenCalledWith('Could not open external link:', cause.message);
  warning.mockRestore();
});
