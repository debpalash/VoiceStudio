import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/app-activity', () => ({ useAppActivities: () => ({}) }));
vi.mock('./system-preflight', () => ({ SystemPreflight: () => null }));
vi.mock('@/components/performance-profile', () => ({ PerformanceProfile: () => null }));
vi.mock('@shared/components/SearchableSelect', () => ({
  default: ({ onChange, disabled }: { onChange: (value: string) => void; disabled: boolean }) => (
    <button disabled={disabled} onClick={() => onChange('GPU-second')}>
      Choose adapter
    </button>
  ),
}));
import { PerformanceSettings } from './performance-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it.each([null, 'cuda', 'compute'])(
  'recovers from a CUDA save error (refetch fails: %s)',
  async (failRefetch) => {
    let retried = false;
    mock.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/settings/cuda-device') {
        if (init?.method === 'PUT') throw new Error('Save failed');
        if (retried && failRefetch === 'cuda') throw new Error('Refetch failed');
        return {
          value: 'auto',
          devices: [
            { value: 'GPU-first', index: 0, name: 'First' },
            { value: 'GPU-second', index: 1, name: 'Second' },
          ],
        };
      }
      if (path === '/api/settings/compute-device') {
        if (retried && failRefetch === 'compute') throw new Error('Refetch failed');
        return { value: 'auto', effective_family: 'cuda', available_families: ['cpu', 'cuda'] };
      }
      if (path === '/model/loaded') return { models: [], count: 0 };
      return {};
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PerformanceSettings />
      </QueryClientProvider>,
    );
    const selector = await screen.findByRole('button', { name: 'Choose adapter' });
    await waitFor(() => expect(selector).toBeEnabled());
    fireEvent.click(selector);
    await screen.findByRole('alert');
    retried = true;
    const reads = (endpoint: string) =>
      mock.api.mock.calls.filter(
        ([path, init]) => path === endpoint && (!init?.method || init.method === 'GET'),
      ).length;
    const endpoints = ['/api/settings/compute-device', '/api/settings/cuda-device'];
    const beforeRetry = endpoints.map(reads);
    fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() =>
      endpoints.forEach((endpoint, index) => {
        expect(reads(endpoint)).toBe(beforeRetry[index] + 1);
      }),
    );
    await waitFor(() => expect(client.isFetching()).toBe(0));
    if (failRefetch) expect(screen.getByRole('alert')).toBeInTheDocument();
    else await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  },
);

it('saves the offload-to-RAM toggle and locks it when an env var pins it (#2618)', async () => {
  let state = { enabled: false, env_pinned: false, device: 'cuda' };
  mock.api.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/settings/perf/offload-after-generation') {
      if (init?.method === 'PUT') {
        state = { ...state, ...(JSON.parse(String(init.body)) as { enabled: boolean }) };
      }
      return state;
    }
    if (path === '/api/settings/cuda-device') return { value: 'auto', devices: [] };
    if (path === '/api/settings/compute-device') {
      return { value: 'auto', effective_family: 'cuda', available_families: ['cpu', 'cuda'] };
    }
    if (path === '/model/loaded') return { models: [], count: 0 };
    return {};
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PerformanceSettings />
    </QueryClientProvider>,
  );
  const toggle = await screen.findByRole('switch', { name: 'settings.offload_after_generation' });
  await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled', 'true'));
  expect(toggle).not.toBeChecked();
  expect(screen.getByText('settings.offload_after_generation_note')).toBeInTheDocument();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(mock.api).toHaveBeenCalledWith(
    '/api/settings/perf/offload-after-generation',
    expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }),
  );

  cleanup();
  state = { enabled: false, env_pinned: true, device: 'cpu' };
  const pinnedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={pinnedClient}>
      <PerformanceSettings />
    </QueryClientProvider>,
  );
  await screen.findByText('settings.offload_after_generation_cpu');
  expect(screen.getByRole('switch', { name: 'settings.offload_after_generation' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(screen.getByText('settings.generate_timeout_shadowed_note')).toBeInTheDocument();
});
