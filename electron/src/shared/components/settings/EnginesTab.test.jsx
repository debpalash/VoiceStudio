import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Keep toast side-channels out of the test (timers, portals).
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

vi.mock('../../api/engines', () => ({
  listEngines: vi.fn(),
  selectEngine: vi.fn(),
  getEngineHealth: vi.fn(),
  selfTestEngine: vi.fn(),
  installSidecarEngine: vi.fn(),
  getSidecarInstallStatus: vi.fn(),
  getEngineDiskUsage: vi.fn(),
}));

// Residency layer (/model/loaded) — mocked so the matrix never hits the
// network in tests; the single-probe behavior is asserted below.
vi.mock('../../api/system', () => ({
  listLoadedModels: vi.fn(),
  unloadLoadedModel: vi.fn(),
}));

// The ASR tab mounts AsrOpenAICompatPanel, which loads its config over the
// api client — mocked so switching tabs never hits the network here.
const apiJson = vi.fn();
const apiFetch = vi.fn();
vi.mock('../../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
  apiPost: vi.fn(),
}));

import { listEngines, selectEngine } from '../../api/engines';
import { listLoadedModels } from '../../api/system';
import EnginesTab from './EnginesTab';

function renderEnginesTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EnginesTab />
    </QueryClientProvider>,
  );
}

function entry(id, name) {
  return {
    id,
    display_name: name,
    available: true,
    reason: null,
    install_hint: null,
    last_error: null,
    isolation_mode: 'in-process',
    gpu_compat: ['cpu'],
  };
}

const ENGINES = {
  tts: { active: 'omnivoice', backends: [entry('omnivoice', 'VoiceStudio (test)')] },
  asr: {
    active: 'whisperx',
    backends: [
      entry('whisperx', 'WhisperX (test)'),
      entry('openai-compat-asr', 'OpenAI-compatible ASR (test)'),
    ],
  },
  llm: { active: 'off', backends: [entry('off', 'Off (test)')] },
};

/** Click the family tab whose label text is `label` (TTS / ASR / LLM).
 *
 * Radix Tabs activate on POINTER DOWN, not click — a bare fireEvent.click
 * leaves the family unchanged, which reads as a matrix that ignores its own
 * tabs. Drive it the way a pointer does. */
function clickFamilyTab(label) {
  const tab = Array.from(document.querySelectorAll('.engine-matrix__tab-family')).find(
    (el) => el.textContent === label,
  );
  expect(tab).toBeTruthy();
  const trigger = tab.closest('button');
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' });
  fireEvent.mouseDown(trigger, { button: 0 });
  fireEvent.click(trigger);
}

describe('EnginesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listEngines.mockResolvedValue(ENGINES);
    listLoadedModels.mockResolvedValue({ models: [], count: 0 });
    apiFetch.mockResolvedValue({
      json: async () => ({
        base_url: 'http://localhost:8000/v1',
        model: 'qwen3-asr',
        has_key: false,
      }),
    });
    // AsrOpenAICompatPanel's GET on mount (ASR tab only).
    apiJson.mockResolvedValue({ base_url: '', model: 'whisper-1', has_key: false });
  });

  it('renders ONE tabbed section — TTS/ASR/LLM tab strip, one family at a time', async () => {
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));

    // One settings card, not three stacked per-family matrices.
    expect(document.querySelectorAll('[data-slot="settings-section"]').length).toBe(1);
    // The tab strip offers all three families over ONE engine list.
    expect(document.querySelectorAll('.engine-matrix__tab-family').length).toBe(3);
    expect(screen.getAllByTestId('engine-list')).toHaveLength(1);
    // Only the selected family's engines are on screen.
    expect(screen.queryByText('WhisperX (test)')).not.toBeInTheDocument();
    expect(screen.queryByText('Off (test)')).not.toBeInTheDocument();
    expect(document.querySelector('[data-engine-id="omnivoice"]')).toBeInTheDocument();
  });

  it('switching to the ASR tab shows ASR engines without refetching /engines', async () => {
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));

    clickFamilyTab('ASR');
    await waitFor(() => screen.getByText('WhisperX (test)'));
    expect(screen.getByText('OpenAI-compatible ASR (test)')).toBeInTheDocument();
    expect(screen.queryByText('VoiceStudio (test)')).not.toBeInTheDocument();
    // Tab switches re-slice the already-fetched payload — no second request.
    expect(listEngines).toHaveBeenCalledTimes(1);
  });

  it('fetches GET /engines exactly once on mount', async () => {
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));
    expect(listEngines).toHaveBeenCalledTimes(1);
  });

  it('probes GET /model/loaded exactly once on mount', async () => {
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));
    await waitFor(() => expect(listLoadedModels).toHaveBeenCalled());
    expect(listLoadedModels).toHaveBeenCalledTimes(1);
  });

  it('refetches the shared inventory after saving OpenAI-compatible ASR config', async () => {
    renderEnginesTab();
    await screen.findByText('VoiceStudio (test)');
    clickFamilyTab('ASR');
    await screen.findByTestId('asr-openai-compat-model');

    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'qwen3-asr' },
    });
    fireEvent.click(screen.getByTestId('asr-openai-compat-save'));

    await waitFor(() => expect(listEngines).toHaveBeenCalledTimes(2));
  });

  it('clicking Use on an ASR engine selects it with family="asr"', async () => {
    selectEngine.mockResolvedValue({
      family: 'asr',
      active: 'openai-compat-asr',
      env_override: false,
      routing_status: 'cpu_only',
      effective_device: 'cpu',
      routing_reason: null,
    });
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));

    clickFamilyTab('ASR');
    await waitFor(() => screen.getByText('OpenAI-compatible ASR (test)'));

    fireEvent.click(screen.getByRole('button', { name: /use openai-compatible asr \(test\)/i }));
    await waitFor(() => {
      expect(selectEngine).toHaveBeenCalledWith('asr', 'openai-compat-asr', undefined);
    });
  });

  it('mounts the OpenAI-compatible ASR config panel on the ASR tab only', async () => {
    renderEnginesTab();
    await waitFor(() => screen.getByText('VoiceStudio (test)'));
    // TTS tab: no ASR config panel.
    expect(screen.queryByTestId('asr-openai-compat-base-url')).not.toBeInTheDocument();

    clickFamilyTab('ASR');
    await screen.findByTestId('asr-openai-compat-base-url');
    expect(screen.getByTestId('asr-openai-compat-test')).toBeInTheDocument();

    // Back to TTS: the panel unmounts again.
    clickFamilyTab('TTS');
    await waitFor(() =>
      expect(screen.queryByTestId('asr-openai-compat-base-url')).not.toBeInTheDocument(),
    );
  });
});
