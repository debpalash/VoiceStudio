import { describe, expect, it } from 'vitest';
import { fmtBytes, fmtGb } from './format';

describe('fmtGb', () => {
  it('uses MB below 1 GB and GB above, like fmtBytes', () => {
    expect(fmtGb(0.025)).toBe('25.6 MB');
    expect(fmtGb(0.104)).toBe('106.5 MB');
    expect(fmtGb(2.4)).toBe('2.40 GB');
    expect(fmtGb(undefined)).toBe('—');
  });
});

describe('fmtBytes', () => {
  it('keeps the existing unit boundaries', () => {
    expect(fmtBytes(0)).toBe('0 B');
    expect(fmtBytes(512 * 1024)).toBe('512 KB');
    expect(fmtBytes(1024 ** 2)).toBe('1.0 MB');
    expect(fmtBytes(1.5 * 1024 ** 3)).toBe('1.50 GB');
    expect(fmtBytes(-1)).toBe('—');
  });

  it('moves to the next unit when rounding reaches 1024', () => {
    expect(fmtBytes(1024 ** 2 - 100)).toBe('1.0 MB'); // 1023.9 KB, was "1024 KB"
    expect(fmtBytes(1024 ** 3 - 20 * 1024)).toBe('1.00 GB'); // 1023.98 MB, was "1024.0 MB"
    expect(fmtBytes(1024 ** 2 - 1024)).toBe('1023 KB');
  });
});
