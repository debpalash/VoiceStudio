/**
 * The pill has to be ON SCREEN while a capture is running.
 *
 * The widget window is created hidden and, for a while, nothing ever showed it
 * again: dictation recorded, transcribed and pasted with no visible feedback at
 * all, and a failed session — a denied mic, a missing Accessibility grant —
 * looked exactly like a hotkey that did nothing.
 *
 * The invariant, pinned here: any state but `idle` shows the window, and `idle`
 * never does (the reconcile in CaptureWidget is what hides it again).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const mocks = vi.hoisted(() => {
  const state = {
    dictationEnabled: true,
    dictationMode: 'toggle',
    dictationModelId: 'sherpa-parakeet-tdt-v3',
    aecEnabled: false,
    loadDictationPrefs: () => {},
  };
  const holder = {
    a11y: true,
    handlers: {},
    invoked: [],
    hide: vi.fn(async () => {}),
    isVisible: vi.fn(async () => false),
    label: 'widget',
  };
  return { state, holder };
});

vi.mock('../store', () => ({
  useAppStore: Object.assign((sel) => sel(mocks.state), { getState: () => mocks.state }),
}));
vi.mock('../api/client', () => ({
  API: 'http://test',
  wsUrl: (p) => `ws://test${p}`,
  apiFetch: vi.fn(async () => ({ json: async () => ({}) })),
}));
vi.mock('../pages/Transcriptions', () => ({ addTranscription: vi.fn() }));
vi.mock('../utils/copyText', () => ({ copyText: vi.fn(async () => {}) }));
vi.mock('react-hot-toast', () => ({ toast: { error: vi.fn() } }));
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async (cmd) => {
    mocks.holder.invoked.push(cmd);
    if (cmd === 'begin_dictation_capture_registration') return 1;
    if (cmd === 'check_accessibility') return mocks.holder.a11y;
    return undefined;
  }),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (name, fn) => {
    mocks.holder.handlers[name] = fn;
    return () => {};
  }),
}));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    get label() {
      return mocks.holder.label;
    },
    hide: mocks.holder.hide,
    isVisible: mocks.holder.isVisible,
  }),
}));
vi.mock('../utils/aec/micCapture', () => ({
  startMicCapture: async () => async () => {},
}));

import CaptureWidget from '../components/CaptureWidget';

const renderWidget = () =>
  render(
    <I18nextProvider i18n={i18n}>
      <CaptureWidget />
    </I18nextProvider>,
  );

const shows = () => mocks.holder.invoked.filter((cmd) => cmd === 'show_dictation_pill');

beforeEach(() => {
  window.__TAURI_INTERNALS__ = {};
  mocks.holder.handlers = {};
  mocks.holder.invoked = [];
  mocks.holder.a11y = true;
  mocks.holder.label = 'widget';
  mocks.state.dictationEnabled = true;
  mocks.state.dictationMode = 'toggle';
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  vi.restoreAllMocks();
});

describe('the pill window is shown for the states the user must see', () => {
  it('shows itself when the capture needs the user to act', async () => {
    // A denied Accessibility probe drives the pill to 'setup' — the state that
    // asks for a grant. Invisible, it is indistinguishable from a dead hotkey.
    mocks.holder.a11y = false;
    renderWidget();

    await waitFor(() => expect(shows().length).toBeGreaterThan(0));
  });

  it('stays hidden while idle', async () => {
    renderWidget();

    await waitFor(() => {
      expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function');
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(shows()).toHaveLength(0);
  });

  it('does not show for a press that arrives while dictation is disabled', async () => {
    // The emit is unconditional — Rust does not know the toggle is off. A show
    // here would strand an empty capsule the user cannot dismiss.
    mocks.state.dictationEnabled = false;
    renderWidget();

    await waitFor(() => {
      expect(mocks.holder.handlers['tray-dictate']).toBeTypeOf('function');
    });
    await act(async () => {
      mocks.holder.handlers['tray-dictate']();
      await Promise.resolve();
    });

    expect(shows()).toHaveLength(0);
    expect(mocks.holder.hide).toHaveBeenCalled();
  });
});
