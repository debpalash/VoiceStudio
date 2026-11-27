import { describe, expect, it } from 'vitest';
import { METER_BARS, formatElapsed, frameRms, nextBars, rmsToLevel } from './level-meter';

describe('level meter maths', () => {
  it('computes RMS of a frame and treats empty input as silence', () => {
    expect(frameRms(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5);
    expect(frameRms(new Float32Array(0))).toBe(0);
  });

  it('maps silence to 0 and loud input to 1, monotonically', () => {
    expect(rmsToLevel(0)).toBe(0);
    expect(rmsToLevel(Number.NaN)).toBe(0);
    expect(rmsToLevel(1)).toBe(1);
    expect(rmsToLevel(0.02)).toBeLessThan(rmsToLevel(0.08));
  });

  it('attacks instantly and settles to zero during a pause', () => {
    let bars: number[] = Array(METER_BARS).fill(0);
    bars = nextBars(bars, 0.9);
    expect(bars.at(-1)).toBe(0.9);
    for (let i = 0; i < 30; i++) bars = nextBars(bars, 0);
    expect(bars.every((value) => value === 0)).toBe(true);
    expect(bars).toHaveLength(METER_BARS);
  });

  it('formats elapsed time as m:ss', () => {
    expect(formatElapsed(0)).toBe('0:00');
    expect(formatElapsed(65_900)).toBe('1:05');
    expect(formatElapsed(-5)).toBe('0:00');
  });
});
