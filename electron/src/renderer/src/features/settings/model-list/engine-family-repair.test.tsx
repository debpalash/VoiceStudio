import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { EngineFamilyList } from './engine-family-list';

it('offers a safe repair on a working one-click sidecar row', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') return Promise.resolve({ models: [] });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'sidecar',
          backends: [
            {
              id: 'sidecar',
              display_name: 'Sidecar',
              available: true,
              one_click_install: true,
            },
          ],
        },
      });
    if (path === '/engines/sidecar/sidecar/install/status')
      return Promise.resolve({ installed: true, repair_available: true, job: null });
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EngineFamilyList family="tts" />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole('button', { name: 'modelMaintenance.repair' })).toBeTruthy();
});
