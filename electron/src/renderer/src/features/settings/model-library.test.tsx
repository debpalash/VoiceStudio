import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({
  api: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('sonner', () => ({ toast: mock.toast }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
import { ModelLibrary, PerformanceModelPacks, SystemRecommendations } from './model-library';
import { modelFamilies } from './model-family';
import { performancePackRepos } from './performance-model-packs';
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it('prioritizes required and curated models across families, with optional downloads folded and explicit', async () => {
  const base = { size_gb: 1, installed: false, supported: true };
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/models'
        ? {
            models: [
              { ...base, repo_id: 'optional', label: 'Optional', role: 'ASR' },
              {
                ...base,
                repo_id: 'recommended',
                label: 'Recommended',
                role: 'ASR',
                curated: true,
              },
              {
                ...base,
                repo_id: 'required',
                label: 'Required',
                role: 'TTS',
                required: true,
              },
              {
                ...base,
                repo_id: 'unsupported',
                label: 'Unsupported',
                role: 'TTS',
                required: true,
                supported: false,
              },
            ],
          }
        : { jobs: [] },
    ),
  );
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <ModelLibrary setup />
    </QueryClientProvider>,
  );
  const required = await screen.findByText('Required');
  const recommended = screen.getByText('Recommended');
  expect(
    required.compareDocumentPosition(recommended) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByText('Optional')).not.toBeVisible();
  expect(screen.queryByText('Unsupported')).not.toBeInTheDocument();
  expect(mock.api.mock.calls.every(([path]) => path !== '/models/install')).toBe(true);
  fireEvent.click(
    within(screen.getByRole('group', { name: 'Required' })).getByRole('button', {
      name: 'modelMaintenance.download',
    }),
  );
  expect(mock.api).toHaveBeenCalledWith(
    '/models/install',
    expect.objectContaining({
      body: JSON.stringify({ repo_id: 'required', target: 'local' }),
    }),
  );
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['performance-profile'],
    }),
  );
});

it('allows using an installed performance pack below the download reserve', async () => {
  const profile = {
    global: 'balanced',
    overrides: {},
    effective: {},
    families: [],
    implemented_families: [],
    targets: { tts: {} },
    selections: { tts: { engine: 'other', model: null } },
  };
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        disk_free_gb: 2.5,
        models: performancePackRepos.balanced.map((repo_id) => ({
          repo_id,
          label: repo_id,
          role: repo_id.includes('whisper') || repo_id.includes('sherpa') ? 'ASR' : 'TTS',
          size_gb: 1,
          installed: true,
          supported: true,
        })),
      });
    }
    if (path === '/api/settings/performance-profile') return Promise.resolve(profile);
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/setup/recommendations') {
      return Promise.resolve({
        device: { label: 'Windows x64' },
        models: [],
        download_gb_remaining: 0,
        total_gb: 0,
        all_installed: true,
      });
    }
    if (path.startsWith('/batch/jobs')) return Promise.resolve([]);
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <PerformanceModelPacks />
    </QueryClientProvider>,
  );

  const usePack = await screen.findByRole('button', { name: 'models.pack_use' });
  expect(usePack).toBeEnabled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.click(usePack);

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/api/settings/performance-profile',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ tier: 'balanced', family: null }),
      }),
    ),
  );
  expect(mock.api.mock.calls.every(([path]) => path !== '/models/install')).toBe(true);
});

it('keeps the disk reserve guard when a performance pack still needs downloads', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        disk_free_gb: 2.5,
        models: performancePackRepos.balanced.map((repo_id, index) => ({
          repo_id,
          label: repo_id,
          role: repo_id.includes('whisper') || repo_id.includes('sherpa') ? 'ASR' : 'TTS',
          size_gb: index === 0 ? 1 : 0,
          installed: index !== 0,
          supported: true,
        })),
      });
    }
    if (path === '/api/settings/performance-profile')
      return Promise.resolve({ global: 'balanced', targets: {}, selections: {} });
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/setup/recommendations') {
      return Promise.resolve({
        device: { label: 'Windows x64' },
        models: [],
        download_gb_remaining: 0,
        total_gb: 0,
        all_installed: true,
      });
    }
    if (path.startsWith('/batch/jobs')) return Promise.resolve([]);
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <PerformanceModelPacks />
    </QueryClientProvider>,
  );

  const installPack = await screen.findByRole('button', { name: 'models.pack_install' });
  expect(installPack).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('models.pack_low_disk');
  fireEvent.click(installPack);
  expect(mock.api.mock.calls.every(([path]) => path !== '/models/install')).toBe(true);
});

