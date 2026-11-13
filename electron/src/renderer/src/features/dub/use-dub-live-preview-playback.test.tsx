import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useDubLivePreview } from './use-dub-live-preview';
import { claimPlayback, stopActivePlayback } from '@/lib/audio/playback';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { appActivity } from '@/lib/app-activity';

vi.mock('@/lib/api/websocket', () => ({
  backendWebSocketUrl: async () => 'ws://127.0.0.1/ws/tts',
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));

class Context {
  static instances: Context[] = [];
  currentTime = 0;
  state = 'running';
  destination = {};
  close = vi.fn(async () => {});
  constructor() { Context.instances.push(this); }
  createBuffer(_channels: number, length: number) {
    return { getChannelData: () => new Float32Array(length) };
  }
  createBufferSource() {
    return { connect() {}, disconnect() {}, start() {}, stop() {} };
  }
  createGain() {
    return {
      connect() {}, disconnect() {},
      gain: { setValueAtTime() {}, linearRampToValueAtTime() {} },
    };
  }
}

class Socket {
  static instances: Socket[] = [];
  binaryType = '';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor() { Socket.instances.push(this); }
  frame(data: string | ArrayBuffer) { this.onmessage?.({ data }); }
}

const segment = { id: 'line', start: 0, end: 1, text: 'Hello', text_original: 'Hello', profile_id: 'voice' };
let extraRelease: (() => void) | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('AudioContext', Context);
  Socket.instances = [];
  Context.instances = [];
});

afterEach(() => {
  cleanup();
  stopActivePlayback();
  extraRelease?.();
  extraRelease = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function startPreview() {
  const view = renderHook(
    ({ enabled }) => useDubLivePreview({ enabled, language: 'English' }),
    { initialProps: { enabled: true } },
  );
  await act(async () => view.result.current.onToggle(segment));
  const socket = Socket.instances[0]!;
  act(() => {
    socket.onopen?.();
    socket.frame('{"type":"start","sample_rate":24000}');
    socket.frame(new Uint8Array(4800).buffer);
  });
  expect(appActivity.state.synthesis).toBe(1);
  return { ...view, socket };
}

function expectReleased() {
  expect(appActivity.state.synthesis).toBe(0);
  extraRelease = acquireSynthesis();
  expect(extraRelease).toBeTypeOf('function');
  extraRelease?.();
  extraRelease = null;
}

it.each(['stop', 'replacement'] as const)(
  'releases an unfinished live stream after global playback %s',
  async (mode) => {
    const { result, socket } = await startPreview();
    act(() => {
      if (mode === 'stop') stopActivePlayback();
      else extraRelease = claimPlayback(vi.fn(), 'reference');
    });
    expect(socket.close).toHaveBeenCalledOnce();
    expect(Context.instances[0]!.close).toHaveBeenCalledOnce();
    expect(result.current.liveSegmentId).toBeNull();
    extraRelease?.();
    extraRelease = null;
    expectReleased();
    await act(async () => result.current.onToggle(segment));
    expect(Socket.instances).toHaveLength(2);
  },
);

it('keeps the completed buffered tail until playback naturally ends', async () => {
  const { result, socket } = await startPreview();
  act(() => socket.frame('{"type":"done"}'));
  expect(socket.close).toHaveBeenCalledOnce();
  expectReleased();
  expect(result.current.liveSegmentId).toBe(segment.id);
  expect(Context.instances[0]!.close).not.toHaveBeenCalled();
  Context.instances[0]!.currentTime = 1;
  await act(async () => vi.advanceTimersByTimeAsync(100));
  expect(result.current.liveSegmentId).toBeNull();
  expect(Context.instances[0]!.close).toHaveBeenCalledOnce();
  expect(appActivity.state.synthesis).toBe(0);
});

it.each(['disable', 'unmount'] as const)('releases a live stream on %s', async (mode) => {
  const { rerender, unmount, socket } = await startPreview();
  if (mode === 'disable') rerender({ enabled: false });
  else unmount();
  expect(socket.close).toHaveBeenCalledOnce();
  expect(Context.instances[0]!.close).toHaveBeenCalledOnce();
  expectReleased();
});
