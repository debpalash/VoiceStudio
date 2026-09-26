import { describe, it, expect } from 'vitest';

// Stub Tauri globals before import so isTauri is false
delete globalThis.window?.__TAURI_INTERNALS__;
delete globalThis.window?.__TAURI__;

describe('media utils', () => {
  it('isTauri is false in jsdom', async () => {
    const { isTauri } = await import('../utils/media');
    expect(isTauri).toBe(false);
  });

  it('playPing does not throw in jsdom', async () => {
    const { playPing } = await import('../utils/media');
    // Should be a no-op (no AudioContext in jsdom), not an error
    expect(() => playPing()).not.toThrow();
  });
});

describe('format utils', () => {
  it('formatTime returns m:ss.d', async () => {
    const { formatTime } = await import('../utils/format');
    expect(formatTime(0)).toBe('0:00.0');
    expect(formatTime(61)).toBe('1:01.0');
    expect(formatTime(3661)).toBe('61:01.0');
  });
});

describe('constants', () => {
  it('POPULAR_LANGS is non-empty', async () => {
    const { POPULAR_LANGS } = await import('../utils/constants');
    expect(POPULAR_LANGS.length).toBeGreaterThan(0);
  });

  it('CLONE_MAX_SECONDS is a positive number', async () => {
    const { CLONE_MAX_SECONDS } = await import('../utils/constants');
    expect(CLONE_MAX_SECONDS).toBeGreaterThan(0);
  });

  it('PRESETS array has entries with attrs', async () => {
    const { PRESETS } = await import('../utils/constants');
    expect(PRESETS.length).toBeGreaterThan(0);
    expect(PRESETS[0]).toHaveProperty('attrs');
    expect(PRESETS[0]).toHaveProperty('id');
  });
});
