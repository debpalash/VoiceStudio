import { expect, it } from 'vitest';
import {
  canExportTranscriptSubtitles,
  formatTranscriptExport,
} from '@shared/utils/transcriptionFormat';
import { addTranscription, loadTranscriptions } from '@shared/utils/transcriptionsStore';

it.each([false, undefined])('refuses synthetic or legacy timing provenance: %s', (available) => {
  const saved = addTranscription({
    text: 'Speech across a long clip',
    timestamps_available: available,
    segments: [{ start: 0, end: 600, text: 'Speech across a long clip' }],
  });
  const restored = loadTranscriptions().find((entry) => entry.id === saved.id)!;
  expect(restored.timestamps_available).toBe(available);
  expect(canExportTranscriptSubtitles(restored)).toBe(false);
  expect(() => formatTranscriptExport([restored], { format: 'srt' })).toThrow();
  expect(formatTranscriptExport([restored], { format: 'text' })).toBe(
    'Speech across a long clip\n',
  );
});

it('saves only the visible full text when metadata-free text is selected', () => {
  const entry = addTranscription({
    text: 'um original',
    refined_text: 'First line.\nSecond line.',
    language: 'en',
  });
  expect(formatTranscriptExport([entry], { format: 'text' })).toBe('First line.\nSecond line.\n');
});

it('keeps every selected transcript without date/language headers', () => {
  const entries = ['First.', 'Second.'].map((text) => addTranscription({ text, language: 'en' }));
  expect(formatTranscriptExport(entries, { format: 'text' })).toBe('First.\n\nSecond.\n');
});

it('serializes real caption timings as UTF-8 SubRip, preserving original speech', () => {
  const saved = addTranscription({
    timestamps_available: true,
    text: 'Original words',
    refined_text: 'Edited words',
    segments: [
      { start: 0.125, end: 1.9996, text: 'Original' },
      { start: 3661.001, end: 3662.25, text: 'words\nnext line' },
    ],
  });
  const entry = loadTranscriptions().find((candidate) => candidate.id === saved.id)!;
  expect(entry.timestamps_available).toBe(true);
  expect(formatTranscriptExport([entry], { format: 'srt' })).toBe(
    '1\r\n00:00:00,125 --> 00:00:02,000\r\nOriginal\r\n\r\n' +
      '2\r\n01:01:01,001 --> 01:01:02,250\r\nwords\r\nnext line\r\n\r\n',
  );
});

it('requires a strict boolean timing guarantee even in malformed stored history', () => {
  const entry = addTranscription({
    text: 'Speech',
    timestamps_available: true,
    segments: [{ start: 0, end: 1, text: 'Speech' }],
  });
  const malformed = JSON.parse(JSON.stringify(entry));
  malformed.timestamps_available = 'true';
  expect(canExportTranscriptSubtitles(malformed)).toBe(false);
  expect(() => formatTranscriptExport([malformed], { format: 'srt' })).toThrow();
});

it.each(
  [
    [],
    [{ text: 'No timings' }],
    [
      { start: 0, end: 1, text: 'Timed' },
      { start: null, end: null, text: 'Untimed' },
    ],
    [{ start: -1, end: 1, text: 'Invalid' }],
    [{ start: 1, end: 1, text: 'Invalid' }],
    [{ start: 1, end: Number.POSITIVE_INFINITY, text: 'Invalid' }],
    [{ start: 0.0001, end: 0.0002, text: 'Zero milliseconds after rounding' }],
  ].map((segments) => ({ segments })),
)('refuses to invent or silently omit missing/invalid subtitle timings: %j', ({ segments }) => {
  const entry = addTranscription({ text: 'Speech', segments, timestamps_available: true });
  expect(canExportTranscriptSubtitles(entry)).toBe(false);
  expect(() => formatTranscriptExport([entry], { format: 'srt' })).toThrow();
});

it('keeps Unicode and spoken numbers, and removes empty cue separators', () => {
  const entry = addTranscription({
    timestamps_available: true,
    text: 'Café 123',
    segments: [{ text: '  ' }, { start: 0, end: 1, text: 'Café 123\r\n\r\nnext line' }],
  });
  expect(canExportTranscriptSubtitles(entry)).toBe(true);
  expect(formatTranscriptExport([entry], { format: 'srt' })).toBe(
    '1\r\n00:00:00,000 --> 00:00:01,000\r\nCafé 123\r\nnext line\r\n\r\n',
  );
  expect(() => formatTranscriptExport([entry, entry], { format: 'srt' })).toThrow();
});

it('keeps the existing history export unchanged', () => {
  const entry = addTranscription({ text: 'Original.', language: 'en' });
  expect(formatTranscriptExport([entry])).toContain('(en)\nOriginal.\n');
});
