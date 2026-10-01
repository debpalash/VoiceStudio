import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  addTranscription,
  loadTranscriptions,
  removeTranscription,
  subscribeTranscriptions,
  TRANSCRIPTIONS_KEY,
  TRANSCRIPTION_EVENT,
} from '@shared/utils/transcriptionsStore';
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
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

it('deleting one same-clock utterance preserves the others and the selected row identity', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1000);
  const rows = Array.from({ length: 5 }, (_, index) => addTranscription({ text: `Utterance ${index}` }));
  const selected = rows[2]!;
  const updates = vi.fn();
  const unsubscribe = subscribeTranscriptions(updates);
  try {
    expect(new Set(rows.map(row => row.id)).size).toBe(5);
    removeTranscription(rows[4]!.id);
    expect(loadTranscriptions().map(row => row.text)).toEqual(['Utterance 3', 'Utterance 2', 'Utterance 1', 'Utterance 0']);
    expect(loadTranscriptions().find(row => row.id === selected.id)?.text).toBe(selected.text);
    expect(updates).toHaveBeenCalledOnce();
    expect(updates.mock.calls[0]![0]).toHaveLength(4);
  } finally {
    unsubscribe();
  }
});
it('reads persisted identities once and leaves prior records unchanged when avoiding a collision', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1000);
  const existing = [{ id: 1000, text: 'Earlier' }, { id: 1001, text: 'Next' }, { id: 50, text: 'Legacy' }];
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(existing));
  const reader = vi.spyOn(Storage.prototype, 'getItem');
  const saved = addTranscription({ text: 'Latest' });
  expect(reader).toHaveBeenCalledTimes(1);
  expect(saved.id).toBe(1002);
  expect(loadTranscriptions().slice(1)).toEqual(existing);
});
it('keeps a distinct numeric identity after wall-clock rollback', () => {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(1000);
  const first = addTranscription({ text: 'First' });
  clock.mockReturnValue(999);
  const second = addTranscription({ text: 'Second' });
  const third = addTranscription({ text: 'Third' });
  expect([first.id, second.id, third.id]).toEqual([1000, 999, 1001]);
  removeTranscription(second.id);
  expect(loadTranscriptions().map(row => row.text)).toEqual(['Third', 'First']);
});
it('keeps the 200-entry bound when every persisted timestamp identity is occupied', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1000);
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(Array.from({ length: 200 }, (_, offset) => ({ id: 1000 + offset, text: `Old ${offset}` }))));
  const saved = addTranscription({ text: 'Latest' });
  const history = loadTranscriptions();
  expect(saved.id).toBe(1200);
  expect(history).toHaveLength(200);
  expect(new Set(history.map(row => row.id)).size).toBe(200);
  expect(history[199]!.text).toBe('Old 198');
});

it('does not publish a history update when saving a distinct identity fails', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1000);
  const existing = [{ id: 1000, text: 'Existing' }];
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(existing));
  const listener = vi.fn();
  window.addEventListener(TRANSCRIPTION_EVENT, listener);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage full'); });
  try {
    expect(() => addTranscription({ text: 'New' })).toThrow('storage full');
    expect(loadTranscriptions()).toEqual(existing);
    expect(listener).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener(TRANSCRIPTION_EVENT, listener);
  }
});
