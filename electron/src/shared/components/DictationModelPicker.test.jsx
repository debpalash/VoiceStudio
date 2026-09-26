import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { listEngines, selectEngine, listLoadedModels, apiJson, apiPost, installMutate } = vi.hoisted(
  () => ({
    listEngines: vi.fn(),
    selectEngine: vi.fn(),
    listLoadedModels: vi.fn(),
    apiJson: vi.fn(),
    apiPost: vi.fn(),
    installMutate: vi.fn(),
  }),
);
vi.mock('../api/engines', () => ({ listEngines, selectEngine }));
vi.mock('../api/system', () => ({ listLoadedModels }));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal()),
  apiJson: (...a) => apiJson(...a),
  apiPost: (...a) => apiPost(...a),
}));
vi.mock('../api/hooks', async (importOriginal) => ({
  ...(await importOriginal()),
  useInstallModel: () => ({ mutateAsync: installMutate }),
}));

import EngineQuickSwitch from './EngineQuickSwitch';
import { useAppStore } from '../store';

const ASR_INVENTORY = {
  tts: {
    active: 'omnivoice',
    backends: [{ id: 'omnivoice', display_name: 'OmniVoice', available: true }],
  },
  asr: {
    active: 'whisperx',
    backends: [
      { id: 'whisperx', display_name: 'WhisperX (faster-whisper + wav2vec2)', available: true },
      {
        id: 'sherpa-onnx-asr',
        display_name: 'Sherpa-ONNX dictation (live, CPU — streaming + offline)',
        available: true,
      },
      { id: 'nemo-parakeet', display_name: 'Parakeet TDT (NVIDIA NeMo)', available: false },
    ],
  },
  llm: { active: 'off', backends: [] },
};

const MODELS = {
  models: [
    {
      id: 'sherpa-whisper-tiny',
      repo_id: 'csukuangfj/sherpa-onnx-whisper-tiny',
      label: 'Whisper Tiny',
      tag: 'offline',
      recommended: true,
      size_gb: 0.104,
      languages: '90+ languages',
      installed: true,
    },
    {
      id: 'sherpa-parakeet-tdt-v3',
      repo_id: 'csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8',
      label: 'Parakeet TDT v3',
      tag: 'offline',
      recommended: false,
      size_gb: 0.67,
      languages: '25 European languages',
      installed: true,
    },
    {
      id: 'sherpa-zipformer-en-20m',
      repo_id: 'csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17',
      label: 'Zipformer Streaming EN',
      tag: 'streaming',
      recommended: false,
      size_gb: 0.044,
      languages: 'English',
      installed: false,
    },
  ],
  default_model_id: 'sherpa-whisper-tiny',
};

function renderSwitch(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EngineQuickSwitch family="asr" embedded {...props} />
    </QueryClientProvider>,
  );
}

describe('EngineQuickSwitch → dictation model picker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listEngines.mockResolvedValue(ASR_INVENTORY);
    listLoadedModels.mockResolvedValue({ models: [] });
    apiJson.mockImplementation(async (path) => {
      if (path === '/dictation/models') return MODELS;
      if (path === '/dictation/prefs')
        return { enabled: true, mode: 'toggle', model_id: 'sherpa-whisper-tiny' };
      throw new Error(`unexpected GET ${path}`);
    });
    apiPost.mockImplementation(async (path, body) => {
      if (path === '/dictation/prefs')
        return { enabled: true, mode: 'toggle', model_id: body.model_id };
      throw new Error(`unexpected POST ${path}`);
    });
    installMutate.mockResolvedValue({});
    useAppStore.setState({ dictationModelId: 'sherpa-whisper-tiny', dictationLoaded: true });
  });

  it('lists the sherpa dictation models under the Sherpa-ONNX engine row only', async () => {
    renderSwitch();
    const picker = await screen.findByTestId('dictation-model-picker');
    expect(picker).toHaveTextContent('Whisper Tiny');
    expect(picker).toHaveTextContent('Parakeet TDT v3');
    expect(picker).toHaveTextContent('Zipformer Streaming EN');
    // Nested directly after the Sherpa row, not after WhisperX.
    const sherpaRow = screen.getByText('Sherpa-ONNX dictation').closest('button');
    expect(sherpaRow.nextElementSibling).toBe(picker);
    // Engine rows themselves are untouched.
    expect(screen.getByText('WhisperX').closest('button')).toBeInTheDocument();
    expect(screen.getByTestId('dictation-model-sherpa-whisper-tiny')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('does not render the picker for a family without the Sherpa engine', async () => {
    renderSwitch({ family: 'tts' });
    expect(await screen.findByText('OmniVoice')).toBeInTheDocument();
    expect(screen.queryByTestId('dictation-model-picker')).not.toBeInTheDocument();
  });

  it('selecting an installed model writes dictation.model_id and does not download', async () => {
    renderSwitch();
    fireEvent.click(await screen.findByTestId('dictation-model-sherpa-parakeet-tdt-v3'));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', {
        model_id: 'sherpa-parakeet-tdt-v3',
      }),
    );
    expect(installMutate).not.toHaveBeenCalled();
    expect(useAppStore.getState().dictationModelId).toBe('sherpa-parakeet-tdt-v3');
    expect(screen.getByTestId('dictation-model-sherpa-parakeet-tdt-v3')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('selecting a model that is not installed persists the choice and starts the download', async () => {
    renderSwitch();
    fireEvent.click(await screen.findByTestId('dictation-model-sherpa-zipformer-en-20m'));
    await waitFor(() =>
      expect(installMutate).toHaveBeenCalledWith(
        'csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17',
      ),
    );
    expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', {
      model_id: 'sherpa-zipformer-en-20m',
    });
  });
});
