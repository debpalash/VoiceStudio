/**
 * Settings → Capture → Voice panel render + interaction.
 *
 * Verifies the screenshot contract: the Voice title/subtitle, the enable
 * toggle, the Toggle/Hold mode control, and the Speech Model dropdown listing
 * models with badges + size + a download/delete affordance. Also asserts the
 * panel writes the model pref through the store on selection.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

// ── Mocks ────────────────────────────────────────────────────────────────
const apiJson = vi.fn();
const apiPost = vi.fn();
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiPost: (...a) => apiPost(...a),
}));

const installMutate = vi.fn(() => Promise.resolve());
const deleteMutate = vi.fn(() => Promise.resolve());
vi.mock('../api/hooks', () => ({
  useInstallModel: () => ({ mutateAsync: installMutate }),
  useDeleteModel: () => ({ mutateAsync: deleteMutate }),
}));
vi.mock('../api/setup', () => ({ setupDownloadStreamUrl: () => 'http://localhost/stream' }));

import VoicePanel from '../components/settings/VoicePanel';
import { useAppStore } from '../store';

const MODELS = {
  models: [
    {
      id: 'sherpa-parakeet-tdt-v3',
      repo_id: 'org/parakeet-v3',
      label: 'Parakeet TDT v3',
      tag: 'offline',
      recommended: false,
      size_gb: 0.67,
      languages: '25 European languages',
      installed: false,
    },
    {
      id: 'sherpa-whisper-tiny',
      repo_id: 'org/whisper-tiny',
      label: 'Whisper Tiny',
      tag: 'offline',
      recommended: true,
      size_gb: 0.104,
      languages: '90+ languages',
      installed: true,
    },
  ],
  engine_available: true,
  engine_reason: null,
  default_model_id: 'sherpa-whisper-tiny',
};

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('VoicePanel', () => {
  beforeEach(() => {
    apiJson.mockReset();
    apiPost.mockReset();
    installMutate.mockClear();
    deleteMutate.mockClear();
    // Stub EventSource (jsdom lacks it).
    global.EventSource = class {
      constructor() {
        this.onmessage = null;
      }
      close() {}
    };
    // /dictation/models and /dictation/prefs both go through apiJson.
    apiJson.mockImplementation((path) => {
      if (path === '/dictation/models') return Promise.resolve(MODELS);
      if (path === '/dictation/prefs')
        return Promise.resolve({
          enabled: true,
          mode: 'toggle',
          model_id: 'sherpa-whisper-tiny',
        });
      return Promise.resolve({});
    });
    apiPost.mockResolvedValue({ enabled: true, mode: 'toggle', model_id: 'sherpa-whisper-tiny' });
    useAppStore.setState({
      dictationEnabled: true,
      dictationMode: 'toggle',
      dictationModelId: 'sherpa-whisper-tiny',
      dictationLoaded: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it('renders the Voice title, subtitle, toggle, mode control and model dropdown', async () => {
    render(withI18n(<VoicePanel />));
    expect(screen.getByText('Voice')).toBeInTheDocument();
    expect(screen.getByText(/Local speech-to-text dictation/)).toBeInTheDocument();
    expect(screen.getByText('Enable Voice Dictation')).toBeInTheDocument();
    expect(screen.getByText('Dictation Mode')).toBeInTheDocument();
    // The switch reflects the enabled pref.
    expect(screen.getByRole('switch', { name: 'Enable Voice Dictation' })).toBeChecked();
    // The dropdown trigger shows the selected model once models load.
    await waitFor(() =>
      expect(screen.getByTestId('dictation-model-trigger')).toHaveTextContent('Whisper Tiny'),
    );
  });

  it('lists models with badges, size and install/delete affordances when expanded', async () => {
    render(withI18n(<VoicePanel />));
    await waitFor(() =>
      expect(screen.getByTestId('dictation-model-trigger')).toHaveTextContent('Whisper Tiny'),
    );
    fireEvent.click(screen.getByTestId('dictation-model-trigger'));

    const whisper = screen.getByTestId('dictation-model-sherpa-whisper-tiny').closest('li');
    expect(within(whisper).getByText('recommended')).toBeInTheDocument();
    expect(within(whisper).getByText('offline')).toBeInTheDocument();
    expect(within(whisper).getByText('104 MB')).toBeInTheDocument();
    // Installed → delete affordance.
    expect(screen.getByTestId('dictation-delete-sherpa-whisper-tiny')).toBeInTheDocument();
    // Not installed → download affordance.
    expect(screen.getByTestId('dictation-install-sherpa-parakeet-tdt-v3')).toBeInTheDocument();
  });

  it('writes the model pref and kicks off install when picking an uninstalled model', async () => {
    render(withI18n(<VoicePanel />));
    await waitFor(() => expect(screen.getByTestId('dictation-model-trigger')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('dictation-model-trigger'));
    fireEvent.click(screen.getByTestId('dictation-model-sherpa-parakeet-tdt-v3'));

    // Pref write-through (POST /dictation/prefs with the new model id).
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', {
        model_id: 'sherpa-parakeet-tdt-v3',
      }),
    );
    // Uninstalled → download started via the model-store install mutation.
    expect(installMutate).toHaveBeenCalledWith('org/parakeet-v3');
  });

  it('toggles the enable switch through the store write-through', async () => {
    render(withI18n(<VoicePanel />));
    fireEvent.click(screen.getByRole('switch', { name: 'Enable Voice Dictation' }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', { enabled: false }),
    );
  });
});
