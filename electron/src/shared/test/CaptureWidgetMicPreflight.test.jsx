/**
 * Dictation mic pre-flight (guided OS-permissions UX): when the OS reports
 * the microphone grant as DENIED, the pill must skip getUserMedia entirely
 * and show the guided path (per-OS hint + Open Settings deep-link) instead
 * of the opaque NotAllowedError toast. Every other state ('granted',
 * 'prompt', 'unknown' — and the plain browser, which has no probe) proceeds
 * to getUserMedia exactly as before.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { toastMock, eventHandlers, captureState } = vi.hoisted(() => ({
  toastMock: Object.assign(vi.fn(), {
    error: vi.fn(),
    success: vi.fn(),
    dismiss: vi.fn(),
    loading: vi.fn(),
  }),
  eventHandlers: {},
  captureState: { pending: null },
}));
vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (name, handler) => {
    eventHandlers[name] = handler;
    return () => delete eventHandlers[name];
  }),
}));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({ hide: async () => {} }),
}));

// Keep the api/history/model-CTA side modules out of this test's blast radius.
vi.mock('../api/client', () => ({
  API: 'http://test',
  wsUrl: (p) => `ws://test${p}`,
  apiFetch: vi.fn(),
}));
vi.mock('../pages/Transcriptions', () => ({ addTranscription: vi.fn() }));
vi.mock('../utils/asrModelMissing', () => ({
  asrMissingPayload: () => null,
  toastAsrModelMissing: vi.fn(),
}));

// Minimal zustand stand-in: dictation enabled, toggle mode, no AEC/sherpa.
const storeState = {
  dictationEnabled: true,
  dictationMode: 'toggle',
  loadDictationPrefs: vi.fn(),
  aecEnabled: false,
  dictationModelId: null,
};
vi.mock('../store', () => {
  const useAppStore = (sel) => sel(storeState);
  useAppStore.getState = () => storeState;
  return { useAppStore };
});

import CaptureWidget from '../components/CaptureWidget';
import { requestDictationCapture } from '../utils/dictationCapture';

/** Route the invoke mock per command. */
function stubInvoke({ mic = 'granted' } = {}) {
  invokeMock.mockImplementation(async (cmd, payload) => {
    if (cmd === 'begin_dictation_capture_registration') return 1;
    if (cmd === 'check_microphone') return mic;
    if (cmd === 'check_accessibility') return true;
    if (cmd === 'request_dictation_capture') {
      const event = payload?.action === 'stop' ? 'tray-dictate-stop' : 'tray-dictate';
      const eventPayload =
        event === 'tray-dictate' ? { payload: { sessionId: 'mic-preflight-session' } } : undefined;
      if (eventHandlers[event]) return eventHandlers[event](eventPayload);
      captureState.pending = { event, eventPayload };
      return undefined;
    }
    if (cmd === 'mark_dictation_capture_ready' && captureState.pending) {
      const { event, eventPayload } = captureState.pending;
      captureState.pending = null;
      return eventHandlers[event]?.(eventPayload);
    }
    return undefined;
  });
}

/** A getUserMedia spy installed on jsdom's bare navigator. */
function installGum(impl) {
  const gum = vi.fn(impl);
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: gum },
    configurable: true,
  });
  return gum;
}

const notFound = () => {
  const e = new Error('no device');
  e.name = 'NotFoundError';
  return e;
};

/** Request capture through the same controller used by the page and shortcut. */
function pressShortcut() {
  void requestDictationCapture('start');
}

beforeEach(() => {
  captureState.pending = null;
  invokeMock.mockReset();
  toastMock.mockClear();
  toastMock.error.mockClear();
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  delete navigator.mediaDevices;
});

describe('CaptureWidget — mic permission pre-flight (Tauri)', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
  });

  // #1884: the pill clips its label, so every state that can clip needs a
  // hover title. This covers the error branch that carries a message — the
  // title is the detailed message, which is strictly more than the clipped
  // label shows. The no-message branch falls through to the label and is
  // covered by the setup-state test in CaptureWidget.test.jsx.
  it('uses the error detail as the title, not just the clipped label', async () => {
    stubInvoke({ mic: 'denied' });
    installGum(async () => {
      throw notFound();
    });
    render(<CaptureWidget />);
    pressShortcut();

    const labelEl = await screen.findByText(/Mic access denied/);
    // Assert the detail itself, not merely "some different string". This
    // phrase lives only in `errorInfo.message` (capture.mic_hint_linux) and
    // never in the label, so a regression that fell back to the label — or
    // any unrelated tooltip — fails here.
    expect(labelEl.title).toMatch(/audio group/);
  });

  it('OS-denied → guided error pill with Open Settings, getUserMedia never called', async () => {
    stubInvoke({ mic: 'denied' });
    const gum = installGum(async () => {
      throw notFound();
    });
    render(<CaptureWidget />);
    pressShortcut();

    // Guided path instead of the raw getUserMedia failure.
    await waitFor(() => {
      expect(screen.getByText(/Mic access denied/)).toBeInTheDocument();
    });
    expect(gum).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalled();

    // The pill's Open Settings action deep-links the OS mic-privacy pane.
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }));
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('open_microphone_settings');
    });
  });

  it('the shared desktop capture request surfaces microphone denial', async () => {
    stubInvoke({ mic: 'denied' });
    const gum = installGum(async () => {
      throw notFound();
    });
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));
    eventHandlers['tray-dictate']({ payload: { sessionId: 'mic-preflight-session' } });

    expect(await screen.findByText(/Mic access denied/)).toBeInTheDocument();
    expect(gum).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalled();
  });

  it.each(['granted', 'prompt', 'unknown'])(
    '"%s" proceeds to getUserMedia as before (reactive micError stays the fallback)',
    async (mic) => {
      stubInvoke({ mic });
      const gum = installGum(async () => {
        throw notFound();
      });
      render(<CaptureWidget />);
      pressShortcut();

      await waitFor(() => {
        expect(gum).toHaveBeenCalled();
      });
      // A no-device failure is NOT an OS denial — no Open Settings action.
      await waitFor(() => {
        expect(screen.getByText(/Mic access denied/)).toBeInTheDocument();
      });
      expect(screen.queryByRole('button', { name: 'Open Settings' })).not.toBeInTheDocument();
    },
  );
});

describe('CaptureWidget — plain browser (no Tauri)', () => {
  it('starts through the shared capture request', async () => {
    const gum = installGum(async () => {
      throw notFound();
    });
    render(<CaptureWidget />);
    await requestDictationCapture('start');

    await waitFor(() => expect(gum).toHaveBeenCalled());
  });

  it('behaviour unchanged: no permission probe, straight to getUserMedia', async () => {
    const gum = installGum(async () => {
      throw notFound();
    });
    render(<CaptureWidget />);
    pressShortcut();

    await waitFor(() => {
      expect(gum).toHaveBeenCalled();
    });
    expect(invokeMock).not.toHaveBeenCalledWith('check_microphone');
  });
});