it('unloads a resident model before deleting its slash-separated repository', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'owner/model name',
            label: 'Installed model',
            role: 'TTS',
            size_gb: 2,
            installed: true,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') {
      return Promise.resolve({
        models: [
          {
            id: 'sidecar:voice',
            checkpoint: 'owner/model name',
            unloadable: true,
          },
        ],
        count: 1,
      });
    }
    if (path === '/model/unload/sidecar%3Avoice') return Promise.resolve({ success: true });
    if (path === '/models/owner/model%20name') {
      return Promise.resolve({
        deleted: true,
        repo_id: 'owner/model name',
        freed_bytes: 10,
      });
    }
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="tts" />
    </QueryClientProvider>,
  );

  await screen.findByText('modelMaintenance.inMemory');
  fireEvent.click(screen.getByRole('button', { name: 'modelMaintenance.delete' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('modelMaintenance.deleteConfirm');
  fireEvent.click(screen.getByRole('button', { name: 'modelMaintenance.delete' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/models/owner/model%20name', {
      method: 'DELETE',
    }),
  );
  const unloadCall = mock.api.mock.calls.findIndex(
    ([path]) => path === '/model/unload/sidecar%3Avoice',
  );
  const deleteCall = mock.api.mock.calls.findIndex(
    ([path]) => path === '/models/owner/model%20name',
  );
  expect(unloadCall).toBeGreaterThan(-1);
  expect(deleteCall).toBeGreaterThan(unloadCall);
});

it('confirms reinstall, removes the old copy, then starts a fresh install', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'owner/model',
            label: 'Installed model',
            role: 'ASR',
            size_gb: 2,
            installed: true,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="asr" />
    </QueryClientProvider>,
  );

  await screen.findByText('Installed model');
  fireEvent.click(screen.getByRole('button', { name: 'modelMaintenance.reinstall' }));
  fireEvent.click(screen.getByRole('button', { name: 'modelMaintenance.reinstall' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/models/install',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ repo_id: 'owner/model', target: 'local' }),
      }),
    ),
  );
  const deleteCall = mock.api.mock.calls.findIndex(([path]) => path === '/models/owner/model');
  const installCall = mock.api.mock.calls.findIndex(([path]) => path === '/models/install');
  expect(deleteCall).toBeGreaterThan(-1);
  expect(installCall).toBeGreaterThan(deleteCall);
});

it('surfaces the backend routing verdict when selecting an ASR model from the catalogue', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'Systran/faster-whisper-large-v3',
            label: 'Whisper large-v3',
            role: 'ASR',
            size_gb: 2.9,
            installed: true,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    if (path === '/engines') {
      return Promise.resolve({
        asr: {
          active: 'faster-whisper',
          active_model: 'another/model',
          backends: [{ id: 'faster-whisper', available: true }],
        },
      });
    }
    if (path === '/engines/select') {
      return Promise.resolve({
        active: 'faster-whisper',
        routing_status: 'cpu_fallback',
        routing_reason: 'CUDA runtime is unavailable',
      });
    }
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="asr" />
    </QueryClientProvider>,
  );

  await screen.findByText('Whisper large-v3');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.select' }));

  await waitFor(() => expect(mock.toast.warning).toHaveBeenCalledWith('engines.selectCpuFallback'));
  expect(mock.toast.success).not.toHaveBeenCalled();
});

