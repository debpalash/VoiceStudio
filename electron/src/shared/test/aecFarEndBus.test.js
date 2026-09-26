import { describe, it, expect, vi, beforeEach } from 'vitest';
import { publishFarEnd, subscribeFarEnd, farEndListenerCount } from '../utils/aec/farEndBus';

describe('aec/farEndBus', () => {
  beforeEach(() => {
    // Drain any leftover subscribers between tests (module singleton).
    while (farEndListenerCount() > 0) {
      // subscribe then immediately unsubscribe can't clear others; instead
      // rely on each test cleaning up its own. This guard just asserts a
      // clean baseline.
      break;
    }
  });

  it('starts with no listeners and publish is a no-op', () => {
    expect(farEndListenerCount()).toBe(0);
    expect(() => publishFarEnd(new Float32Array([0.1]))).not.toThrow();
  });

  it('delivers frames to subscribers and unsubscribes cleanly', () => {
    const a = vi.fn();
    const b = vi.fn();
    const unsubA = subscribeFarEnd(a);
    const unsubB = subscribeFarEnd(b);
    expect(farEndListenerCount()).toBe(2);

    const frame = new Float32Array([0.5, -0.5]);
    publishFarEnd(frame);
    expect(a).toHaveBeenCalledWith(frame);
    expect(b).toHaveBeenCalledWith(frame);

    unsubA();
    expect(farEndListenerCount()).toBe(1);
    publishFarEnd(frame);
    expect(a).toHaveBeenCalledTimes(1); // no longer receiving
    expect(b).toHaveBeenCalledTimes(2);

    unsubB();
    expect(farEndListenerCount()).toBe(0);
  });

  it('a throwing subscriber does not break delivery to others', () => {
    const bad = vi.fn(() => {
      throw new Error('boom');
    });
    const good = vi.fn();
    const unsubBad = subscribeFarEnd(bad);
    const unsubGood = subscribeFarEnd(good);

    expect(() => publishFarEnd(new Float32Array([1]))).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);

    unsubBad();
    unsubGood();
  });
});
