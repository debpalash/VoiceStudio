import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { startMicCapture } from './micCapture';

let context;
let workletNode;
let contextSampleRate;

class FakeAudioContext {
  constructor() {
    context = this;
    this.sampleRate = contextSampleRate;
    this.state = 'suspended';
    this.audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
    this.resume = vi.fn(async () => {
      this.state = 'running';
    });
    this.close = vi.fn().mockResolvedValue(undefined);
    this.source = {
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
    this.filters = [];
  }

  createMediaStreamSource() {
    return this.source;
  }

  createBiquadFilter() {
    const filter = {
      type: '',
      frequency: { value: 0 },
      Q: { value: 0 },
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
    this.filters.push(filter);
    return filter;
  }
}

class FakeAudioWorkletNode {
  constructor(_context, _name, options) {
    workletNode = this;
    this.options = options;
    this.port = { onmessage: null };
    this.disconnect = vi.fn();
  }
}

describe('startMicCapture', () => {
  beforeEach(() => {
    contextSampleRate = 48000;
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    context = undefined;
    workletNode = undefined;
    contextSampleRate = undefined;
  });

  it('resumes a suspended AudioContext before capturing microphone frames', async () => {
    const stop = await startMicCapture({}, vi.fn());

    expect(context.resume).toHaveBeenCalledOnce();
    expect(context.state).toBe('running');

    await stop();
  });

  it('delivers fixed-size 16 kHz frames when the AudioContext runs at 48 kHz', async () => {
    const frames = [];
    const stop = await startMicCapture({}, (frame) => frames.push(frame), {
      sampleRate: 16000,
      frameSize: 4,
    });

    expect(workletNode.options.processorOptions).toEqual({ frameSize: 12, channels: 1 });
    workletNode.port.onmessage({
      data: new Float32Array([-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1, 0.75, 0.5, 0.25]),
    });

    expect(frames).toEqual([new Float32Array([-1, -0.25, 0.5, 0.75])]);
    expect(stop.sampleRate).toBe(16000);

    await stop();
  });

  it('preserves worklet frames when the AudioContext honors the requested rate', async () => {
    contextSampleRate = 16000;
    const onFrame = vi.fn();
    const stop = await startMicCapture({}, onFrame, { sampleRate: 16000, frameSize: 4 });
    const frame = new Float32Array([-1, -0.25, 0.5, 0.75]);

    workletNode.port.onmessage({ data: frame });

    expect(onFrame).toHaveBeenCalledOnce();
    expect(onFrame).toHaveBeenCalledWith(frame);

    await stop();
  });

  it('low-passes before decimating when the browser refuses the requested rate', async () => {
    // WKWebView hands back 48 kHz whatever we ask for; interpolating straight
    // down to 16 kHz would fold everything above 8 kHz into the speech band.
    const stop = await startMicCapture({}, vi.fn(), { sampleRate: 16000, frameSize: 4 });

    expect(context.filters).toHaveLength(3);
    for (const filter of context.filters) {
      expect(filter.type).toBe('lowpass');
      expect(filter.frequency.value).toBeLessThan(16000 / 2); // below the fold frequency
      expect(filter.Q.value).toBeCloseTo(Math.SQRT1_2, 5); // Butterworth, no resonant peak
    }

    // Mic → filters → worklet, in order.
    expect(context.source.connect).toHaveBeenCalledWith(context.filters[0]);
    expect(context.filters[0].connect).toHaveBeenCalledWith(context.filters[1]);
    expect(context.filters[1].connect).toHaveBeenCalledWith(context.filters[2]);
    expect(context.filters[2].connect).toHaveBeenCalledWith(workletNode);

    await stop();
    for (const filter of context.filters) expect(filter.disconnect).toHaveBeenCalled();
  });

  it('skips the filter chain when the AudioContext honors the requested rate', async () => {
    contextSampleRate = 16000;
    const stop = await startMicCapture({}, vi.fn(), { sampleRate: 16000, frameSize: 4 });

    // No decimation happens, so there is nothing to anti-alias.
    expect(context.filters).toHaveLength(0);
    expect(context.source.connect).toHaveBeenCalledWith(workletNode);

    await stop();
  });

  it('fails loudly when the AudioContext cannot be resumed', async () => {
    // A suspended context runs no worklet: every frame is silently lost and
    // the pill sits on "Listening" forever. Better to surface it.
    const failing = class extends FakeAudioContext {
      constructor(...args) {
        super(...args);
        this.resume = vi.fn(async () => {
          throw new Error('user gesture required');
        });
      }
    };
    vi.stubGlobal('AudioContext', failing);

    await expect(startMicCapture({}, vi.fn())).rejects.toThrow(/mic-suspended/);
    expect(context.close).toHaveBeenCalled();
  });

  it('fails loudly when resume resolves but the context stays suspended', async () => {
    const stuck = class extends FakeAudioContext {
      constructor(...args) {
        super(...args);
        this.resume = vi.fn(async () => {}); // resolves, state never changes
      }
    };
    vi.stubGlobal('AudioContext', stuck);

    await expect(startMicCapture({}, vi.fn())).rejects.toThrow(/mic-suspended/);
    expect(context.close).toHaveBeenCalled();
  });
});
