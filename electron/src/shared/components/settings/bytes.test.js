import { describe, expect, it } from 'vitest';
import { fmtBytes } from './bytes';

describe('fmtBytes', () => {
  it('formats sizes in the largest unit below 1024', () => {
    expect(fmtBytes(0)).toBe('0 B');
    expect(fmtBytes(391)).toBe('391 B');
    expect(fmtBytes(12 * 1024)).toBe('12 KB');
    expect(fmtBytes(820 * 1024 ** 2)).toBe('820 MB');
    expect(fmtBytes(1.4 * 1024 ** 3)).toBe('1.4 GB');
  });

  it('moves to the next unit when rounding reaches 1024', () => {
    expect(fmtBytes(1024 ** 2 - 100)).toBe('1.0 MB'); // 1023.9 KB, was "1024 KB"
    expect(fmtBytes(1024 ** 3 - 200 * 1024)).toBe('1.0 GB'); // 1023.8 MB, was "1024 MB"
    expect(fmtBytes(1023 * 1024)).toBe('1023 KB');
  });

  it('drops the decimal once a value rounds to 10', () => {
    expect(fmtBytes(9.96 * 1024 ** 3)).toBe('10 GB'); // was "10.0 GB"
    expect(fmtBytes(9.94 * 1024 ** 3)).toBe('9.9 GB');
  });
});
