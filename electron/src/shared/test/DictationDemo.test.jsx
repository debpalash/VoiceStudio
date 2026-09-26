import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const { readiness, apiJson } = vi.hoisted(() => ({
  readiness: {
    phase: 'ready',
    missing: null,
    check: vi.fn().mockResolvedValue(true),
    install: vi.fn(),
    select: vi.fn(),
  },
  apiJson: vi.fn(),
}));
const { shortcutInfo } = vi.hoisted(() => ({
  shortcutInfo: { accelerator: 'CmdOrCtrl+Shift+Space', display: '⌘⇧Space', backend: 'native' },
}));
vi.mock('../hooks/useEffectiveDictationShortcut', () => ({
  useEffectiveDictationShortcut: () => ({ info: shortcutInfo }),
}));
vi.mock('../hooks/useDictationReadiness', () => ({
  useDictationReadiness: () => readiness,
}));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal()),
  apiJson: (...a) => apiJson(...a),
}));

import DictationDemo from '../components/DictationDemo';

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('DictationDemo', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    readiness.phase = 'ready';
    readiness.missing = null;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders the three bundled scripts', () => {
    render(withI18n(<DictationDemo />));
    expect(screen.getByText(/Schedule a meeting with Pat/)).toBeInTheDocument();
    expect(screen.getByText(/Patch the WebGPU shader/)).toBeInTheDocument();
    expect(screen.getByText(/réserver une table/)).toBeInTheDocument();
  });

  it('shows the "no hotkey" warning when outside Tauri', () => {
    render(withI18n(<DictationDemo />));
    // In jsdom, isTauri() returns false → state stays 'unknown' → warn badge.
    expect(screen.getByText(/No hotkey registered/i)).toBeInTheDocument();
  });

  it('keeps the hotkey card but hides the script cards when samples are missing (HEAD 404)', async () => {
    // The mount probe HEAD-checks the first sample; a 404 means no rendered
    // assets on disk. Since #294 the demo no longer disappears wholesale —
    // that blanked the wizard's Try-dictation act on every real install.
    // The hotkey card (shortcut + press-to-verify) needs no assets and must
    // stay; only the replayable script cards are asset-gated.
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 404 }));
    const { container } = render(withI18n(<DictationDemo />));
    await waitFor(() => expect(container.querySelector('.dictation-demo__scripts')).toBeNull());
    // The panel itself (hotkey teaching) is still there.
    expect(container.querySelector('.dictation-demo')).not.toBeNull();
    expect(screen.queryByText(/Schedule a meeting with Pat/)).not.toBeInTheDocument();
  });

  it('POSTs the bundled WAV to /transcribe when Replay is clicked', async () => {
    const wavBlob = new Blob([new Uint8Array([0, 0, 0, 0])], { type: 'audio/wav' });
    // Make the recognized text deliberately different from the on-card
    // script so we can assert the transcribed result appears in the UI.
    const RECOGNIZED = 'sentinel-recognized-payload-9421';
    global.fetch = vi.fn((url) => {
      if (String(url).endsWith('.wav')) {
        return Promise.resolve({ ok: true, blob: () => Promise.resolve(wavBlob) });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ text: RECOGNIZED, language: 'en' }),
      });
    });

    render(withI18n(<DictationDemo />));
    const replayButton = screen.getByLabelText(/Replay Conversational/i);
    fireEvent.click(replayButton);

    // Wait for both fetches to complete and the recognized text to render.
    // The full chain is: fetch(.wav) → fetch(/transcribe) → setState → render.
    await waitFor(
      () => {
        const calls = global.fetch.mock.calls;
        const wavCall = calls.find((c) => String(c[0]).endsWith('.wav'));
        expect(wavCall).toBeTruthy();
      },
      { timeout: 3000 },
    );
    await waitFor(
      () => {
        const calls = global.fetch.mock.calls;
        expect(calls.find((c) => String(c[0]).endsWith('/transcribe'))).toBeTruthy();
      },
      { timeout: 3000 },
    );
    await waitFor(() => expect(screen.getByText(RECOGNIZED)).toBeInTheDocument(), {
      timeout: 3000,
    });

    const calls = global.fetch.mock.calls;
    const transcribeCall = calls.find((c) => String(c[0]).endsWith('/transcribe'));
    expect(transcribeCall[1]?.method).toBe('POST');
  });

  // #1856: the mandatory-only install path ships no speech-to-text model, so
  // the final onboarding step opened with one hard error per script card and
  // told the user to press a hotkey that could not transcribe anything. The
  // step now offers the models instead of failing three times.
  it('offers the models instead of failing every card when none is installed', async () => {
    readiness.phase = 'missing';
    readiness.missing = { recommended: { repo_id: 'r/tiny', label: 'Tiny', size_gb: 0.1 } };
    apiJson.mockResolvedValue({
      models: [
        {
          id: 'sherpa-whisper-tiny',
          repo_id: 'r/tiny',
          label: 'Tiny',
          tag: 'offline',
          recommended: true,
          size_gb: 0.1,
          languages: '90+ languages',
          installed: false,
        },
      ],
    });
    render(withI18n(<DictationDemo />));

    expect(await screen.findByTestId('asr-model-chooser')).toBeInTheDocument();
    expect(screen.queryByText(/Schedule a meeting with Pat/)).not.toBeInTheDocument();
  });

  it('keeps the script cards while readiness is still being probed', () => {
    readiness.phase = 'checking';
    readiness.missing = null;
    render(withI18n(<DictationDemo />));

    // A sub-second probe must not flash the install panel over the cards.
    expect(screen.getByText(/Schedule a meeting with Pat/)).toBeInTheDocument();
    expect(screen.queryByTestId('asr-model-chooser')).not.toBeInTheDocument();
  });
});

// #1858: whichever app registers a global shortcut first wins, and the default
// collides with 1Password Quick Access on macOS. Registration failure used to
// be a Rust-side log line and nothing else — the frontend kept reporting the
// accelerator that had been REQUESTED, so the onboarding screen advertised a
// hotkey the OS had refused, with no way for the user to find out.
describe('DictationDemo — hotkey registration failure', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
  });
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it('says the shortcut is taken rather than merely unregistered', () => {
    shortcutInfo.backend = 'unregistered';
    shortcutInfo.display = '⌘⇧Space';
    render(withI18n(<DictationDemo />));

    expect(screen.getByText(/Another app already uses this shortcut/i)).toBeInTheDocument();
    // "No hotkey registered" means "not checked yet" — a different situation.
    expect(screen.queryByText(/No hotkey registered/i)).not.toBeInTheDocument();
  });

  it('still names the shortcut that failed', () => {
    // The user has to know WHICH combination is taken to pick another.
    shortcutInfo.backend = 'unregistered';
    shortcutInfo.display = '⌘⇧Space';
    render(withI18n(<DictationDemo />));

    expect(screen.getByText('⌘⇧Space')).toBeInTheDocument();
  });

  it('leaves a successfully registered shortcut alone', () => {
    shortcutInfo.backend = 'native';
    render(withI18n(<DictationDemo />));

    expect(screen.queryByText(/Another app already uses this shortcut/i)).not.toBeInTheDocument();
  });
});
