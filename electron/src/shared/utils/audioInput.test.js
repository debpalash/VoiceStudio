import { describe, expect, it, vi } from 'vitest';
import {
  buildAudioInputConstraints,
  createInputLevelStore,
  listAudioInputs,
  startInputLevelMonitor,
} from './audioInput';

describe('audio input utilities', () => {
  it('publishes microphone levels without rerendering the app root', () => {
    const store = createInputLevelStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set(0.3);
    expect(store.getSnapshot()).toBe(0.3);
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
    store.set(0.6);
    expect(listener).toHaveBeenCalledOnce();
  });

  it('builds portable default, device, and channel constraints', () => {
    expect(buildAudioInputConstraints()).toEqual({ audio: true });
    expect(buildAudioInputConstraints('mic-2', 'mono')).toEqual({
      audio: { deviceId: { exact: 'mic-2' }, channelCount: { ideal: 1 } },
    });
    expect(buildAudioInputConstraints('', 'stereo')).toEqual({
      audio: { channelCount: { ideal: 2 } },
    });
  });

  it('lists only audio input devices', async () => {
    const audioInputs = await listAudioInputs({
      enumerateDevices: vi.fn(async () => [
        { kind: 'audiooutput', deviceId: 'speaker' },
        { kind: 'audioinput', deviceId: 'mic', label: 'Desk mic' },
      ]),
    });
    expect(audioInputs).toEqual([{ kind: 'audioinput', deviceId: 'mic', label: 'Desk mic' }]);
  });

  it('reports live input energy without audible monitoring and cleans up', async () => {
    const frames = [];
    const cancelled = vi.fn();
    const source = { connect: vi.fn(), disconnect: vi.fn() };
    const analyser = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      getFloatTimeDomainData: vi.fn((samples) => samples.fill(0.1)),
    };
    const silentGain = {
      gain: { value: 1 },
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
    const context = {
      destination: {},
      createMediaStreamSource: vi.fn(() => source),
      createAnalyser: vi.fn(() => analyser),
      createGain: vi.fn(() => silentGain),
      resume: vi.fn(async () => {}),
      close: vi.fn(async () => {}),
    };
    const levels = [];
    function MockAudioContext() {
      return context;
    }
    const stop = startInputLevelMonitor({}, (level) => levels.push(level), {
      AudioContextClass: MockAudioContext,
      requestFrame: vi.fn((callback) => {
        frames.push(callback);
        return frames.length;
      }),
      cancelFrame: cancelled,
    });

    expect(silentGain.gain.value).toBe(0);
    frames.shift()();
    expect(levels.at(-1)).toBeCloseTo(0.4);

    stop();
    expect(cancelled).toHaveBeenCalled();
    expect(source.disconnect).toHaveBeenCalled();
    expect(context.close).toHaveBeenCalled();
    expect(levels.at(-1)).toBe(0);
  });
});
