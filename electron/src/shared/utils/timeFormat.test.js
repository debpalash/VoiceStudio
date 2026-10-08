import { describe, expect, it } from 'vitest';
import { formatTimestamp, splitRoundedMinutes } from './timeFormat';
import { formatTime } from './format';

describe('formatTimestamp', () => {
  it('carries a rounded-up seconds remainder into the minute', () => {
    expect(formatTimestamp(59.96)).toBe('1:00.0');
    expect(formatTimestamp(119.99)).toBe('2:00.0');
    expect(formatTimestamp(59.996, { decimals: 2 })).toBe('1:00.00');
  });

  it('keeps ordinary values unchanged', () => {
    expect(formatTimestamp(0)).toBe('0:00.0');
    expect(formatTimestamp(61)).toBe('1:01.0');
    expect(formatTimestamp(3661)).toBe('61:01.0');
    expect(formatTimestamp(5.25, { decimals: 2 })).toBe('0:05.25');
    expect(formatTimestamp(9.04, { decimals: 1 })).toBe('0:09.0');
  });

  it('supports hours and whole seconds', () => {
    expect(formatTimestamp(3661.5, { decimals: 2, hours: true })).toBe('1:01:01.50');
    expect(formatTimestamp(3599.999, { decimals: 2, hours: true })).toBe('1:00:00.00');
    expect(formatTimestamp(59.6, { decimals: 0 })).toBe('1:00');
  });

  it('renders invalid input as zero', () => {
    expect(formatTimestamp(NaN)).toBe('0:00.0');
    expect(formatTimestamp(-3)).toBe('0:00.0');
    expect(formatTimestamp(Infinity)).toBe('0:00.0');
  });

  it('is what formatTime uses', () => {
    expect(formatTime(59.96)).toBe('1:00.0');
  });
});

describe('splitRoundedMinutes', () => {
  it('never yields 60 seconds', () => {
    expect(splitRoundedMinutes(59.6)).toEqual({ minutes: 1, seconds: 0 });
    expect(splitRoundedMinutes(125.4)).toEqual({ minutes: 2, seconds: 5 });
  });
});