it('surfaces an interrupted cache as repairable and removable', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'owner/partial',
            label: 'Interrupted model',
            role: 'TTS',
            size_gb: 2,
            size_on_disk_bytes: 512 * 1024 * 1024,
            incomplete: true,
            installed: false,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="tts" />
    </QueryClientProvider>,
  );

  await screen.findByText('modelMaintenance.incomplete');
  expect(screen.getByText('512.0 MB')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'modelMaintenance.repair' }));
  await waitFor(() => expect(mock.api).toHaveBeenCalledWith('/models/install', expect.anything()));
  expect(screen.getByRole('button', { name: 'modelMaintenance.delete' })).toBeInTheDocument();
});

it('keeps speaker diarisation models discoverable after setup', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'pyannote/speaker-diarization-3.1',
            label: 'Speaker diarisation',
            role: 'Diarisation',
            size_gb: 3.1,
            installed: false,
            supported: true,
          },
          {
            repo_id: 'owner/tts',
            label: 'Unrelated TTS',
            role: 'TTS',
            size_gb: 1,
            installed: false,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  expect(modelFamilies).toContain('diarisation');
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family={'diarisation' as never} />
    </QueryClientProvider>,
  );

  await screen.findByText('Speaker diarisation');
  expect(screen.queryByText('Unrelated TTS')).not.toBeInTheDocument();
});

it('searches model metadata and keeps incompatible choices folded', async () => {
  const base = { role: 'TTS', size_gb: 1, installed: false };
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            ...base,
            repo_id: 'owner/compatible',
            label: 'Compatible model',
            note: 'Expressive narration',
            supported: true,
          },
          {
            ...base,
            repo_id: 'owner/apple-only',
            label: 'Apple-only model',
            note: 'MLX voices',
            supported: false,
          },
        ],
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="tts" />
    </QueryClientProvider>,
  );

  await screen.findByText('Compatible model');
  expect(screen.getByText('Apple-only model')).not.toBeVisible();
  fireEvent.change(screen.getByRole('searchbox', { name: 'preferences.search' }), {
    target: { value: 'MLX' },
  });
  expect(screen.queryByText('Compatible model')).not.toBeInTheDocument();
  expect(screen.getByText('Apple-only model')).not.toBeVisible();
  fireEvent.click(screen.getByText('modelSettings.unavailable (1)'));
  expect(screen.getByText('Apple-only model')).toBeVisible();
});

it('shows device recommendations and live aggregate download telemetry', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        models: [
          {
            repo_id: 'owner/recommended',
            label: 'Fast local model',
            role: 'TTS',
            size_gb: 1,
            installed: false,
            supported: true,
            curated: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') {
      return Promise.resolve({
        jobs: [
          {
            repo_id: 'owner/recommended',
            state: 'downloading',
            bytes_done: 512 * 1024 * 1024,
            total_bytes: 1024 * 1024 * 1024,
            rate: 8 * 1024 * 1024,
            eta_seconds: 64,
            files_done: 2,
            files_total: 4,
          },
        ],
      });
    }
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="tts" />
    </QueryClientProvider>,
  );

  await screen.findByText('Fast local model');
  expect(screen.getByText('firstrun.chip_recommended')).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByText('512.0 MB / 1.00 GB')).toBeInTheDocument();
  expect(screen.getByText('8.0 MB/s')).toBeInTheDocument();
  expect(screen.getByText('~2m')).toBeInTheDocument();
});

it('shows progress for the selected remote target instead of a same-repo local job', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({
        target: 'gpu2',
        models: [
          {
            repo_id: 'owner/shared',
            label: 'Remote model',
            role: 'TTS',
            size_gb: 1,
            installed: false,
            supported: true,
          },
        ],
      });
    }
    if (path === '/models/install/status') {
      return Promise.resolve({
        jobs: [
          {
            repo_id: 'owner/shared',
            target: 'local',
            state: 'downloading',
            bytes_done: 90 * 1024 ** 2,
            total_bytes: 100 * 1024 ** 2,
          },
          {
            repo_id: 'owner/shared',
            target: 'gpu2',
            state: 'downloading',
            bytes_done: 25 * 1024 ** 2,
            total_bytes: 100 * 1024 ** 2,
          },
        ],
      });
    }
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelLibrary family="tts" />
    </QueryClientProvider>,
  );

  await screen.findByText('Remote model');
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
  expect(screen.getByText('25.0 MB / 100.0 MB')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/models/install/cancel',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ repo_id: 'owner/shared', target: 'gpu2' }),
      }),
    ),
  );
});

