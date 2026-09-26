import { beforeEach, expect, it, vi } from 'vitest';
import {
  addTranscription,
  loadTranscriptions,
  TRANSCRIPTIONS_KEY,
  TRANSCRIPTION_EVENT,
} from '@shared/utils/transcriptionsStore';
beforeEach(() => localStorage.clear());
it('preserves existing history and publishes a complete entry with nullable timings', () => {
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify([{ id: 1, text: 'Existing' }]));
  const listener = vi.fn();
  window.addEventListener(TRANSCRIPTION_EVENT, listener);
  try {
    const saved = addTranscription({
      text: 'New',
      segments: [{ text: 'New', start: null, end: null }],
    });
    expect(saved.segments[0].start).toBeNull();
    expect(loadTranscriptions().map((entry) => entry.text)).toEqual(['New', 'Existing']);
    expect(listener).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener(TRANSCRIPTION_EVENT, listener);
  }
});
it('keeps the existing 200-entry bound and recovers from malformed storage', () => {
  localStorage.setItem(TRANSCRIPTIONS_KEY, '{');
  expect(loadTranscriptions()).toEqual([]);
  localStorage.setItem(
    TRANSCRIPTIONS_KEY,
    JSON.stringify(Array.from({ length: 200 }, (_, id) => ({ id, text: 'Old' }))),
  );
  addTranscription({ text: 'Latest' });
  expect(loadTranscriptions()).toHaveLength(200);
  expect(loadTranscriptions()[0].text).toBe('Latest');
});

it('keeps raw and refined transcripts separately', () => {
  addTranscription({ text: 'um original', refined_text: 'Original.' });
  expect(loadTranscriptions()[0]).toMatchObject({ text: 'um original', refined_text: 'Original.' });
});
