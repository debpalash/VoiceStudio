import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./farEndBus', () => ({ publishFarEnd: vi.fn() }));

import { publishFarEnd } from './farEndBus';
import { attachPlaybackTap } from './playbackTap';

let context;
let workletNode;
let contextSampleRate;

class FakeAudioContext {
  constructor() {
    context = this;
    this.sampleRate = contextSampleRate;
    this.state = 'running';
    this.destination = {};
    this.audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
    this.source = { connect: vi.fn(), disconnect: vi.fn() };
    this.filters = [];
  }

  createMediaElementSource() {
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

describe('attachPlaybackTap', () => {
  beforeEach(() => {
    contextSampleRate = 48000;
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode);
    publishFarEnd.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    context = undefined;
    workletNode = undefined;
    contextSampleRate = undefined;
  });

  it('delivers fixed-size 16 kHz reference frames from a 48 kHz context', async () => {
    const detach = await attachPlaybackTap({}, { sampleRate: 16000, frameSize: 4 });

    expect(workletNode.options.processorOptions).toEqual({ frameSize: 12, channels: 1 });
    workletNode.port.onmessage({
      data: new Float32Array([-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1, 0.75, 0.5, 0.25]),
    });

    expect(publishFarEnd).toHaveBeenCalledWith(new Float32Array([-1, -0.25, 0.5, 0.75]));

    await detach();
    expect(workletNode.disconnect).toHaveBeenCalledOnce();
    expect(context.source.connect).toHaveBeenCalledWith(context.destination);
  });

  it('low-passes the far-end reference before decimating (48 kHz context)', async () => {
    // The AEC reference decimates exactly like the mic path; without the
    // filter, content above 8 kHz folds into the reference and the canceller
    // subtracts tones the speaker never played.
    const detach = await attachPlaybackTap({}, { sampleRate: 16000, frameSize: 4 });

    expect(context.filters).toHaveLength(3);
    for (const filter of context.filters) {
      expect(filter.type).toBe('lowpass');
      expect(filter.frequency.value).toBeLessThan(16000 / 2);
    }
    // Tap branch only: element → filters → worklet, while the audible
    // element → destination edge stays direct.
    expect(context.source.connect).toHaveBeenCalledWith(context.destination);
    expect(context.source.connect).toHaveBeenCalledWith(context.filters[0]);
    expect(context.filters[0].connect).toHaveBeenCalledWith(context.filters[1]);
    expect(context.filters[1].connect).toHaveBeenCalledWith(context.filters[2]);
    expect(context.filters[2].connect).toHaveBeenCalledWith(workletNode);

    await detach();
    for (const filter of context.filters) expect(filter.disconnect).toHaveBeenCalled();
    // Only the tap edge is removed; the audible src→destination edge stays.
    expect(context.source.disconnect).toHaveBeenCalledWith(context.filters[0]);
    expect(context.source.disconnect).not.toHaveBeenCalledWith(context.destination);
  });

  it('skips the filter chain when the context honors the requested rate', async () => {
    contextSampleRate = 16000;
    const detach = await attachPlaybackTap({}, { sampleRate: 16000, frameSize: 4 });

    expect(context.filters).toHaveLength(0);
    expect(context.source.connect).toHaveBeenCalledWith(workletNode);

    await detach();
    expect(context.source.disconnect).toHaveBeenCalledWith(workletNode);
    expect(context.source.disconnect).not.toHaveBeenCalledWith(context.destination);
  });
});
