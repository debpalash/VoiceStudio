import { describe, it, expect, beforeEach } from 'vitest';
import { loadTranscriptions, TRANSCRIPTIONS_KEY, TRANSCRIPTION_EVENT } from './transcriptionsStore';

describe('transcriptionsStore', () => {
  beforeEach(() => localStorage.clear());

  it('pins the storage contract literals (backward-compat)', () => {
    expect(TRANSCRIPTIONS_KEY).toBe('omni_transcriptions');
    expect(TRANSCRIPTION_EVENT).toBe('omni:transcription-added');
  });

  it('returns [] for absent / empty / malformed / non-array; the array otherwise', () => {
    expect(loadTranscriptions()).toEqual([]); // absent
    localStorage.setItem(TRANSCRIPTIONS_KEY, '[{'); // malformed
    expect(loadTranscriptions()).toEqual([]);
    localStorage.setItem(TRANSCRIPTIONS_KEY, '"x"'); // non-array
    expect(loadTranscriptions()).toEqual([]);
    localStorage.setItem(TRANSCRIPTIONS_KEY, '{}'); // non-array obj
    expect(loadTranscriptions()).toEqual([]);
    const arr = [{ id: 1, text: 'hi' }];
    localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(arr));
    expect(loadTranscriptions()).toEqual(arr); // preserved, no re-sort
  });

  it('never throws even when localStorage access throws', () => {
    const orig = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('storage disabled');
      },
    });
    expect(() => loadTranscriptions()).not.toThrow();
    expect(loadTranscriptions()).toEqual([]);
    Object.defineProperty(window, 'localStorage', orig);
  });
});
