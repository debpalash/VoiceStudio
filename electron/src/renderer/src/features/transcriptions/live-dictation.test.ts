import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ api: vi.fn(), capture: vi.fn(), bridge: null as any }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api }));
vi.mock('@/hooks/use-backend-status', () => ({
  getBackendStatusSnapshot: () => ({ baseUrl: 'http://127.0.0.1:3900' }),
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks.bridge }));
vi.mock('@shared/utils/aec/micCapture', () => ({
  startMicCapture: mocks.capture,
}));
import { LiveDictation } from './live-dictation';

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = 3;
    this.onclose?.();
  });
  constructor(readonly url: URL) {
    Socket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  frame(value: object) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }
}
let live: LiveDictation;
let stopTrack: ReturnType<typeof vi.fn>;
let stopGraph: ReturnType<typeof vi.fn>;
let microphone: ReturnType<typeof vi.fn>;
let emit: (samples: Float32Array) => void;
beforeEach(() => {
  mocks.bridge = null;
  Socket.instances = [];
  vi.stubGlobal('WebSocket', Socket);
  stopTrack = vi.fn();
  stopGraph = vi.fn(async () => {});
  microphone = vi.fn(async (_constraints: MediaStreamConstraints) => ({
    getTracks: () => [{ stop: stopTrack }],
  }));
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: microphone } });
  mocks.api.mockImplementation(async (path: string) =>
    path.endsWith('/prefs')
      ? { enabled: true, model_id: 'sherpa-test' }
      : { engine_available: true, models: [{ id: 'sherpa-test', installed: true }] },
  );
  mocks.capture.mockImplementation(async (_stream: MediaStream, callback: typeof emit) => {
    emit = callback;
    return stopGraph;
  });
  live = new LiveDictation();
});
afterEach(() => {
  live.cancel();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it('checks installation before requesting microphone access', async () => {
  mocks.api.mockResolvedValue({
    enabled: true,
    model_id: 'missing',
    engine_available: true,
    models: [],
  });
  await live.start(vi.fn());
  expect(live.getSnapshot().issue).toBe('model');
  expect(microphone).not.toHaveBeenCalled();
  expect(Socket.instances).toHaveLength(0);
});
it('streams PCM and preserves repeated utterances without duplicating the EOF summary', async () => {
  const result = vi.fn();
  await live.start(result, 'studio-mic');
  const socket = Socket.instances[0];
  expect(socket.url.searchParams.get('model')).toBe('sherpa-test');
  expect(microphone.mock.calls[0]?.[0]).toMatchObject({
    audio: { deviceId: { exact: 'studio-mic' } },
  });
  emit(new Float32Array([-1, 1]));
  expect(Array.from(new Int16Array(socket.send.mock.calls[0][0]))).toEqual([-32768, 32767]);
  socket.frame({ type: 'partial', text: 'Hello' });
  expect(live.getSnapshot().text).toBe('Hello');
  socket.frame({ type: 'final', final_kind: 'utterance', text: 'Hello' });
  socket.frame({ type: 'final', final_kind: 'utterance', text: 'Hello' });
  socket.frame({ type: 'final', final_kind: 'summary', text: 'Hello Hello' });
  expect(result).toHaveBeenCalledTimes(2);
  expect(live.getSnapshot().stage).toBe('done');
  expect(stopTrack).toHaveBeenCalled();
  expect(stopGraph).toHaveBeenCalled();
});
it('releases late microphone permission after cancellation', async () => {
  let allow!: (value: { getTracks: () => { stop: typeof stopTrack }[] }) => void;
  microphone.mockImplementation(
    () =>
      new Promise((resolve) => {
        allow = resolve;
      }),
  );
  const pending = live.start(vi.fn());
  await vi.waitFor(() => expect(microphone).toHaveBeenCalled());
  live.cancel();
  allow({ getTracks: () => [{ stop: stopTrack }] });
  await pending;
  expect(stopTrack).toHaveBeenCalledOnce();
  expect(Socket.instances).toHaveLength(0);
  expect(live.getSnapshot().stage).toBe('idle');
});
it('ignores old socket callbacks after another session starts', async () => {
  const stale = vi.fn();
  await live.start(stale);
  const callback = Socket.instances[0].onmessage!;
  live.cancel();
  const current = vi.fn();
  await live.start(current);
  callback({ data: JSON.stringify({ type: 'final', final_kind: 'summary', text: 'old' }) });
  expect(stale).not.toHaveBeenCalled();
  expect(current).not.toHaveBeenCalled();
  expect(live.getSnapshot().stage).toBe('recording');
});
it('pauses transmission and waits for a final result after EOF', async () => {
  const result = vi.fn();
  await live.start(result);
  const socket = Socket.instances[0];
  live.pause();
  emit(new Float32Array([1]));
  expect(socket.send).not.toHaveBeenCalled();
  live.pause();
  emit(new Float32Array([1]));
  expect(socket.send).toHaveBeenCalledOnce();
  await live.stop();
  expect(socket.send).toHaveBeenLastCalledWith('EOF');
  expect(live.getSnapshot().stage).toBe('transcribing');
  socket.frame({ type: 'final', final_kind: 'summary', text: 'Finished.' });
  expect(result).toHaveBeenCalledWith(expect.objectContaining({ text: 'Finished.' }));
  expect(live.getSnapshot().stage).toBe('done');
});
it('keeps text available when storing a result fails', async () => {
  await live.start(() => {
    throw new Error('quota');
  });
  Socket.instances[0].frame({ type: 'final', final_kind: 'summary', text: 'Keep this.' });
  expect(live.getSnapshot()).toMatchObject({
    stage: 'error',
    issue: 'storage',
    text: 'Keep this.',
  });
});

it('accepts terminal results from a legacy PCM fallback', async () => {
  const result = vi.fn();
  await live.start(result);
  await live.stop();
  Socket.instances[0].frame({ type: 'final', text: 'Fallback transcript.' });
  expect(result).toHaveBeenCalledWith(expect.objectContaining({ text: 'Fallback transcript.' }));
  expect(live.getSnapshot().stage).toBe('done');
});
it('still sends EOF when audio graph cleanup rejects', async () => {
  await live.start(vi.fn());
  stopGraph.mockRejectedValueOnce(new Error('Already closed'));
  await expect(live.stop()).resolves.toBeUndefined();
  expect(Socket.instances[0].send).toHaveBeenLastCalledWith('EOF');
});

it('uses the dev WebSocket proxy even when the native bridge supplies a backend URL', async () => {
  vi.stubGlobal('window', {
    location: { href: 'http://localhost:3912/#/capture', protocol: 'http:' },
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('ws://localhost:3912');
  expect(Socket.instances[0].url.pathname).toBe('/api/ws/transcribe');
});
it('uses the trusted backend URL from the packaged app scheme', async () => {
  vi.stubGlobal('window', {
    location: { href: 'app://voicestudio/index.html#/capture', protocol: 'app:' },
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('ws://127.0.0.1:3900');
  expect(Socket.instances[0].url.pathname).toBe('/ws/transcribe');
});
it('preserves a main-issued ticket for authenticated remote dictation', async () => {
  mocks.bridge = {
    backend: {
      websocketUrl: vi.fn(
        async () => `wss://gpu-box:3900/ws/transcribe?ws_ticket=ovs_ws_ticket_${'b'.repeat(43)}`,
      ),
    },
  };
  vi.stubGlobal('window', {
    location: { href: 'app://voicestudio/index.html#/capture', protocol: 'app:' },
    voicestudio: mocks.bridge,
  });
  await live.start(vi.fn());
  expect(Socket.instances[0].url.origin).toBe('wss://gpu-box:3900');
  expect(Socket.instances[0].url.searchParams.get('ws_ticket')).toMatch(/^ovs_ws_ticket_/);
  expect(Socket.instances[0].url.searchParams.get('model')).toBe('sherpa-test');
});
