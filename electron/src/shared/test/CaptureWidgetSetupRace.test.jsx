/**
 * Connect-time WS error vs. mic-graph setup race: a typed asr_model_missing
 * error frame can arrive while startRecording is still awaiting
 * startMicCapture. The error branch resolves the session (error pill, socket
 * closed, wsHadFinalRef latched) — startRecording's tail must then ABORT
 * instead of clobbering the error with setState('recording') + tray flag,
 * which (with the socket gone) stranded the next Stop on "Transcribing…".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const { toastMock, eventHandlers, eventState, eventUnlisteners } = vi.hoisted(() => ({
  toastMock: Object.assign(vi.fn(), {
    error: vi.fn(),
    success: vi.fn(),
    dismiss: vi.fn(),
    loading: vi.fn(),
  }),
  eventHandlers: {},
  eventState: { pendingStart: false },
  eventUnlisteners: [],
}));
vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (name, handler) => {
    eventHandlers[name] = handler;
    const unlisten = vi.fn(() => delete eventHandlers[name]);
    eventUnlisteners.push(unlisten);
    return unlisten;
  }),
  emit: emitMock,
}));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({ hide: async () => {} }),
}));

vi.mock('../api/client', () => ({
  API: 'http://test',
  wsUrl: (p) => `ws://test${p}`,
  apiFetch: vi.fn(),
}));
vi.mock('../pages/Transcriptions', () => ({ addTranscription: vi.fn() }));

const { toastAsrMock, emitMock } = vi.hoisted(() => ({
  toastAsrMock: vi.fn(),
  emitMock: vi.fn(async () => {}),
}));
vi.mock('../utils/asrModelMissing', () => ({
  // Matches the real payload extraction for the WS frame shape.
  asrMissingPayload: (err) =>
    err && typeof err === 'object' && err.error === 'asr_model_missing' ? err : null,
  toastAsrModelMissing: toastAsrMock,
}));

// Deferred startMicCapture so each test controls WHEN (and HOW — resolve or
// reject) the mic graph finishes setting up relative to the WS error frame.
const { micControl, micStop } = vi.hoisted(() => ({
  micControl: { resolve: null, reject: null, onFrame: null },
  micStop: vi.fn(async () => {}),
}));
vi.mock('../utils/aec/micCapture', () => ({
  startMicCapture: vi.fn(
    (_stream, onFrame) =>
      new Promise((resolve, reject) => {
        micControl.onFrame = onFrame;
        micControl.resolve = resolve;
        micControl.reject = reject;
      }),
  ),
}));
vi.mock('../utils/aec/pcm', () => ({
  frameFromFloat: vi.fn(),
  floatToInt16: vi.fn(() => new Int16Array(0)),
  AEC_NEAR: 0,
  AEC_FAR: 1,
}));

// Sherpa live model selected → raw-PCM path (startMicCapture is awaited).
const storeState = {
  dictationEnabled: true,
  dictationMode: 'toggle',
  loadDictationPrefs: vi.fn(),
  aecEnabled: false,
  dictationModelId: 'sherpa-parakeet-v3',
};
vi.mock('../store', () => {
  const useAppStore = (sel) => sel(storeState);
  useAppStore.getState = () => storeState;
  return { useAppStore };
});

import CaptureWidget from '../components/CaptureWidget';

class FakeWS {
  static instances = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = FakeWS.CONNECTING;
    FakeWS.instances.push(this);
  }
  send() {}
  close() {
    this.readyState = FakeWS.CLOSED;
    this.onclose?.();
  }
}

function pressShortcut() {
  const handler = eventHandlers['tray-dictate'];
  if (handler) {
    handler({
      payload: { sessionId: 'setup-race-session', deliveryId: 9, registrationId: 1 },
    });
  } else eventState.pendingStart = true;
}

let realWebSocket;

beforeEach(() => {
  window.__TAURI_INTERNALS__ = {};
  invokeMock.mockReset();
  invokeMock.mockImplementation(async (cmd) => {
    if (cmd === 'begin_dictation_capture_registration') return 1;
    if (cmd === 'check_microphone') return 'granted';
    if (cmd === 'check_accessibility') return true;
    if (cmd === 'mark_dictation_capture_ready' && eventState.pendingStart) {
      eventState.pendingStart = false;
      return eventHandlers['tray-dictate']?.({
        payload: { sessionId: 'setup-race-session', deliveryId: 9, registrationId: 1 },
      });
    }
    return undefined;
  });
  eventState.pendingStart = false;
  eventUnlisteners.length = 0;
  FakeWS.instances = [];
  storeState.dictationEnabled = true;
  storeState.dictationModelId = 'sherpa-parakeet-v3';
  realWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = FakeWS;
  // jsdom has no MediaRecorder; only isTypeSupported is reached on the
  // raw-PCM (sherpa) path exercised here.
  globalThis.MediaRecorder = class {
    static isTypeSupported() {
      return false;
    }
  };
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })) },
    configurable: true,
  });
});

afterEach(() => {
  globalThis.WebSocket = realWebSocket;
  delete globalThis.MediaRecorder;
  delete window.__TAURI_INTERNALS__;
  delete navigator.mediaDevices;
});

describe('CaptureWidget — connect-time asr_model_missing during mic setup', () => {
  it('removes both native listeners when registration readiness fails', async () => {
    invokeMock.mockImplementation(async (cmd) => {
      if (cmd === 'begin_dictation_capture_registration') return 1;
      if (cmd === 'mark_dictation_capture_ready') throw new Error('registration failed');
      return undefined;
    });

    render(<CaptureWidget />);

    await waitFor(() => expect(eventUnlisteners).toHaveLength(2));
    await waitFor(() =>
      expect(eventUnlisteners.every((unlisten) => unlisten.mock.calls.length)).toBe(true),
    );
    expect(invokeMock).toHaveBeenCalledWith('end_dictation_capture_registration', {
      registrationId: 1,
    });
  });

  it('acknowledges a queued native event after the listener receives it', async () => {
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate-stop']).toBeTypeOf('function'));

    await eventHandlers['tray-dictate-stop']({
      payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
    });

    expect(invokeMock).toHaveBeenCalledWith('acknowledge_dictation_capture_delivery', {
      registrationId: 1,
      deliveryId: 9,
    });
  });

  it('does not start capture when native receipt acknowledgement fails', async () => {
    invokeMock.mockImplementation(async (cmd) => {
      if (cmd === 'begin_dictation_capture_registration') return 1;
      if (cmd === 'check_microphone') return 'granted';
      if (cmd === 'check_accessibility') return true;
      if (cmd === 'acknowledge_dictation_capture_delivery') {
        throw new Error('receipt state unavailable');
      }
      return undefined;
    });
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));

    await eventHandlers['tray-dictate']({
      payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
    });

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock).not.toHaveBeenCalledWith('activate_dictation_output_session', {
      sessionId: 7,
    });
    expect(invokeMock).not.toHaveBeenCalledWith(
      'complete_dictation_capture_delivery',
      expect.anything(),
    );
  });

  it('completes an in-page delivery only after microphone startup is accepted', async () => {
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));

    await eventHandlers['tray-dictate']({
      payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
    });
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    expect(invokeMock).not.toHaveBeenCalledWith('complete_dictation_capture_delivery', {
      registrationId: 1,
      deliveryId: 9,
      error: null,
    });

    micControl.resolve(micStop);
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
        registrationId: 1,
        deliveryId: 9,
        error: null,
      }),
    );
  });

  it('rejects an in-page delivery when dictation is disabled', async () => {
    storeState.dictationEnabled = false;
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));

    await eventHandlers['tray-dictate']({
      payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
    });

    expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
      registrationId: 1,
      deliveryId: 9,
      error: 'Dictation is disabled',
    });
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it('turns a PCM-fallback socket failure into a terminal error', async () => {
    storeState.dictationModelId = 'whisperx';
    render(<CaptureWidget />);
    pressShortcut();

    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    ws.onerror?.(new Event('error'));
    ws.onclose?.();
    micControl.resolve(micStop);

    await waitFor(() => expect(screen.getByText(/Transcription failed/)).toBeInTheDocument());
    expect(invokeMock).not.toHaveBeenCalledWith('set_tray_recording', { recording: true });
  });

  it('keeps the error pill: no recording state, no tray flag, mic released', async () => {
    render(<CaptureWidget />);
    pressShortcut();

    // The socket opens before the mic worklet finishes setting up.
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];

    // Typed preflight error lands while startMicCapture is still pending.
    ws.onmessage({
      data: JSON.stringify({
        type: 'error',
        kind: 'asr_model_missing',
        error: 'asr_model_missing',
        message: 'no ASR model installed',
        recommended: { repo_id: 'x/y', label: 'Y', size_gb: 1 },
      }),
    });
    await waitFor(() => {
      expect(screen.getByText(/No speech-to-text model/)).toBeInTheDocument();
    });
    // In the desktop app this window has no <Toaster>, so a local toast here
    // rendered nowhere. The install action goes to the main window instead,
    // carrying the recommendation so it can offer the one-click download.
    expect(toastAsrMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(emitMock).toHaveBeenCalledWith(
        'dictation-notice',
        expect.objectContaining({
          kind: 'asr_missing',
          missing: expect.objectContaining({
            recommended: expect.objectContaining({ repo_id: 'x/y' }),
          }),
        }),
      ),
    );

    // Mic graph setup completes AFTER the error — the tail must abort:
    // release the worklet, keep the error state, never flip the tray on.
    micControl.resolve(micStop);
    await waitFor(() => expect(micStop).toHaveBeenCalled());

    expect(screen.getByText(/No speech-to-text model/)).toBeInTheDocument();
    expect(screen.queryByText(/Listening/)).not.toBeInTheDocument();
    expect(invokeMock).not.toHaveBeenCalledWith('set_tray_recording', { recording: true });
    expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
      registrationId: 1,
      deliveryId: 9,
      error: 'Dictation could not start',
    });
  });

  it('setup REJECTION after the terminal frame must not clobber it with a mic error', async () => {
    // #1175 review: the guard used to live only on the success path — a
    // startMicCapture rejection after the typed asr_model_missing frame
    // overwrote the truthful terminal state with a generic microphone error.
    toastMock.error.mockClear();
    render(<CaptureWidget />);
    pressShortcut();

    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    ws.onmessage({
      data: JSON.stringify({
        type: 'error',
        kind: 'asr_model_missing',
        error: 'asr_model_missing',
        message: 'no ASR model installed',
        recommended: { repo_id: 'x/y', label: 'Y', size_gb: 1 },
      }),
    });
    await waitFor(() => {
      expect(screen.getByText(/No speech-to-text model/)).toBeInTheDocument();
    });

    micControl.reject(Object.assign(new Error('mic exploded'), { name: 'NotReadableError' }));
    // Let the rejection propagate through startRecording's catch.
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.getByText(/No speech-to-text model/)).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(invokeMock).not.toHaveBeenCalledWith('set_tray_recording', { recording: true });
  });
});

it('pauses and resumes the microphone, then closes and releases capture', async () => {
  const track = { enabled: true, stop: vi.fn() };
  navigator.mediaDevices.getUserMedia.mockResolvedValue({ getTracks: () => [track] });
  render(<CaptureWidget />);
  await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));
  await eventHandlers['tray-dictate']({
    payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
  });
  await waitFor(() => expect(FakeWS.instances.length).toBe(1));
  micControl.resolve(micStop);
  const pause = await screen.findByRole('button', { name: 'Pause' });
  fireEvent.click(pause);
  expect(track.enabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  expect(track.enabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(track.stop).toHaveBeenCalled());
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument(),
  );
});
