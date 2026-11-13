import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  addTranscription,
  loadTranscriptions,
  removeTranscription,
  TRANSCRIPTIONS_KEY,
  TRANSCRIPTION_EVENT,
} from './transcriptionsStore';

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

describe('transcription identities', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-04T12:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('gives a burst of saves in one millisecond distinct numeric ids', () => {
    const saved = Array.from({ length: 100 }, (_, index) =>
      addTranscription({ text: `utterance ${index}` }),
    );
    expect(new Set(saved.map((entry) => entry.id)).size).toBe(100);
    expect(saved.every((entry) => Number.isInteger(entry.id))).toBe(true);

    removeTranscription(saved[42].id);
    const remaining = loadTranscriptions();
    expect(remaining).toHaveLength(99);
    expect(remaining.some((entry) => entry.text === 'utterance 42')).toBe(false);
  });

  it('stays unique after the clock steps back', () => {
    const first = addTranscription({ text: 'before' });
    vi.setSystemTime(new Date('2026-10-04T11:00:00Z'));
    const second = addTranscription({ text: 'after rollback' });
    expect(second.id).toBeGreaterThan(first.id);
  });

  it('keeps the 200-entry bound and existing records untouched', () => {
    const legacy = Array.from({ length: 200 }, (_, index) => ({ id: index + 1, text: `old ${index}` }));
    localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(legacy));
    addTranscription({ text: 'new' });
    const stored = loadTranscriptions();
    expect(stored).toHaveLength(200);
    expect(stored.slice(1)).toEqual(legacy.slice(0, 199));
  });

  it('removes only one row when an older history already repeats an id', () => {
    localStorage.setItem(
      TRANSCRIPTIONS_KEY,
      JSON.stringify([
        { id: 7, text: 'a' },
        { id: 7, text: 'b' },
        { id: 8, text: 'c' },
      ]),
    );
    removeTranscription(7);
    expect(loadTranscriptions().map((entry) => entry.text)).toEqual(['b', 'c']);
  });
});
