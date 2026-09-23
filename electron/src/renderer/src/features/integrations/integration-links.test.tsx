import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import '@/i18n';
import { IntegrationsPage } from './integrations-page';
import { IntegrationDetailPage } from './integration-detail-page';

const navigate = vi.hoisted(() => vi.fn());
const route = vi.hoisted(() => ({ slug: 'acme-voice' }));
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
vi.mock('../../../../../../frontend/src/config/sponsors', () => ({
  SPONSORS: [
    { name: 'Acme Voice', logoUrl: 'acme.svg', url: 'https://acme.example', tier: 'gold' },
  ],
}));
const bridge = vi.hoisted(() => ({ openExternal: vi.fn() }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: bridge.openExternal } }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('opens every catalog and featured logo in-app, never the vendor website', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null);
  const { container } = render(<IntegrationsPage />);
  expect(container.querySelector('.lucide-external-link')).toBeNull();
  const featured = container.querySelector<HTMLButtonElement>('.integration-featured-card')!;
  fireEvent.click(featured);
  await waitFor(() =>
    expect(navigate).toHaveBeenCalledWith({
      to: '/integrations/$slug',
      params: { slug: 'acme-voice' },
    }),
  );
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

it('gives a featured sponsor its own page, with the website only on its Website card', () => {
  render(<IntegrationDetailPage />);
  expect(screen.getByRole('heading', { name: 'Acme Voice', level: 2 })).toBeInTheDocument();
  expect(bridge.openExternal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  expect(bridge.openExternal).toHaveBeenCalledWith('https://acme.example');
});
