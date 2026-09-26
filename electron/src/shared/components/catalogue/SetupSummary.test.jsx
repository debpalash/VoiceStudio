import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';

vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let enginesData = null;
let enginesError = null;
const enginesRefetch = vi.fn();
let recoData = null;
const installMutate = vi.fn().mockResolvedValue({});
vi.mock('../../api/hooks', () => ({
  useEngines: () => ({
    data: enginesData,
    isLoading: false,
    isError: !!enginesError,
    error: enginesError,
    refetch: enginesRefetch,
  }),
  useRecommendations: () => ({ data: recoData }),
  useInstallModel: () => ({ mutateAsync: installMutate }),
}));
vi.mock('../../api/client', () => ({ apiJson: vi.fn() }));

import { apiJson } from '../../api/client';
import { toast } from 'react-hot-toast';
import { useAppStore } from '../../store';
import SetupSummary, { summarizeDictation, summarizeFamily } from './SetupSummary';

const t = i18n.t.bind(i18n);

const ENGINES = {
  tts: {
    active: 'indextts',
    backends: [
      {
        id: 'indextts',
        display_name: 'IndexTTS 2',
        available: true,
        effective_device: 'mps',
        routing_status: 'accelerated',
      },
    ],
  },
  asr: {
    active: 'faster-whisper',
    backends: [
      {
        id: 'faster-whisper',
        display_name: 'Faster-Whisper',
        available: true,
        effective_device: 'cpu',
        routing_status: 'cpu_fallback',
      },
    ],
  },
  llm: { active: 'off', backends: [] },
};

const DICTATION = {
  engine_available: true,
  models: [
    { id: 'sherpa-whisper-tiny', label: 'Whisper Tiny', installed: true, recommended: true },
    { id: 'sherpa-parakeet-tdt-v3', label: 'Parakeet TDT v3', installed: false },
  ],
};

