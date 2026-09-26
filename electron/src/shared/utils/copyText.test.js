import { describe, it, expect, vi, afterEach } from 'vitest';
import { copyText } from './copyText';

describe('copyText', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    try {
      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    } catch {
      /* noop */
    }
  });

  it('uses navigator.clipboard.writeText in a secure context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const ok = await copyText('hello');
    expect(writeText).toHaveBeenCalledWith('hello');
    expect(ok).toBe(true);
  });

  it('falls back to execCommand over plain HTTP (no navigator.clipboard) — never throws', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    const exec = vi.fn(() => true);
    document.execCommand = exec; // jsdom doesn't pre-define it; assign a mock
    const ok = await copyText('hello'); // must NOT throw "Cannot read properties of undefined"
    expect(exec).toHaveBeenCalledWith('copy');
    expect(ok).toBe(true);
  });
});
