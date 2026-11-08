import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { EngineInstall } from './engine-install';

it('offers repair without marking a working sidecar uninstalled', async () => {
  mock.api.mockResolvedValue({ installed: true, repair_available: true, job: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EngineInstall id="test" />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole('button', { name: 'modelMaintenance.repair' });
  expect(button.hasAttribute('disabled')).toBe(false);
  fireEvent.click(button);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/engines/sidecar/test/install', { method: 'POST' }),
  );
});

it('refreshes engine inventory when a staged repair succeeds', async () => {
  mock.api.mockResolvedValue({
    installed: true,
    repair_available: true,
    job: { state: 'running', steps: [] },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <EngineInstall id="test" />
    </QueryClientProvider>,
  );
  await screen.findByRole('button', { name: 'modelMaintenance.installing' });
  await waitFor(() => expect(invalidate).toHaveBeenCalled());
  invalidate.mockClear();
  client.setQueryData(['engine-install', 'test'], {
    installed: true,
    repair_available: false,
    job: { state: 'succeeded', steps: [] },
  });
  await waitFor(() => expect(invalidate).toHaveBeenCalled());
});
