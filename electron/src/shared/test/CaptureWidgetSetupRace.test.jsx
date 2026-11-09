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
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';

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

const shortcutEvent = () => ({
  payload: { sessionId: 7, deliveryId: 9, registrationId: 1 },
});

function pressShortcut() {
  const handler = eventHandlers['tray-dictate'];
  if (handler) return handler(shortcutEvent());
  eventState.pendingStart = true;
  return undefined;
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
      return eventHandlers['tray-dictate']?.(shortcutEvent());
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

  it('does not start or complete a delivery whose acknowledgement fails', async () => {
    invokeMock.mockImplementation(async (cmd) => {
      if (cmd === 'begin_dictation_capture_registration') return 1;
      if (cmd === 'check_accessibility') return true;
      if (cmd === 'acknowledge_dictation_capture_delivery') throw new Error('receipt refused');
      return undefined;
    });
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));

    await eventHandlers['tray-dictate'](shortcutEvent());

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock).not.toHaveBeenCalledWith('activate_dictation_output_session', {
      sessionId: 7,
    });
    expect(
      invokeMock.mock.calls.some(([cmd]) => cmd === 'complete_dictation_capture_delivery'),
    ).toBe(false);
  });

  it('completes a native delivery only after microphone startup succeeds', async () => {
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));
    const delivery = shortcutEvent();

    void eventHandlers['tray-dictate'](delivery);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    expect(invokeMock).not.toHaveBeenCalledWith('complete_dictation_capture_delivery', {
      registrationId: delivery.payload.registrationId,
      deliveryId: delivery.payload.deliveryId,
      error: null,
    });

    micControl.resolve(micStop);
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
        registrationId: delivery.payload.registrationId,
        deliveryId: delivery.payload.deliveryId,
        error: null,
      }),
    );
  });

  it('completes a disabled native delivery without opening the microphone', async () => {
    storeState.dictationEnabled = false;
    render(<CaptureWidget />);
    await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));
    const delivery = shortcutEvent();

    await eventHandlers['tray-dictate'](delivery);

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
      registrationId: delivery.payload.registrationId,
      deliveryId: delivery.payload.deliveryId,
      error: 'Dictation is turned off',
    });
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

  it('keeps the missing-model error after microphone setup finishes late', async () => {
    render(<CaptureWidget />);
    const delivery = shortcutEvent();
    pressShortcut();
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    const missing = {
      type: 'error',
      kind: 'asr_model_missing',
      error: 'asr_model_missing',
      recommended: { repo_id: 'acme/speech-small', label: 'Speech Small', size_gb: 1.2 },
    };

    await act(async () => {
      await FakeWS.instances[0].onmessage({ data: JSON.stringify(missing) });
    });
    expect(await screen.findByText(/No speech-to-text model is installed/)).toBeInTheDocument();
    expect(toastAsrMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(emitMock).toHaveBeenCalledWith(
        'dictation-notice',
        expect.objectContaining({
          kind: 'asr_missing',
          missing: expect.objectContaining({
            recommended: expect.objectContaining({ repo_id: 'acme/speech-small' }),
          }),
        }),
      ),
    );

    await act(async () => micControl.resolve(micStop));
    await waitFor(() => expect(micStop).toHaveBeenCalled());
    expect(screen.getByText(/No speech-to-text model is installed/)).toBeInTheDocument();
    expect(screen.queryByText('Listening…')).not.toBeInTheDocument();
    expect(invokeMock).not.toHaveBeenCalledWith('set_tray_recording', { recording: true });
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('complete_dictation_capture_delivery', {
        registrationId: delivery.payload.registrationId,
        deliveryId: delivery.payload.deliveryId,
        error: 'Dictation could not start',
      }),
    );
  });

  it('does not replace the missing-model error when late microphone setup rejects', async () => {
    render(<CaptureWidget />);
    pressShortcut();
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    const missing = {
      type: 'error',
      kind: 'asr_model_missing',
      error: 'asr_model_missing',
      recommended: { repo_id: 'acme/speech-small', label: 'Speech Small', size_gb: 1.2 },
    };
    await act(async () => {
      await FakeWS.instances[0].onmessage({ data: JSON.stringify(missing) });
    });
    const failure = new Error('input device became busy');
    failure.name = 'NotReadableError';
    await act(async () => {
      micControl.reject(failure);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByText(/No speech-to-text model is installed/)).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(invokeMock).not.toHaveBeenCalledWith('set_tray_recording', { recording: true });
  });
});

it('pauses, resumes, and closes a capture started by the native shortcut', async () => {
  const track = { enabled: true, stop: vi.fn() };
  navigator.mediaDevices.getUserMedia.mockResolvedValue({ getTracks: () => [track] });
  render(<CaptureWidget />);
  await waitFor(() => expect(eventHandlers['tray-dictate']).toBeTypeOf('function'));

  void eventHandlers['tray-dictate'](shortcutEvent());
  await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
  micControl.resolve(micStop);

  const pause = await screen.findByRole('button', { name: 'Pause' });
  fireEvent.click(pause);
  expect(track.enabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  expect(track.enabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));

  await waitFor(() => expect(track.stop).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
});
