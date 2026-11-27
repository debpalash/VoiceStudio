import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { EngineNotice } from './engine-notice';
import { useEngineSelect } from '@/features/settings/model-list/use-engine-select';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  toast: vi.fn(),
  jobs: {
    data: {
      jobs: [] as ({ repo_id: string; state: string } & Record<string, unknown>)[],
    },
  },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('sonner', () => ({ toast: { success: mock.toast } }));
vi.mock('@/hooks/use-tts-readiness', () => ({
  useTtsReadiness: () => 'engine',
}));
vi.mock('@/hooks/use-model-install-sync', () => ({
  useModelInstallJobs: () => mock.jobs,
  TERMINAL_MODEL_INSTALL_STATES: new Set(['done', 'failed', 'cancelled', 'install_cancelled']),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  describeError: String,
}));

function renderNotice(operation?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EngineNotice operation={operation} />
    </QueryClientProvider>,
  );
}

function renderCompactNotice() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EngineNotice operation="design" compact />
    </QueryClientProvider>,
  );
}

function mockMissingRecommendedModel() {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/setup/recommendations'
        ? {
            models: [
              { repo_id: 'k2-fsa/OmniVoice', role: 'TTS', required: true, installed: false },
            ],
          }
        : {},
    ),
  );
}

afterEach(() => {
  cleanup();
  mock.api.mockReset();
  mock.toast.mockReset();
  mock.jobs.data.jobs = [];
});

it('installs the required TTS model without sending the user through Settings', async () => {
  mockMissingRecommendedModel();
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenLastCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'k2-fsa/OmniVoice' }),
    }),
  );
  expect(mock.toast).toHaveBeenCalled();
});

it('names the selected model and its size before starting the first-run download', async () => {
  mock.api.mockImplementation(async (path: string) => {
    if (path === '/engines')
      return {
        tts: {
          active: 'local',
          active_model: 'voice/selected',
          backends: [{ id: 'local', available: true }],
        },
      };
    if (path === '/models')
      return {
        models: [
          { repo_id: 'voice/selected', label: 'Selected model', size_gb: 3, installed: false },
        ],
      };
    return {};
  });
  renderNotice('clone');
  const download = await screen.findByRole('button', { name: /Selected model.*3/ });
  expect(mock.api.mock.calls.some(([path]) => path === '/models/install')).toBe(false);
  fireEvent.click(download);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'voice/selected' }),
    }),
  );
});

it('refreshes the download offer after the selected TTS model changes', async () => {
  let selected = 'voice/first';
  mock.api.mockImplementation(async (path: string) => {
    if (path === '/engines/select') {
      selected = 'voice/second';
      return { active: 'local' };
    }
    if (path === '/engines')
      return {
        tts: {
          active: 'local',
          active_model: selected,
          backends: [{ id: 'local', available: true }],
        },
      };
    if (path === '/models')
      return { models: [{ repo_id: selected, installed: false }] };
    return {};
  });
  function SelectModel() {
    const engine = useEngineSelect('tts');
    return <button onClick={() => void engine.select('local', 'voice/second')}>Change model</button>;
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EngineNotice operation="clone" />
      <SelectModel />
    </QueryClientProvider>,
  );
  await screen.findByRole('button', { name: /voice\/first/ });
  fireEvent.click(screen.getByRole('button', { name: 'Change model' }));
  fireEvent.click(await screen.findByRole('button', { name: /voice\/second/ }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'voice/second' }),
    }),
  );
});

it('hands an unexplained readiness failure to the default repair agent', async () => {
  mock.api
    .mockResolvedValueOnce({
      models: [
        {
          repo_id: 'k2-fsa/OmniVoice',
          role: 'TTS',
          required: true,
          installed: true,
        },
      ],
    })
    .mockResolvedValueOnce({ tts: { active: null, backends: [] } });
  const listener = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() => expect(listener).toHaveBeenCalledOnce());
  const event = listener.mock.calls[0]?.[0] as CustomEvent<{
    report: string;
    autoFix: boolean;
  }>;
  expect(event.detail.autoFix).toBe(true);
  expect(event.detail.report).toContain('ACTION_REQUEST');
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});

it('selects an installed compatible engine without opening Settings or an agent', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/setup/recommendations')
      return Promise.resolve({
        models: [
          {
            repo_id: 'k2-fsa/OmniVoice',
            role: 'TTS',
            required: true,
            installed: true,
          },
        ],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: null,
          backends: [{ id: 'omnivoice', available: true, routing_status: 'native' }],
        },
      });
    return Promise.resolve({});
  });
  const listener = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/engines/select', {
      method: 'POST',
      body: JSON.stringify({ family: 'tts', backend_id: 'omnivoice' }),
    }),
  );
  expect(listener).not.toHaveBeenCalled();
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});

