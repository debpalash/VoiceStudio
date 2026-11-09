import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import '@/i18n';
import { DownloadActivityList, useDownloadActivity } from './download-activity';

const mock = vi.hoisted(() => ({ api: vi.fn(), jobs: [] as Record<string, unknown>[] }));
vi.mock('@/hooks/use-model-install-sync', () => ({
  useModelInstallJobs: () => ({ data: { jobs: mock.jobs } }),
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
afterEach(() => {
  cleanup();
  mock.api.mockReset();
  mock.jobs = [];
});

function Panel() {
  return <DownloadActivityList activity={useDownloadActivity()} />;
}

function mount() {
  const client = new QueryClient();
  client.setQueryData(['model-catalogue'], {
    models: [{ repo_id: 'org/whisper-small', label: 'Whisper Small' }],
  });
  return render(
    <QueryClientProvider client={client}>
      <Panel />
    </QueryClientProvider>,
  );
}

it('lists running downloads by name with Cancel, and failures with Retry and Dismiss', async () => {
  mock.api.mockResolvedValue({});
  mock.jobs = [
    { repo_id: 'org/whisper-small', state: 'downloading', bytes_done: 1, total_bytes: 4 },
    { repo_id: 'org/other-model', state: 'failed', failed_at: 3, error: 'HTTP 503' },
  ];
  mount();
  expect(screen.getByRole('region', { name: 'Downloads' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Whisper Small · Downloading · 25%');
  expect(screen.getByRole('alert')).toHaveTextContent('other-model · Download failed — HTTP 503');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install/cancel', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'org/whisper-small' }),
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'org/other-model' }),
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('renders nothing when no download is running or failed', () => {
  mock.jobs = [{ repo_id: 'org/x', state: 'cancelled' }];
  const { container } = mount();
  expect(container).toBeEmptyDOMElement();
});
