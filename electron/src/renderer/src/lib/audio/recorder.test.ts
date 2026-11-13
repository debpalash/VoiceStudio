import { afterEach, describe, expect, it, vi } from 'vitest';
import { startInputLevelMonitor } from './recorder';

function stubAudioContext(overrides: Record<string, unknown> = {}) {
  const node = () => ({ connect: vi.fn(), disconnect: vi.fn() });
  const context = {
    destination: {},
    createMediaStreamSource: vi.fn(node),
    createAnalyser: vi.fn(() => ({ ...node(), fftSize: 0, getFloatTimeDomainData: vi.fn() })),
    createGain: vi.fn(() => ({ ...node(), gain: { value: 1 } })),
    resume: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
    ...overrides,
  };
  vi.stubGlobal('AudioContext', function AudioContextStub() {
    return context;
  });
  return context;
}

afterEach(() => vi.unstubAllGlobals());

describe('startInputLevelMonitor setup failure', () => {
  it('closes the AudioContext when graph setup throws', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const context = stubAudioContext({
      createMediaStreamSource: vi.fn(() => {
        throw new Error('bad stream');
      }),
    });
    expect(() => startInputLevelMonitor({} as MediaStream, vi.fn())).toThrow('bad stream');
    expect(context.close).toHaveBeenCalledOnce();
  });

  it('closes the AudioContext when frame scheduling throws', () => {
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn(() => {
        throw new Error('no frames');
      }),
    );
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const context = stubAudioContext();
    expect(() => startInputLevelMonitor({} as MediaStream, vi.fn())).toThrow('no frames');
    expect(context.close).toHaveBeenCalledOnce();
  });
});
