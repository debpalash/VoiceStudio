/**
 * useRecording (voice-clone reference recording) shares the same mic
 * pre-flight seam as the dictation pill: OS-denied → guided toast, no
 * getUserMedia; anything else → unchanged.
 */
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

const { toastMock, cleanAudioMock, startMicCaptureMock } = vi.hoisted(() => ({
  toastMock: Object.assign(vi.fn(), {
    error: vi.fn(),
    success: vi.fn(),
    dismiss: vi.fn(),
    loading: vi.fn(),
  }),
  cleanAudioMock: vi.fn(),
  startMicCaptureMock: vi.fn(),
}));
vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));
vi.mock('../api/system', () => ({ cleanAudio: cleanAudioMock }));
vi.mock('../utils/aec/micCapture', () => ({ startMicCapture: startMicCaptureMock }));

import useRecording from '../hooks/useRecording';

beforeEach(() => {
  invokeMock.mockReset();
  toastMock.error.mockClear();
  cleanAudioMock.mockReset();
  startMicCaptureMock.mockReset();
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  delete navigator.mediaDevices;
  delete globalThis.MediaRecorder;
});

function installGum(impl) {
  const gum = vi.fn(impl);
  Object.defineProperty(navigator, 'mediaDevices', {
    value: {
      getUserMedia: gum,
      enumerateDevices: vi.fn(async () => []),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
    configurable: true,
  });
  return gum;
}

it('OS-denied → guided toast, getUserMedia skipped', async () => {
  window.__TAURI_INTERNALS__ = {};
  invokeMock.mockImplementation(async (cmd) => (cmd === 'check_microphone' ? 'denied' : undefined));
  const gum = installGum(async () => {
    throw new Error('should not be reached');
  });
  const { result } = renderHook(() => useRecording(vi.fn()));
  await act(async () => {
    await result.current.startRecording();
  });
  expect(gum).not.toHaveBeenCalled();
  expect(toastMock.error).toHaveBeenCalled();
  expect(result.current.isRecording).toBe(false);
});

it('uses the selected microphone and requested channel mode', async () => {
  const gum = installGum(async () => {
    const error = new Error('busy');
    error.name = 'NotReadableError';
    throw error;
  });
  const { result } = renderHook(() => useRecording(vi.fn()));

  act(() => {
    result.current.setSelectedAudioInputId('usb-mic');
    result.current.setChannelMode('stereo');
  });
  await act(async () => {
    await result.current.startRecording();
  });

  expect(gum).toHaveBeenCalledWith({
    audio: { deviceId: { exact: 'usb-mic' }, channelCount: { ideal: 2 } },
  });
});

it('prompt/unknown/granted → getUserMedia proceeds as before', async () => {
  window.__TAURI_INTERNALS__ = {};
  invokeMock.mockImplementation(async (cmd) => (cmd === 'check_microphone' ? 'prompt' : undefined));
  const err = new Error('denied later');
  err.name = 'NotAllowedError';
  const gum = installGum(async () => {
    throw err; // reactive micError path still handles the real failure
  });
  const { result } = renderHook(() => useRecording(vi.fn()));
  await act(async () => {
    await result.current.startRecording();
  });
  expect(gum).toHaveBeenCalled();
  expect(toastMock.error).toHaveBeenCalled();
});

it('plain browser: no probe, straight to getUserMedia', async () => {
  const gum = installGum(async () => {
    const e = new Error('nope');
    e.name = 'NotFoundError';
    throw e;
  });
  const { result } = renderHook(() => useRecording(vi.fn()));
  await act(async () => {
    await result.current.startRecording();
  });
  expect(gum).toHaveBeenCalled();
  expect(invokeMock).not.toHaveBeenCalled();
});

it('reports microphone startup as pending until permission resolves', async () => {
  window.__TAURI_INTERNALS__ = {};
  let resolvePermission;
  invokeMock.mockImplementation(
    async (cmd) =>
      cmd === 'check_microphone' &&
      new Promise((resolve) => {
        resolvePermission = resolve;
      }),
  );
  const gum = installGum(async () => {
    throw new Error('test microphone unavailable');
  });
  const { result } = renderHook(() => useRecording(vi.fn()));

  let startPromise;
  let duplicateStartPromise;
  act(() => {
    startPromise = result.current.startRecording();
    duplicateStartPromise = result.current.startRecording();
  });
  await waitFor(() => expect(result.current.isStartingRecording).toBe(true));
  expect(invokeMock).toHaveBeenCalledTimes(1);

  resolvePermission('prompt');
  await act(async () => Promise.all([startPromise, duplicateStartPromise]));
  expect(result.current.isStartingRecording).toBe(false);
  expect(gum).toHaveBeenCalledTimes(1);
});

it('records a WAV through Web Audio when MediaRecorder is unsupported', async () => {
  const stopTrack = vi.fn();
  installGum(async () => ({
    getTracks: () => [{ stop: stopTrack }],
    getAudioTracks: () => [{ getSettings: () => ({ channelCount: 2 }) }],
  }));
  const stopCapture = vi.fn(async () => {});
  stopCapture.sampleRate = 16000;
  startMicCaptureMock.mockImplementation(async (_stream, onFrame, options) => {
    stopCapture.channels = options.channels;
    onFrame(new Float32Array(3200).fill(0.25));
    return stopCapture;
  });
  cleanAudioMock.mockResolvedValue(
    new Response(new Uint8Array(1200), {
      headers: {
        'Content-Type': 'audio/wav',
        'X-Clean-Filename': 'recording_clean.wav',
      },
    }),
  );
  const ingest = vi.fn(async () => {});
  const { result } = renderHook(() => useRecording(ingest));

  await act(async () => {
    result.current.setChannelMode('stereo');
  });
  await act(async () => {
    await result.current.startRecording();
  });
  expect(result.current.isRecording).toBe(true);

  await act(async () => {
    result.current.stopRecording();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(startMicCaptureMock).toHaveBeenCalledWith(
    expect.anything(),
    expect.any(Function),
    expect.objectContaining({ channels: 2 }),
  );
  expect(stopCapture).toHaveBeenCalledOnce();
  expect(stopTrack).toHaveBeenCalledOnce();
  const form = cleanAudioMock.mock.calls[0][0];
  const audio = form.get('audio');
  expect(audio.type).toBe('audio/wav');
  expect(audio.name).toBe('recording.wav');
  expect(new DataView(await audio.arrayBuffer()).getUint16(22, true)).toBe(2);
  expect(ingest).toHaveBeenCalledOnce();
});
