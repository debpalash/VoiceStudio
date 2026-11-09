import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  socketUrl: vi.fn().mockResolvedValue('ws://127.0.0.1:3900/ws/tts'),
  append: vi.fn(),
  finalize: vi.fn(),
  fail: vi.fn(),
}));

vi.mock('@/lib/api/websocket', () => ({ backendWebSocketUrl: mocks.socketUrl }));
vi.mock('@/lib/audio/streaming-preview', () => ({
  supportsStreamingPreview: () => true,
  createStreamingPreview: () => ({
    appendPcm16Bytes: mocks.append,
    finalize: mocks.finalize,
    fail: mocks.fail,
  }),
}));

import { LIVE_DUB_PREVIEW_DELAY_MS, useDubLivePreview } from './use-dub-live-preview';

class Socket {
  static instances: Socket[] = [];
  readyState = 0;
  binaryType = '';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = 3;
  });

  constructor(readonly url: string) {
    Socket.instances.push(this);
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  frame(data: string | ArrayBuffer) {
    this.onmessage?.({ data });
  }
}

const segment = {
  id: 'line-1',
  start: 0,
  end: 1.5,
  text: 'Hola',
  text_original: 'Hello',
  profile_id: 'voice-1',
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', Socket);
  Socket.instances = [];
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it('debounces edits, streams the CAST voice and plays binary PCM chunks', async () => {
  const { result } = renderHook(() => useDubLivePreview({ enabled: true, language: 'Spanish' }));

  act(() => result.current.onEdit(segment, 'Hola de nuevo'));
  await act(async () => vi.advanceTimersByTimeAsync(LIVE_DUB_PREVIEW_DELAY_MS - 1));
  expect(Socket.instances).toHaveLength(0);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(Socket.instances).toHaveLength(1);
  expect(mocks.socketUrl).toHaveBeenCalledWith('/ws/tts');

  const socket = Socket.instances[0];
  act(() => socket.open());
  expect(socket.send).toHaveBeenCalledWith(
    JSON.stringify({ text: 'Hola de nuevo', speed: 1, voice: 'voice-1', language: 'Spanish' }),
  );
  act(() => socket.frame('{"type":"start","sample_rate":24000}'));
  const pcm = new Uint8Array([1, 2, 3, 4]).buffer;
  act(() => socket.frame(pcm));
  expect(mocks.append).toHaveBeenCalledWith(pcm);
  act(() => socket.frame('{"type":"done"}'));
  expect(mocks.finalize).toHaveBeenCalled();
  // The mocked preview never reports playback end; release the shared slot.
  act(() => result.current.stop());
});

it('opens the licence dialog when the TTS stream is refused for an unaccepted model', async () => {
  const seen = vi.fn();
  window.addEventListener('ov:model-licence-required', seen);
  const { result } = renderHook(() => useDubLivePreview({ enabled: true, language: 'Spanish' }));
  act(() => result.current.onEdit(segment, 'Hola otra vez'));
  await act(async () => vi.advanceTimersByTimeAsync(LIVE_DUB_PREVIEW_DELAY_MS));
  const socket = Socket.instances[0];
  act(() => socket.open());
  act(() =>
    socket.frame(
      JSON.stringify({
        type: 'error',
        detail: 'Accept the licence',
        code: 'model_licence_required',
        models: [{ repo_id: 'org/m', license: 'x', category: 'noncommercial', fingerprint: 'fp' }],
      }),
    ),
  );
  window.removeEventListener('ov:model-licence-required', seen);
  expect(seen).toHaveBeenCalledTimes(1);
});

it('cancels an obsolete stream immediately and stays silent while disabled', async () => {
  const { result, rerender } = renderHook(
    ({ enabled }) => useDubLivePreview({ enabled, language: 'Spanish' }),
    { initialProps: { enabled: true } },
  );
  act(() => result.current.onEdit(segment, 'Primero'));
  await act(async () => vi.advanceTimersByTimeAsync(LIVE_DUB_PREVIEW_DELAY_MS));
  const first = Socket.instances[0];

  act(() => result.current.onEdit(segment, 'Segundo'));
  expect(first.close).toHaveBeenCalled();
  rerender({ enabled: false });
  await act(async () => vi.advanceTimersByTimeAsync(LIVE_DUB_PREVIEW_DELAY_MS));
  expect(Socket.instances).toHaveLength(1);
});