function mount(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <SetupSummary onChange={vi.fn()} {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('SetupSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    enginesData = ENGINES;
    enginesError = null;
    recoData = null;
    apiJson.mockResolvedValue(DICTATION);
    useAppStore.setState({ dictationModelId: 'sherpa-whisper-tiny', dictationLoaded: true });
  });

  it('shows one line per capability: engine, device, and a plain status word', async () => {
    mount();
    const speech = screen.getByTestId('setup-row-speech');
    expect(within(speech).getByTestId('setup-name-speech')).toHaveTextContent('IndexTTS 2');
    expect(speech).toHaveTextContent('MPS');
    expect(within(speech).getByTestId('setup-status-speech')).toHaveTextContent(
      t('models.ready_badge'),
    );

    const asr = screen.getByTestId('setup-row-transcription');
    expect(asr).toHaveTextContent('Faster-Whisper');
    expect(within(asr).getByTestId('setup-status-transcription')).toHaveTextContent(
      t('catalogue.status_cpu'),
    );

    const llm = screen.getByTestId('setup-row-llm');
    expect(within(llm).getByTestId('setup-status-llm')).toHaveTextContent(
      t('catalogue.status_off'),
    );
    expect(within(llm).queryByTestId('setup-name-llm')).toBeNull();

    await waitFor(() =>
      expect(screen.getByTestId('setup-name-dictation')).toHaveTextContent('Whisper Tiny'),
    );
    expect(screen.getByTestId('setup-status-dictation')).toHaveTextContent(t('models.ready_badge'));
  });

  it('flags an unavailable active engine and an undownloaded dictation model', async () => {
    enginesData = {
      ...ENGINES,
      tts: { active: 'gone', backends: [{ id: 'gone', display_name: 'Gone', available: false }] },
    };
    useAppStore.setState({ dictationModelId: 'sherpa-parakeet-tdt-v3' });
    mount();
    expect(screen.getByTestId('setup-status-speech')).toHaveTextContent(
      t('catalogue.status_setup'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('setup-status-dictation')).toHaveTextContent(
        t('models.not_installed'),
      ),
    );
  });

  it('Change hands the row’s engine family to the page (dictation lives under ASR)', () => {
    const onChange = vi.fn();
    mount({ onChange });
    for (const [key, family] of [
      ['speech', 'tts'],
      ['transcription', 'asr'],
      ['dictation', 'asr'],
      ['llm', 'llm'],
    ]) {
      fireEvent.click(screen.getByTestId(`setup-change-${key}`));
      expect(onChange).toHaveBeenLastCalledWith(family);
    }
  });

  it('summarises the recommended preset in one line and installs the rest in one click', async () => {
    recoData = {
      device: { label: 'Apple M2 Pro' },
      all_installed: false,
      download_gb_remaining: 2.4,
      models: [
        { repo_id: 'a/have', installed: true, size_gb: 1 },
        { repo_id: 'b/need', installed: false, size_gb: 2.4 },
      ],
    };
    mount();
    const line = screen.getByTestId('setup-reco');
    expect(line).toHaveTextContent(
      t('catalogue.reco_line', { installed: 1, total: 2, device: 'Apple M2 Pro', remaining: 2.4 }),
    );
    fireEvent.click(screen.getByTestId('setup-install-rest'));
    await waitFor(() => expect(installMutate).toHaveBeenCalledWith('b/need'));
    expect(installMutate).toHaveBeenCalledTimes(1);
  });

  it('keeps Install the rest disabled until every request settles, then reports the failures', async () => {
    recoData = {
      device: { label: 'M2' },
      all_installed: false,
      download_gb_remaining: 3,
      models: [
        { repo_id: 'a/fails', installed: false, size_gb: 1 },
        { repo_id: 'b/slow', installed: false, size_gb: 2 },
      ],
    };
    let resolveSlow;
    installMutate.mockImplementation((repo) =>
      repo === 'a/fails'
        ? Promise.reject(new Error('gated'))
        : new Promise((resolve) => {
            resolveSlow = resolve;
          }),
    );
    mount();
    const btn = screen.getByTestId('setup-install-rest');
    fireEvent.click(btn);
    // The first request rejected immediately; the second is still pending, so
    // the action must stay disabled (a second click would re-request b/slow).
    await waitFor(() => expect(installMutate).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    expect(btn).toBeDisabled();
    resolveSlow({});
    await waitFor(() => expect(screen.getByTestId('setup-install-rest')).not.toBeDisabled());
    expect(toast.success).toHaveBeenCalledWith(t('models.started_downloading', { count: 1 }));
    expect(toast.error).toHaveBeenCalledWith(
      t('models.install_failed', { message: 'a/fails: gated' }),
    );
  });

  it('shows a failed engines fetch as an error with Retry, never as a configuration state', async () => {
    enginesData = null;
    enginesError = new Error('backend down');
    apiJson.mockRejectedValue(new Error('dictation down'));
    mount();
    expect(screen.getByTestId('setup-error-speech')).toHaveTextContent(
      t('engines.loadFailed', { message: 'backend down' }),
    );
    expect(screen.queryByTestId('setup-status-llm')).toBeNull();
    fireEvent.click(screen.getByTestId('setup-retry-speech'));
    expect(enginesRefetch).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId('setup-error-dictation')).toBeInTheDocument());
    expect(screen.queryByTestId('setup-status-dictation')).toBeNull();
  });

  it('drops the install button once the preset is complete', () => {
    recoData = {
      device: { label: 'Apple M2 Pro' },
      all_installed: true,
      total_gb: 3.4,
      models: [{ repo_id: 'a/have', installed: true, size_gb: 1 }],
    };
    mount();
    expect(screen.getByTestId('setup-reco')).toHaveTextContent(
      t('models.reco_installed_for', { device: 'Apple M2 Pro' }),
    );
    expect(screen.queryByTestId('setup-install-rest')).toBeNull();
  });
});

describe('summarizeFamily / summarizeDictation', () => {
  it('maps routing and availability to the four status words', () => {
    expect(summarizeFamily('tts', ENGINES.tts)).toEqual({
      name: 'IndexTTS 2',
      device: 'MPS',
      status: 'ready',
    });
    expect(summarizeFamily('asr', ENGINES.asr).status).toBe('cpu');
    // Installed but with no usable device path: /engines says available
    // with routing 'unavailable' (select would be refused) — not Ready.
    expect(
      summarizeFamily('tts', {
        active: 'gpu-only',
        backends: [
          {
            id: 'gpu-only',
            display_name: 'GPU only',
            available: true,
            effective_device: 'cuda',
            routing_status: 'unavailable',
          },
        ],
      }),
    ).toEqual({ name: 'GPU only', device: null, status: 'setup' });
    expect(summarizeFamily('llm', ENGINES.llm)).toEqual({
      name: null,
      device: null,
      status: 'off',
    });
    expect(summarizeFamily('tts', { active: 'x', backends: [] })).toEqual({
      name: 'x',
      device: null,
      status: 'setup',
    });
    expect(
      summarizeFamily('asr', {
        active: 'openai-compat',
        backends: [{ id: 'openai-compat', available: true, routing_status: 'n/a' }],
      }).device,
    ).toBe('remote');
  });

  it('falls back to the recommended dictation model and reports engine trouble', () => {
    expect(summarizeDictation(DICTATION, 'unknown').name).toBe('Whisper Tiny');
    expect(summarizeDictation({ ...DICTATION, engine_available: false }, null).status).toBe(
      'setup',
    );
    expect(summarizeDictation(null, null).status).toBe('setup');
  });
});
