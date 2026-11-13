import { describe, expect, it } from 'vitest';
import { noteTranscriptSave, transcriptSaveMarker, transcriptSavedSince } from './transcript-saves';

describe('transcript saves', () => {
  it('reports a different transcript the user saved after the marker', () => {
    const marker = transcriptSaveMarker();
    expect(transcriptSavedSince('voice-a', marker, 'Hello there.')).toBe(false);
    noteTranscriptSave('voice-a', 'Hello, friend.');
    expect(transcriptSavedSince('voice-a', marker, 'Hello there.')).toBe(true);
    // Saves of other voices and saves before the marker do not count.
    expect(transcriptSavedSince('voice-b', marker, 'Hello there.')).toBe(false);
    expect(transcriptSavedSince('voice-a', transcriptSaveMarker(), 'Hello there.')).toBe(false);
  });

  it('counts a typed transcript for a voice that had none, and any length', () => {
    const marker = transcriptSaveMarker();
    noteTranscriptSave('voice-c', 'Typed by the user.');
    expect(transcriptSavedSince('voice-c', marker, null)).toBe(true);
    const legacyLength = transcriptSaveMarker();
    noteTranscriptSave('voice-d', 'b'.repeat(100));
    expect(transcriptSavedSince('voice-d', legacyLength, 'a'.repeat(100))).toBe(true);
  });

  it('ignores a save that keeps the transcript the take was queued with', () => {
    const marker = transcriptSaveMarker();
    noteTranscriptSave('voice-e', 'Same words.');
    expect(transcriptSavedSince('voice-e', marker, 'Same words.')).toBe(false);
  });
});