it('joins an existing install instead of submitting the model twice', async () => {
  mock.jobs.data.jobs = [
    {
      repo_id: 'k2-fsa/OmniVoice',
      state: 'downloading',
    },
  ];
  mock.api.mockResolvedValue({
    models: [
      {
        repo_id: 'k2-fsa/OmniVoice',
        role: 'TTS',
        required: true,
        installed: false,
      },
    ],
  });
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  // The shared progress block replaces the Fix button: one status line and
  // a Cancel, not a second "downloading" button.
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('modelInstall.queued'));
  expect(screen.getByRole('button', { name: 'common.cancel' })).toBeEnabled();
  expect(mock.api.mock.calls.filter(([path]) => path === '/models/install')).toHaveLength(0);
  expect(mock.toast).not.toHaveBeenCalled();
});

it('offers the same automatic recovery on compact synthesis surfaces', async () => {
  mockMissingRecommendedModel();
  renderCompactNotice();

  expect(screen.getByText('engines.none_ready_body')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'k2-fsa/OmniVoice' }),
    }),
  );
});

it('downloads the model chosen for an available engine instead of reporting success', async () => {
  const selected = 'mlx-community/Llama-OuteTTS-1.0-1B-4bit';
  mock.api.mockImplementation((path: string) => {
    if (path === '/setup/recommendations')
      return Promise.resolve({
        target: 'local',
        models: [{ repo_id: 'k2-fsa/OmniVoice', role: 'TTS', required: true, installed: true }],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'mlx-audio',
          active_model: selected,
          backends: [{ id: 'mlx-audio', available: true, routing_status: 'accelerated' }],
        },
      });
    if (path === '/models')
      return Promise.resolve({
        models: [{ repo_id: selected, installed: false, incomplete: false }],
      });
    return Promise.resolve({});
  });
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: selected }),
    }),
  );
  expect(mock.api.mock.calls.some(([path]) => path === '/engines/select')).toBe(false);
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('modelInstall.queued'));
});

it('downloads the selected model before a missing recommended model', async () => {
  const selected = 'mlx-community/Llama-OuteTTS-1.0-1B-4bit';
  mock.api.mockImplementation((path: string) => {
    if (path === '/setup/recommendations')
      return Promise.resolve({
        target: 'local',
        models: [{ repo_id: 'k2-fsa/OmniVoice', role: 'TTS', required: true, installed: false }],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'mlx-audio',
          active_model: selected,
          backends: [{ id: 'mlx-audio', available: true, routing_status: 'accelerated' }],
        },
      });
    if (path === '/models')
      return Promise.resolve({
        models: [{ repo_id: selected, installed: false, incomplete: false }],
      });
    return Promise.resolve({});
  });
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: selected }),
    }),
  );
  const installs = mock.api.mock.calls.filter(([path]) => path === '/models/install');
  expect(installs).toHaveLength(1);
});

it('switches engines instead of downloading the model of an unavailable engine', async () => {
  const selected = 'mlx-community/Llama-OuteTTS-1.0-1B-4bit';
  mock.api.mockImplementation((path: string) => {
    if (path === '/setup/recommendations')
      return Promise.resolve({
        target: 'local',
        models: [{ repo_id: 'k2-fsa/OmniVoice', role: 'TTS', required: true, installed: true }],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'mlx-audio',
          active_model: selected,
          backends: [
            { id: 'mlx-audio', available: false, routing_status: 'unavailable' },
            { id: 'omnivoice', available: true, routing_status: 'accelerated' },
          ],
        },
      });
    if (path === '/models')
      return Promise.resolve({
        models: [{ repo_id: selected, installed: false, incomplete: false }],
      });
    return Promise.resolve({});
  });
  renderNotice();

  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/engines/select', {
      method: 'POST',
      body: JSON.stringify({ family: 'tts', backend_id: 'omnivoice' }),
    }),
  );
  expect(mock.api.mock.calls.some(([path]) => path === '/models/install')).toBe(false);
});

it('shows a full disk with its next step instead of opening the repair agent', async () => {
  mockMissingRecommendedModel();
  const listener = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  const view = renderNotice();
  fireEvent.click(screen.getByRole('button', { name: 'engines.set_up' }));
  await waitFor(() => expect(mock.toast).toHaveBeenCalled());
  mock.jobs.data.jobs = [
    { repo_id: 'k2-fsa/OmniVoice', state: 'downloading', bytes_done: 1, total_bytes: 8 },
  ];
  view.rerender(
    <QueryClientProvider client={new QueryClient()}>
      <EngineNotice />
    </QueryClientProvider>,
  );
  await screen.findByText(/modelInstall.downloadingPct/);
  mock.jobs.data.jobs = [
    {
      repo_id: 'k2-fsa/OmniVoice',
      state: 'failed',
      failed_at: 1,
      docs_topic: 'DISK_SPACE_LOW',
      error: 'No space left on device',
    },
  ];
  view.rerender(
    <QueryClientProvider client={new QueryClient()}>
      <EngineNotice />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('modelInstall.failed')).toBeInTheDocument();
  expect(screen.getByText('modelInstall.nextDisk')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'common.retry' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'settings.storage' })).toBeInTheDocument();
  expect(listener).not.toHaveBeenCalled();
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});
