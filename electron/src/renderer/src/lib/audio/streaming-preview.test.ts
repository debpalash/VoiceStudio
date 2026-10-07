import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stopActivePlayback } from './playback';
import { createStreamingPreview } from './streaming-preview';

interface ParamEvent {
  kind: 'set' | 'ramp';
  value: number;
  time: number;
}

class FakeParam {
  events: ParamEvent[] = [];
  setValueAtTime(value: number, time: number) {
    this.events.push({ kind: 'set', value, time });
  }
  linearRampToValueAtTime(value: number, time: number) {
    this.events.push({ kind: 'ramp', value, time });
  }
}

class FakeGain {
  gain = new FakeParam();
  connect() {}
  disconnect() {}
}

class FakeSource {
  buffer: { duration: number } | null = null;
  startedAt: number | null = null;
  stopped = false;
  connect() {}
  disconnect() {}
  start(when: number) {
    this.startedAt = when;
  }
  stop() {
    this.stopped = true;
  }
}

class FakeContext {
  static instances: FakeContext[] = [];
  currentTime = 0;
  state = 'running';
  destination = {};
  sources: FakeSource[] = [];
  gains: FakeGain[] = [];
  closed = false;
  constructor(readonly options: { sampleRate: number }) {
    FakeContext.instances.push(this);
  }
  createBuffer(_channels: number, length: number, sampleRate: number) {
    const data = new Float32Array(length);
    return { duration: length / sampleRate, getChannelData: () => data };
  }
  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
  createGain() {
    const gain = new FakeGain();
    this.gains.push(gain);
    return gain;
  }
  resume() {
    return Promise.resolve();
  }
  close() {
    this.closed = true;
    this.state = 'closed';
    return Promise.resolve();
  }
}

const SAMPLE_RATE = 1000;
const chunk = (ms: number) => new Int16Array((SAMPLE_RATE * ms) / 1000).fill(1000).buffer;

describe('createStreamingPreview scheduling', () => {
  const saved = window.AudioContext;
  beforeEach(() => {
    vi.useFakeTimers();
    FakeContext.instances = [];
    window.AudioContext = FakeContext as unknown as typeof AudioContext;
  });
  afterEach(() => {
    stopActivePlayback();
    window.AudioContext = saved;
    vi.useRealTimers();
  });

  it('keeps the final chunk playing until its scheduled end', () => {
    const onDone = vi.fn();
    const preview = createStreamingPreview(SAMPLE_RATE, 0, onDone);
    const context = FakeContext.instances[0]!;
    preview.appendPcm16Bytes(chunk(100));
    preview.finalize();
    const source = context.sources[0]!;
    const end = source.startedAt! + 0.1;

    context.currentTime = end - 0.01;
    vi.advanceTimersByTime(100);
    expect(source.stopped).toBe(false);
    expect(context.closed).toBe(false);
    expect(onDone).not.toHaveBeenCalled();

    context.currentTime = end;
    vi.advanceTimersByTime(100);
    expect(context.closed).toBe(true);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('starts a chunk after an underrun at full gain instead of fading from silence', () => {
    const preview = createStreamingPreview(SAMPLE_RATE, 20);
    const context = FakeContext.instances[0]!;
    preview.appendPcm16Bytes(chunk(100));
    const firstEnd = context.sources[0]!.startedAt! + 0.1;

    context.currentTime = firstEnd + 0.5; // backend paused past the first chunk
    preview.appendPcm16Bytes(chunk(100));

    const second = context.sources[1]!;
    expect(second.startedAt).toBeGreaterThanOrEqual(firstEnd);
    expect(context.gains[1]!.gain.events).toEqual([]);
    expect(context.gains[0]!.gain.events).toEqual([]);
  });

  it('limits the fade to the remaining overlap when the previous chunk is nearly done', () => {
    const preview = createStreamingPreview(SAMPLE_RATE, 20);
    const context = FakeContext.instances[0]!;
    preview.appendPcm16Bytes(chunk(100));
    const firstEnd = context.sources[0]!.startedAt! + 0.1;

    context.currentTime = firstEnd - 0.025; // only 5 ms overlap left after the 20 ms lead
    preview.appendPcm16Bytes(chunk(100));

    const start = context.sources[1]!.startedAt!;
    const ramp = context.gains[1]!.gain.events.find((event) => event.kind === 'ramp')!;
    expect(ramp.time - start).toBeCloseTo(firstEnd - start, 6);
    expect(ramp.time).toBeLessThanOrEqual(firstEnd + 1e-9);
  });

  it('keeps the full crossfade for overlapping chunks', () => {
    const preview = createStreamingPreview(SAMPLE_RATE, 20);
    const context = FakeContext.instances[0]!;
    preview.appendPcm16Bytes(chunk(100));
    preview.appendPcm16Bytes(chunk(100));

    const firstEnd = context.sources[0]!.startedAt! + 0.1;
    expect(context.sources[1]!.startedAt).toBeCloseTo(firstEnd - 0.02, 6);
    expect(context.gains[1]!.gain.events).toEqual([
      { kind: 'set', value: 0, time: context.sources[1]!.startedAt },
      { kind: 'ramp', value: 1, time: firstEnd },
    ]);
    expect(context.gains[0]!.gain.events.at(-1)).toEqual({
      kind: 'ramp',
      value: 0,
      time: firstEnd,
    });
  });

  it('still cancels scheduled playback immediately', () => {
    const onDone = vi.fn();
    const preview = createStreamingPreview(SAMPLE_RATE, 0, onDone);
    const context = FakeContext.instances[0]!;
    preview.appendPcm16Bytes(chunk(100));
    preview.fail();
    expect(context.sources[0]!.stopped).toBe(true);
    expect(context.closed).toBe(true);
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
