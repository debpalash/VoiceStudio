import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn() } }));

const { listEngines, selectEngine, listLoadedModels } = vi.hoisted(() => ({
  listEngines: vi.fn(),
  selectEngine: vi.fn(),
  listLoadedModels: vi.fn(),
}));
vi.mock('../api/engines', () => ({ listEngines, selectEngine }));
vi.mock('../api/system', () => ({ listLoadedModels }));

import EngineQuickSwitch from './EngineQuickSwitch';

const inventory = (env_override = false) => ({
  tts: {
    active: 'omnivoice',
    env_override,
    backends: [
      { id: 'omnivoice', display_name: 'OmniVoice', available: true },
      { id: 'indextts2', display_name: 'IndexTTS 2', available: true },
      { id: 'offline', display_name: 'Offline', available: false },
    ],
  },
  asr: { active: 'whisper', backends: [] },
  llm: { active: 'off', backends: [] },
});

function renderPicker(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EngineQuickSwitch {...props} />
    </QueryClientProvider>,
  );
}

describe('EngineQuickSwitch', () => {
  it('embeds engine choices without another popup trigger', async () => {
    renderPicker({ embedded: true });
    expect(await screen.findByText('IndexTTS 2')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /active tts:/i })).not.toBeInTheDocument();
  });
  it('anchors the prominent menu to the left-hand engine button', async () => {
    renderPicker({ prominent: true });
    fireEvent.click(await screen.findByRole('button', { name: /active tts: omnivoice/i }));
    expect(screen.getByRole('dialog')).toHaveClass('left-0');
    expect(screen.getByRole('dialog')).not.toHaveClass('right-0');
  });

  beforeEach(() => {
    vi.clearAllMocks();
    listEngines.mockResolvedValue(inventory());
    listLoadedModels.mockResolvedValue({ models: [{ engine_id: 'omnivoice' }] });
  });

  it.each(['VoiceStudio', 'VoiceStudio TTS', 'VoiceStudio (k2-fsa/OmniVoice)'])(
    'formats the active name consistently for %s',
    async (name) => {
      const data = inventory();
      data.tts.backends[0].display_name = name;
      listEngines.mockResolvedValue(data);
      const expected = name.replace('VoiceStudio', 'OmniVoice');
      const { unmount } = renderPicker();
      const trigger = await screen.findByRole('button', {
        name: `Active TTS: ${expected}`,
      });
      expect(trigger).toHaveAttribute('title', `Active TTS: ${expected}`);
      expect(trigger).toHaveTextContent(expected);
      fireEvent.click(trigger);
      expect(screen.getByRole('dialog')).not.toHaveTextContent('VoiceStudio');
      unmount();
      renderPicker({ embedded: true });
      expect(await screen.findByRole('group')).not.toHaveTextContent('VoiceStudio');
      expect(screen.getByRole('group')).toHaveTextContent('OmniVoice');
    },
  );

  it('lists only available engines and annotates residency', async () => {
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: /active tts: omnivoice/i }));

    expect(screen.getByText('IndexTTS 2')).toBeInTheDocument();
    expect(screen.queryByText('Offline')).not.toBeInTheDocument();
    expect(await screen.findByText('In memory')).toBeInTheDocument();
  });

  it('selects through the shared mutation', async () => {
    selectEngine.mockResolvedValue({ family: 'tts', active: 'indextts2', env_override: false });
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: /active tts: omnivoice/i }));
    fireEvent.click(screen.getByText('IndexTTS 2'));

    await waitFor(() => expect(selectEngine).toHaveBeenCalledWith('tts', 'indextts2', undefined));
  });

  it('locks a family owned by an environment variable', async () => {
    listEngines.mockResolvedValue(inventory(true));
    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: /active tts: omnivoice/i }));

    expect(screen.getByText(/set via an environment variable/i)).toBeInTheDocument();
    expect(screen.getByText('IndexTTS 2').closest('button')).toBeDisabled();
  });

  it('opens only the designated picker from the global shortcut bridge', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <EngineQuickSwitch />
        <EngineQuickSwitch shortcutTarget />
      </QueryClientProvider>,
    );
    await screen.findAllByRole('button', { name: /active tts: omnivoice/i });

    fireEvent(window, new Event('engine-quick-switch'));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
  });
});
