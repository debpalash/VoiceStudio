import { describe, it, expect } from 'vitest';
import { UPDATE_CHANNELS, normalizeChannel } from './updateChannel';

describe('updateChannel', () => {
  it('exposes exactly stable + preview', () => {
    expect(UPDATE_CHANNELS).toEqual(['stable', 'preview']);
  });

  it('passes through known channels', () => {
    expect(normalizeChannel('stable')).toBe('stable');
    expect(normalizeChannel('preview')).toBe('preview');
  });

  it('clamps unknown / empty / nullish values to stable', () => {
    expect(normalizeChannel('beta')).toBe('stable');
    expect(normalizeChannel('')).toBe('stable');
    expect(normalizeChannel(undefined)).toBe('stable');
    expect(normalizeChannel(null)).toBe('stable');
  });
});
