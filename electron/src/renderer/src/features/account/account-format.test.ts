import { describe, expect, it } from 'vitest';
import { storageSize } from './account-format';

describe('storageSize', () => {
  it('formats sizes as KB, MB or GB', () => {
    expect(storageSize(0)).toBe('0 KB');
    expect(storageSize(12 * 1024)).toBe('12 KB');
    expect(storageSize(820 * 1024 ** 2)).toBe('820.0 MB');
    expect(storageSize(1.4 * 1024 ** 3)).toBe('1.4 GB');
  });

  it('moves to the next unit when rounding reaches 1024', () => {
    expect(storageSize(1024 ** 2 - 100)).toBe('1.0 MB'); // 1023.9 KB, was "1024 KB"
    expect(storageSize(1024 ** 3 - 20 * 1024)).toBe('1.0 GB'); // 1023.98 MB, was "1024.0 MB"
    expect(storageSize(1023 * 1024)).toBe('1023 KB');
    expect(storageSize(1023.9 * 1024 ** 2)).toBe('1023.9 MB');
  });
});