it('shows the device bundle, warns before exceeding disk space, and starts selected installs', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') {
      return Promise.resolve({ models: [], disk_free_gb: 2.5 });
    }
    if (path === '/setup/recommendations') {
      return Promise.resolve({
        device: { label: 'Windows x64 + CUDA' },
        models: [
          {
            repo_id: 'owner/required',
            label: 'Required voice',
            role: 'TTS',
            size_gb: 2.4,
            required: true,
            installed: false,
          },
          {
            repo_id: 'owner/optional',
            label: 'Fast transcription',
            role: 'ASR',
            size_gb: 1.6,
            required: false,
            installed: false,
          },
        ],
        download_gb_remaining: 4,
        total_gb: 4,
        all_installed: false,
      });
    }
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <SystemRecommendations />
    </QueryClientProvider>,
  );

  await screen.findByText('Required voice');
  expect(screen.getByText('models.reco_disk_free')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('models.reco_low_disk');
  fireEvent.click(screen.getAllByRole('button', { name: 'models.reco_install_one' })[0]);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/models/install',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ repo_id: 'owner/required', target: 'local' }),
      }),
    ),
  );
  expect(
    mock.api.mock.calls.some(
      ([path, options]) =>
        path === '/models/install' && String(options?.body).includes('owner/optional'),
    ),
  ).toBe(false);
});

it('labels installed recommendations and distinguishes the active model', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models') return Promise.resolve({ models: [], disk_free_gb: 20 });
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/engines') {
      return Promise.resolve({
        tts: {
          active: 'voice',
          active_model: 'owner/active',
          backends: [{ id: 'voice', available: true }],
        },
        asr: { active: 'asr', active_model: null, backends: [] },
      });
    }
    if (path === '/setup/recommendations') {
      return Promise.resolve({
        device: { label: 'Test device' },
        models: [
          {
            repo_id: 'owner/active',
            label: 'Active voice',
            role: 'TTS',
            size_gb: 1,
            required: true,
            installed: true,
          },
          {
            repo_id: 'owner/installed',
            label: 'Installed voice',
            role: 'TTS',
            size_gb: 1,
            required: false,
            installed: true,
          },
          {
            repo_id: 'owner/missing',
            label: 'Optional voice',
            role: 'TTS',
            size_gb: 1,
            required: false,
            installed: false,
          },
        ],
        download_gb_remaining: 1,
        total_gb: 3,
        all_installed: false,
      });
    }
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <SystemRecommendations />
    </QueryClientProvider>,
  );

  await screen.findByText('Active voice');
  expect(screen.getByText('modelSettings.selected')).toBeInTheDocument();
  expect(screen.getByText('modelMaintenance.installed')).toBeInTheDocument();
  expect(screen.getByText('Optional voice')).toBeInTheDocument();
});

it.each([
  // Fits the raw download but not the headroom the backend keeps (#2597).
  { free: 8, headroom: undefined, warns: true },
  { free: 8, headroom: 2, warns: false },
  { free: 20, headroom: 10, warns: false },
])(
  'warns about the full bundle with the backend disk rule (free $free GB, headroom $headroom)',
  async ({ free, headroom, warns }) => {
    mock.api.mockImplementation((path: string) => {
      if (path === '/models') {
        return Promise.resolve({ models: [], disk_free_gb: free, disk_headroom_gb: headroom });
      }
      if (path === '/setup/recommendations') {
        return Promise.resolve({
          device: { label: 'Test device' },
          models: [
            {
              repo_id: 'owner/required',
              label: 'Required voice',
              role: 'TTS',
              size_gb: 4,
              required: true,
              installed: false,
            },
          ],
          download_gb_remaining: 4,
          total_gb: 4,
          all_installed: false,
        });
      }
      if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
      return Promise.resolve({});
    });

    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemRecommendations />
      </QueryClientProvider>,
    );

    await screen.findByText('models.reco_disk_free');
    expect(screen.queryByRole('alert') !== null).toBe(warns);
  },
);
