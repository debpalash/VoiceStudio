import { afterEach, expect, it, vi } from 'vitest';
import { createStreamingPreview } from './streaming-preview';
import { stopActivePlayback } from '@/lib/audio/playback';
class Context {
  static latest: Context;
  currentTime = 0; state = 'running'; destination = {};
  sources: Array<{ startAt: number; stop: ReturnType<typeof vi.fn> }> = [];
  gains: Array<{ changes: number[]; times: number[] }> = [];
  close = vi.fn(async () => {});
  constructor(readonly options?: { sampleRate: number }) { Context.latest = this; }
  createBuffer(_n: number, length: number) { return { getChannelData: () => new Float32Array(length) }; }
  createBufferSource() {
    const node = { startAt: 0, connect() {}, disconnect() {}, start(at: number) { node.startAt = at; }, stop: vi.fn() };
    this.sources.push(node); return node;
  }
  createGain() {
    const changes: number[] = []; const times: number[] = []; this.gains.push({ changes, times });
    return { connect() {}, disconnect() {}, gain: { setValueAtTime(v: number, at: number) { changes.push(v); times.push(at); }, linearRampToValueAtTime(v: number, at: number) { changes.push(v); times.push(at); } } };
  }
}
afterEach(() => { stopActivePlayback(); vi.useRealTimers(); vi.unstubAllGlobals(); });
it('does not stop the final chunk while its scheduled audio is still playing', async () => {
  vi.useFakeTimers(); vi.stubGlobal('AudioContext', Context);
  const player = createStreamingPreview(24000);
  player.appendPcm16Bytes(new Uint8Array(4800).buffer); // 100 ms of PCM
  player.finalize();
  const context = Context.latest;
  context.currentTime = context.sources[0]!.startAt + 0.09; // 10 ms still scheduled
  await vi.advanceTimersByTimeAsync(100);
  expect(context.close, 'remaining PCM must drain before completion').not.toHaveBeenCalled();
});
it('does not fade a recovered chunk into silence after the preceding source ended', () => {
  vi.stubGlobal('AudioContext', Context);
  const player = createStreamingPreview(24000, 20);
  const pcm = new Uint8Array(4800).buffer;
  player.appendPcm16Bytes(pcm);
  const context = Context.latest;
  context.currentTime = 1; // backend paused; the first 100-ms source has ended
  player.appendPcm16Bytes(pcm);
  expect(context.sources[1]!.startAt).toBeGreaterThan(context.currentTime);
  expect(context.gains[1]!.changes, 'no preceding audio exists to crossfade').not.toContain(0);
});

it('preserves an ordinary overlapping crossfade and the PCM device rate', () => {
  vi.stubGlobal('AudioContext', Context);
  const player = createStreamingPreview(24000, 20);
  const pcm = new Uint8Array(4800).buffer;
  player.appendPcm16Bytes(pcm);
  player.appendPcm16Bytes(pcm);
  const context = Context.latest;
  expect(context.options?.sampleRate).toBe(24000);
  expect(context.sources[0]!.startAt).toBeCloseTo(0.08);
  expect(context.sources[1]!.startAt).toBeCloseTo(0.16);
  expect(context.gains[0]!.changes).toEqual([1, 0]);
  expect(context.gains[1]!.changes).toEqual([0, 1]);
});
it('limits a recovered crossfade to the preceding source actual remaining overlap', () => {
  vi.stubGlobal('AudioContext', Context);
  const player = createStreamingPreview(24000, 20);
  const pcm = new Uint8Array(4800).buffer;
  player.appendPcm16Bytes(pcm);
  const context = Context.latest;
  context.currentTime = 0.151;
  player.appendPcm16Bytes(pcm);
  expect(context.sources[1]!.startAt).toBeCloseTo(0.171);
  expect(context.gains[1]!.changes).toEqual([0, 1]);
  expect(context.gains[1]!.times[1]).toBeCloseTo(0.18);
});
it('naturally completes after the final scheduled buffer and padding drain', async () => {
  vi.useFakeTimers(); vi.stubGlobal('AudioContext', Context);
  const done = vi.fn();
  const player = createStreamingPreview(24000, 0, done);
  player.appendPcm16Bytes(new Uint8Array(4800).buffer);
  player.finalize();
  const context = Context.latest;
  context.currentTime = context.sources[0]!.startAt + 0.1;
  await vi.advanceTimersByTimeAsync(100);
  expect(done).not.toHaveBeenCalled();
  context.currentTime += 0.06;
  await vi.advanceTimersByTimeAsync(100);
  expect(context.close).toHaveBeenCalledOnce();
  expect(done).toHaveBeenCalledOnce();
});
it('keeps explicit cancellation immediate and idempotent', () => {
  vi.stubGlobal('AudioContext', Context);
  const done = vi.fn();
  const player = createStreamingPreview(24000, 0, done);
  player.appendPcm16Bytes(new Uint8Array(4800).buffer);
  stopActivePlayback(); player.fail();
  expect(Context.latest.close).toHaveBeenCalledOnce();
  expect(Context.latest.sources[0]!.stop).toHaveBeenCalledOnce();
  expect(done).toHaveBeenCalledOnce();
});
it('completes an empty stream without waiting for a device-clock deadline', () => {
  vi.stubGlobal('AudioContext', Context);
  const done = vi.fn();
  createStreamingPreview(24000, 0, done).finalize();
  expect(Context.latest.close).toHaveBeenCalledOnce();
  expect(done).toHaveBeenCalledOnce();
});

it('anchors the first chunk after a delayed PCM response with the same scheduling lead', () => {
  vi.stubGlobal('AudioContext', Context);
  const player = createStreamingPreview(24000);
  const context = Context.latest;
  context.currentTime = 4;
  player.appendPcm16Bytes(new Uint8Array(4800).buffer);
  expect(context.sources[0]!.startAt - context.currentTime).toBeCloseTo(0.08);
});
