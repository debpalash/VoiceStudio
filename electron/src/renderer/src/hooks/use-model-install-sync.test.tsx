import { act, cleanup, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import {
  ModelInstallSync,
  modelInstallPollInterval,
  type ModelInstallJobsResponse,
} from './use-model-install-sync';
import { useDownloadActivity } from '@/components/app-shell/download-activity';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  jobs: {
    jobs: [{ repo_id: 'k2-fsa/OmniVoice', state: 'downloading' }],
  } as ModelInstallJobsResponse,
}));

vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ stage: 'ready' }),
}));

afterEach(() => {
  cleanup();
  mock.api.mockReset();
  mock.jobs = {
    jobs: [{ repo_id: 'k2-fsa/OmniVoice', state: 'downloading' }],
  };
});

it('invalidates every readiness surface when an observed install completes', async () => {
  mock.api.mockImplementation(() => Promise.resolve(mock.jobs));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['model-install-jobs'], mock.jobs);
  client.setQueryData(['engines'], { stale: false });
  render(
    <QueryClientProvider client={client}>
      <ModelInstallSync />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(client.getQueryData(['model-install-jobs'])).toEqual(mock.jobs));
  await act(async () => {});

  mock.jobs = { jobs: [] };
  act(() => client.setQueryData(['model-install-jobs'], mock.jobs));

  await waitFor(() => {
    expect(client.getQueryState(['engines'])?.isInvalidated).toBe(true);
  });
});

it.each([
  null,
  {},
  { jobs: null },
  { jobs: {} },
  { jobs: [null] },
  { jobs: [{ repo_id: 'model' }] },
])('keeps the app mounted for malformed install status %j', async (body) => {
  mock.api.mockResolvedValue(body);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // A browser mock or another mounted observer can populate the shared cache.
  client.setQueryData(['model-install-jobs'], body);
  expect(() =>
    render(
      <QueryClientProvider client={client}>
        <ModelInstallSync />
      </QueryClientProvider>,
    ),
  ).not.toThrow();
  await waitFor(() => expect(client.getQueryState(['model-install-jobs'])?.status).toBe('error'));
  expect(() =>
    modelInstallPollInterval({ state: { data: body as ModelInstallJobsResponse } }),
  ).not.toThrow();
});

it('does not report an install completed because an invalid response arrived', async () => {
  mock.api.mockImplementation(() => Promise.resolve(mock.jobs));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['model-install-jobs'], mock.jobs);
  client.setQueryData(['engines'], { stale: false });
  render(
    <QueryClientProvider client={client}>
      <ModelInstallSync />
    </QueryClientProvider>,
  );
  await act(async () => {});
  mock.api.mockResolvedValue({});
  await act(async () => {
    await client.refetchQueries({ queryKey: ['model-install-jobs'] });
  });
  expect(client.getQueryState(['model-install-jobs'])?.status).toBe('error');
  expect(client.getQueryData(['model-install-jobs'])).toEqual(mock.jobs);
  expect(client.getQueryState(['engines'])?.isInvalidated).toBe(false);
  mock.api.mockResolvedValue({ jobs: [] });
  await act(async () => {
    await client.refetchQueries({ queryKey: ['model-install-jobs'] });
  });
  await waitFor(() => expect(client.getQueryState(['engines'])?.isInvalidated).toBe(true));
});

it.each([
  { error: { message: 'failed' } },
  { docs_topic: {} },
  { phase: [] },
  { bytes_done: 'many' },
  { rate: Number.POSITIVE_INFINITY },
])('protects a real download consumer from malformed job fields %j', async (fields) => {
  const body = { jobs: [{ repo_id: 'model', state: 'failed', ...fields }] };
  mock.api.mockResolvedValue(body);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['model-install-jobs'], body);
  function Downloads() {
    const activity = useDownloadActivity();
    return <span>{activity.items.length}</span>;
  }
  expect(() =>
    render(
      <QueryClientProvider client={client}>
        <Downloads />
      </QueryClientProvider>,
    ),
  ).not.toThrow();
  await waitFor(() => expect(client.getQueryState(['model-install-jobs'])?.status).toBe('error'));
});

it('accepts nullable optional fields emitted by the Python backend', async () => {
  const body = {
    jobs: [
      {
        repo_id: 'model',
        state: 'failed',
        error: null,
        docs_topic: null,
        eta_seconds: null,
        total_bytes: null,
        bytes_done: 0,
      },
    ],
  };
  mock.api.mockResolvedValue(body);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Downloads() {
    return <span>{useDownloadActivity().items.length}</span>;
  }
  const screen = render(
    <QueryClientProvider client={client}>
      <Downloads />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByText('1')).toBeTruthy());
  expect(client.getQueryState(['model-install-jobs'])?.status).toBe('success');
});
