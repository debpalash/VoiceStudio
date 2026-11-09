import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { createInstance } from 'i18next';
import deLocale from '@/i18n/locales/de.json';
import enLocale from '@/i18n/locales/en.json';
import unavailableAsr from './fixtures/asr-unavailable.json';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  t: vi.fn<(key: string, options?: Record<string, unknown>) => string>((key) => key),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('sonner', () => ({ toast: mock.toast }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: mock.t }),
}));

import { EngineFamilyList } from './engine-family-list';
import { ModelStoreShownContext } from './model-store-context';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mock.t.mockImplementation((key) => key);
});

const base = { role: 'TTS', size_gb: 1, installed: false, supported: true };

function serve() {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models')
      return Promise.resolve({
        models: [
          {
            ...base,
            repo_id: 'k2-fsa/OmniVoice',
            label: 'OmniVoice weights',
            engines: ['omnivoice'],
            curated: true,
            size_gb: 2.4,
          },
          { ...base, repo_id: 'mlx/kokoro', label: 'Kokoro', engines: ['mlx-audio'] },
          {
            ...base,
            repo_id: 'mlx/csm',
            label: 'CSM',
            engines: ['mlx-audio'],
            installed: true,
          },
          { ...base, repo_id: 'owner/loose', label: 'Loose model' },
        ],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'omnivoice',
          active_model: 'k2-fsa/OmniVoice',
          backends: [
            {
              id: 'voxcpm',
              display_name: 'VoxCPM',
              available: false,
              reason: 'Needs CUDA',
              setup_snippet: 'uv sync --extra voxcpm',
            },
            {
              id: 'mlx-audio',
              display_name: 'MLX-Audio',
              available: true,
              reason: null,
              effective_device: 'mps',
              curated_models: [
                { key: 'kokoro', label: 'Kokoro', repo_id: 'mlx/kokoro' },
                { key: 'csm', label: 'CSM', repo_id: 'mlx/csm' },
              ],
            },
            {
              id: 'omnivoice',
              display_name: 'OmniVoice',
              available: true,
              reason: null,
              effective_device: 'cpu',
            },
          ],
        },
      });
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    if (path === '/engines/select') return Promise.resolve({ active: 'mlx-audio' });
    return Promise.resolve({});
  });
}

function renderList(family: 'tts' | 'asr' = 'tts') {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EngineFamilyList family={family} />
    </QueryClientProvider>,
  );
}

it('lists each engine once with install and select on the same row, active first', async () => {
  serve();
  renderList();
  const omnivoice = await screen.findByRole('group', { name: 'OmniVoice' });
  const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent);
  expect(headings).toEqual(['OmniVoice', 'MLX-Audio', 'VoxCPM', 'Loose model']);
  // One row: the engine's weights download and its selection state.
  await waitFor(() =>
    expect(
      within(omnivoice).getByRole('button', { name: 'modelMaintenance.download' }),
    ).toBeEnabled(),
  );
  // Selected once, as a badge: no second, disabled "Selected" button.
  expect(within(omnivoice).queryByRole('button', { name: 'modelSettings.selected' })).toBeNull();
  expect(screen.getAllByText('modelSettings.selected')).toHaveLength(1);
  fireEvent.click(within(omnivoice).getByRole('button', { name: 'modelMaintenance.download' }));
  expect(mock.api).toHaveBeenCalledWith(
    '/models/install',
    expect.objectContaining({ body: JSON.stringify({ repo_id: 'k2-fsa/OmniVoice' }) }),
  );
  // The unavailable engine keeps its reason on the row.
  expect(screen.getByText('Needs CUDA')).toBeInTheDocument();
  expect(screen.queryByText('Kokoro')).not.toBeInTheDocument();
});

