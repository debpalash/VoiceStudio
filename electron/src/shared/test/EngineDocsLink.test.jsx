/**
 * An unavailable engine row links to the doc that explains it (#1866).
 *
 * The row cannot explain itself. `public_backends()` replaces the computed
 * `reason` and `last_error` with two fixed strings before they reach the UI,
 * because an availability probe can carry exception text, a local path or a
 * credential. So "Engine unavailable. Check installation and configuration."
 * and "Last error: A previous engine check failed." are all the row can ever
 * say, and the second one reads like a crash when the truth is usually that an
 * optional engine was never installed.
 *
 * `docs/engines/<engine>.md` is what actually explains it — accurate, and in
 * cosyvoice's case CI-guarded against the installer registry — and nothing in
 * `electron/src/shared` referenced `docs/engines` at all. The backend now carries a
 * registry-authored `docs_url` that the scrub leaves alone, and the row renders
 * it with the same "Learn more →" affordance the MCP and Remote GPU panels use.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../api/system', () => ({
  listLoadedModels: vi.fn().mockResolvedValue({ models: [], count: 0 }),
  unloadLoadedModel: vi.fn(),
}));

const openExternal = vi.fn();
vi.mock('../api/external', () => ({ openExternal: (...a) => openExternal(...a) }));

import EngineCompatibilityMatrix from '../components/EngineCompatibilityMatrix';

const DOCS = 'https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/cosyvoice.md';

const response = () => ({
  tts: {
    active: 'omnivoice',
    backends: [
      {
        id: 'omnivoice',
        display_name: 'OmniVoice (test)',
        available: true,
        reason: null,
        docs_url: 'https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/omnivoice.md',
        isolation_mode: 'in-process',
        gpu_compat: ['cpu'],
      },
      {
        id: 'cosyvoice',
        display_name: 'CosyVoice 3 (test)',
        available: false,
        // Exactly what the scrub leaves for an engine that is merely not
        // installed — no package name, no next action.
        reason: 'Engine unavailable. Check installation and configuration.',
        last_error: 'A previous engine check failed.',
        install_hint: 'git clone --recursive FunAudioLLM/CosyVoice',
        docs_url: DOCS,
        isolation_mode: 'in-process',
        gpu_compat: ['cuda', 'cpu'],
      },
    ],
  },
  asr: { active: 'whisperx', backends: [] },
  llm: { active: 'off', backends: [] },
});

beforeEach(() => {
  openExternal.mockClear();
});

describe('unavailable engine rows point at their doc', () => {
  const mount = () =>
    render(
      <EngineCompatibilityMatrix
        family="tts"
        activeId="omnivoice"
        apiListEngines={vi.fn().mockResolvedValue(response())}
        apiGetEngineHealth={vi.fn()}
      />,
    );

  it('opens the engine doc from the why panel', async () => {
    mount();
    await waitFor(() => screen.getByText('CosyVoice 3 (test)'));

    fireEvent.click(screen.getByTestId('why-toggle-cosyvoice'));
    const link = screen.getByTestId('engine-docs-cosyvoice');
    // Inside the why panel, next to the two placeholder strings it exists to
    // compensate for — not somewhere the user has to go looking.
    expect(screen.getByTestId('engine-detail-cosyvoice').contains(link)).toBe(true);

    fireEvent.click(link);
    expect(openExternal).toHaveBeenCalledWith(DOCS);
  });

  it('renders nothing when the backend sends no docs_url', async () => {
    const legacy = response();
    legacy.tts.backends[1].docs_url = null;
    render(
      <EngineCompatibilityMatrix
        family="tts"
        activeId="omnivoice"
        apiListEngines={vi.fn().mockResolvedValue(legacy)}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitFor(() => screen.getByText('CosyVoice 3 (test)'));

    fireEvent.click(screen.getByTestId('why-toggle-cosyvoice'));
    expect(screen.getByTestId('engine-detail-cosyvoice')).toBeInTheDocument();
    expect(screen.queryByTestId('engine-docs-cosyvoice')).not.toBeInTheDocument();
  });
});