it('expands a multi-model engine to install and select one of its models', async () => {
  serve();
  renderList();
  const toggle = await screen.findByRole('button', { name: 'MLX-Audio' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(await screen.findByText('modelSettings.modelsInstalled')).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const csm = await screen.findByRole('group', { name: 'CSM' });
  fireEvent.click(within(csm).getByRole('button', { name: 'modelSettings.select' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/engines/select',
      expect.objectContaining({
        body: JSON.stringify({ family: 'tts', backend_id: 'mlx-audio', model_id: 'csm' }),
      }),
    ),
  );
  expect(
    within(screen.getByRole('group', { name: 'Kokoro' })).getByRole('button', {
      name: 'modelMaintenance.download',
    }),
  ).toBeInTheDocument();
});

it('searches hosted models and opens the engine that holds the match', async () => {
  serve();
  renderList();
  await screen.findByRole('heading', { name: 'OmniVoice' });
  fireEvent.change(screen.getByRole('searchbox', { name: 'preferences.search' }), {
    target: { value: 'csm' },
  });
  expect(screen.queryByRole('heading', { name: 'OmniVoice' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'MLX-Audio' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  expect(screen.getByRole('group', { name: 'CSM' })).toBeInTheDocument();
});

it('leaves install-only model rows to the first-run store shown above it', async () => {
  serve();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelStoreShownContext.Provider value>
        <EngineFamilyList family="tts" />
      </ModelStoreShownContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByRole('group', { name: 'OmniVoice' });
  const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent);
  expect(headings).toEqual(['OmniVoice', 'MLX-Audio', 'VoxCPM']);
});

it('opens the active multi-model engine once its models arrive after the engine list', async () => {
  let release!: () => void;
  const models = new Promise<void>((resolve) => (release = resolve));
  serve();
  const fallback = mock.api.getMockImplementation()!;
  mock.api.mockImplementation(async (path: string, ...rest: unknown[]) => {
    if (path === '/engines') {
      const engines = (await fallback(path, ...rest)) as { tts: Record<string, unknown> };
      return { tts: { ...engines.tts, active: 'mlx-audio', active_model: 'csm' } };
    }
    if (path === '/models') await models;
    return fallback(path, ...rest);
  });
  renderList();
  await screen.findByRole('heading', { name: 'MLX-Audio' });
  release();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'MLX-Audio' })).toHaveAttribute(
      'aria-expanded',
      'true',
    ),
  );
});

it('reports a failed model catalogue with a retry instead of hiding downloads', async () => {
  serve();
  const fallback = mock.api.getMockImplementation()!;
  mock.api.mockImplementation((path: string, ...rest: unknown[]) =>
    path === '/models' ? Promise.reject(new Error('catalogue down')) : fallback(path, ...rest),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <EngineFamilyList family="tts" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Error: catalogue down')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'backend.retry' })).toBeInTheDocument();
});

// Mirrors the owned guidance returned by ASR list_backends(), not a raw probe error.
const nemoInstallHint =
  "No safe install path in this app yet — nemo_toolkit's ASR extras pin " +
  "transformers>=4.57,<4.58, which conflicts with VoiceStudio's own " +
  'transformers>=5.3 requirement and WILL break the backend ' +
  '(ImportError on startup) if installed into this shared venv. Do NOT ' +
  'install nemo_toolkit here. If you want to try Parakeet TDT, set it ' +
  'up in a separate/dedicated Python environment — not the one ' +
  'VoiceStudio manages; in-app isolation for this engine is tracked ' +
  'separately.';

it.each([
  ['nemo-parakeet', 'Parakeet TDT (NVIDIA NeMo)', nemoInstallHint],
  [
    'moonshine',
    'Moonshine',
    'uv pip install moonshine-onnx  (or moonshine-voice; edge/CPU-optimized ASR)',
  ],
  ['funasr', 'FunASR', 'pip install funasr  (SenseVoiceSmall + FSMN-VAD; CUDA or CPU)'],
])(
  'shows owned setup guidance for unavailable ASR %s without inventing an installer',
  async (id, name, hint) => {
    const english = createInstance();
    await english.init({
      lng: 'en',
      resources: { en: { translation: enLocale } },
      interpolation: { escapeValue: false },
    });
    mock.t.mockImplementation((key, options) =>
      key.startsWith('engines.asrSetup') ? english.t(key, options) : key,
    );
    serve();
    const fallback = mock.api.getMockImplementation()!;
    mock.api.mockImplementation((path: string, ...rest: unknown[]) => {
      if (path === '/engines')
        return Promise.resolve({
          asr: {
            active: 'faster-whisper',
            backends: [
              {
                id,
                display_name: name,
                available: false,
                reason:
                  "This engine's package isn't installed yet. Install it from Model Catalogue.",
                install_hint: hint,
              },
            ],
          },
        });
      if (path === '/models')
        return Promise.resolve({
          models: ['v3', 'v2'].map((version) => ({
            ...base,
            role: 'ASR',
            repo_id: `nvidia/parakeet-tdt-0.6b-${version}`,
            label: `Parakeet ${version}`,
            engines: [id],
          })),
        });
      return fallback(path, ...rest);
    });
    renderList('asr');
    fireEvent.click(await screen.findByRole('button', { name }));
    if (id === 'nemo-parakeet') {
      expect(screen.getByText(/NeMo cannot safely be installed/)).toBeInTheDocument();
      expect(screen.getByText(/Do not install nemo_toolkit here/)).toBeInTheDocument();
    } else {
      const command = id === 'moonshine' ? 'uv pip install moonshine-onnx' : 'pip install funasr';
      expect(
        screen.getByText(`Run ${command} in the backend's Python environment.`),
      ).toBeInTheDocument();
    }
    expect(screen.getAllByRole('button', { name: 'modelMaintenance.download' })).toHaveLength(2);
    expect(
      screen.queryByRole('button', { name: 'modelMaintenance.install' }),
    ).not.toBeInTheDocument();
  },
);

it.each([
  { setup_snippet: 'uv sync --extra whisperx' },
  { docs_url: 'https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/whisperx.md' },
  { local_install_required: true },
])(
  'preserves existing setup guidance instead of replacing it with an install hint: %o',
  async (guidance) => {
    serve();
    const fallback = mock.api.getMockImplementation()!;
    mock.api.mockImplementation((path: string, ...rest: unknown[]) => {
      if (path === '/engines')
        return Promise.resolve({
          asr: {
            active: 'faster-whisper',
            backends: [
              {
                id: 'whisperx',
                display_name: 'WhisperX',
                available: false,
                reason: 'Needs setup',
                install_hint: 'Do not replace existing guidance',
                ...guidance,
              },
            ],
          },
        });
      return fallback(path, ...rest);
    });
    renderList('asr');
    const toggle = await screen.findByRole('button', { name: 'WhisperX' });
    if (toggle.getAttribute('aria-expanded') === 'false') fireEvent.click(toggle);
    expect(screen.queryByText('Do not replace existing guidance')).not.toBeInTheDocument();
    if (guidance.setup_snippet)
      expect(screen.getByText(guidance.setup_snippet)).toBeInTheDocument();
    if (guidance.docs_url)
      expect(screen.getByRole('link', { name: 'common.learn_more' })).toHaveAttribute(
        'href',
        guidance.docs_url,
      );
    if ('local_install_required' in guidance)
      expect(screen.getAllByText('engines.localInstallRequired').length).toBeGreaterThan(0);
  },
);

it('localizes the NeMo safety warning for a German interface', async () => {
  const i18n = createInstance();
  await i18n.init({
    lng: 'de',
    resources: { de: { translation: deLocale } },
    interpolation: { escapeValue: false },
  });
  mock.t.mockImplementation((key, options) => i18n.t(key, options));
  serve();
  const fallback = mock.api.getMockImplementation()!;
  mock.api.mockImplementation((path: string, ...rest: unknown[]) => {
    if (path === '/engines')
      return Promise.resolve({
        asr: {
          active: 'faster-whisper',
          backends: [
            {
              id: 'nemo-parakeet',
              display_name: 'Parakeet TDT',
              available: false,
              reason:
                "This engine's package isn't installed yet. Install it from Settings → Models.",
              install_hint: nemoInstallHint,
            },
          ],
        },
      });
    return fallback(path, ...rest);
  });
  renderList('asr');
  const toggle = await screen.findByRole('button', { name: 'Parakeet TDT' });
  if (toggle.getAttribute('aria-expanded') === 'false') fireEvent.click(toggle);
  expect(screen.getByText(/NeMo lässt sich.*nicht sicher installieren/)).toBeInTheDocument();
  expect(screen.getByText(deLocale.modelSettings.unavailable)).toHaveAttribute(
    'title',
    deLocale.engines.setupStatusNotInstalled,
  );
  expect(screen.queryByText(nemoInstallHint)).not.toBeInTheDocument();
});

it.each(
  unavailableAsr.flatMap((entry) => ['en', 'de'].map((language) => ({ ...entry, language }))),
)(
  'keeps setup guidance consistent with $engine.id: $diagnostic ($language)',
  async ({ engine, key, language }) => {
    const i18n = createInstance();
    await i18n.init({
      lng: language,
      resources: { en: { translation: enLocale }, de: { translation: deLocale } },
      interpolation: { escapeValue: false },
    });
    mock.t.mockImplementation((key, options) =>
      key.startsWith('engines.') ? i18n.t(key, options) : key,
    );
    serve();
    const fallback = mock.api.getMockImplementation()!;
    mock.api.mockImplementation((path: string, ...rest: unknown[]) =>
      path === '/engines'
        ? Promise.resolve({ asr: { active: 'faster-whisper', backends: [engine] } })
        : fallback(path, ...rest),
    );
    renderList('asr');
    const toggle = await screen.findByRole('button', { name: engine.display_name });
    if (toggle.getAttribute('aria-expanded') === 'false') fireEvent.click(toggle);
    const expected = i18n.t(`engines.${key}`, {
      command: engine.id === 'moonshine' ? 'uv pip install moonshine-onnx' : 'pip install funasr',
      nemoRequirement: 'transformers>=4.57,<4.58',
      appRequirement: 'transformers>=5.3',
    });
    expect(screen.getAllByText(expected).length).toBeGreaterThan(0);
    if (key === 'asrSetupRepairBundled')
      expect(screen.queryByText(i18n.t('engines.asrSetupRemote'))).not.toBeInTheDocument();
    if (key === 'setupStatusFileMissing') {
      expect(
        screen.queryByText(
          i18n.t('engines.asrSetupPackageInstall', { command: 'pip install funasr' }),
        ),
      ).not.toBeInTheDocument();
    }
    if (key === 'setupStatusPlatform' || key === 'setupStatusNoMps') {
      expect(screen.getByText('modelSettings.unavailable')).toHaveAttribute('title', expected);
      expect(
        screen.queryByText(
          i18n.t('engines.asrSetupApplePackageInstall', { command: 'pip install mlx-whisper' }),
        ),
      ).not.toBeInTheDocument();
    }
  },
);
