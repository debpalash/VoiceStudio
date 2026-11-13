import { describe, expect, it } from 'vitest';
import {
  beginTranscriptSave,
  commitTranscriptSave,
  failTranscriptSave,
  transcriptSaveMarker,
  transcriptSavedSince,
  beginVoiceChange,
  voiceChangesSettled,
} from './transcript-saves';

function save(profileId: string, transcript: string): void {
  commitTranscriptSave(beginTranscriptSave(profileId, transcript));
}

describe('transcript saves', () => {
  it('reports a different transcript the user saved after the marker', () => {
    const marker = transcriptSaveMarker();
    expect(transcriptSavedSince('voice-a', marker, 'Hello there.')).toBe(false);
    save('voice-a', 'Hello, friend.');
    expect(transcriptSavedSince('voice-a', marker, 'Hello there.')).toBe(true);
    // Saves of other voices and saves before the marker do not count.
    expect(transcriptSavedSince('voice-b', marker, 'Hello there.')).toBe(false);
    expect(transcriptSavedSince('voice-a', transcriptSaveMarker(), 'Hello there.')).toBe(false);
  });

  it('counts a typed transcript for a voice that had none, and any length', () => {
    const marker = transcriptSaveMarker();
    save('voice-c', 'Typed by the user.');
    expect(transcriptSavedSince('voice-c', marker, null)).toBe(true);
    const legacyLength = transcriptSaveMarker();
    save('voice-d', 'b'.repeat(100));
    expect(transcriptSavedSince('voice-d', legacyLength, 'a'.repeat(100))).toBe(true);
  });

  it('ignores a save that keeps the transcript the take was queued with', () => {
    const marker = transcriptSaveMarker();
    save('voice-e', 'Same words.');
    expect(transcriptSavedSince('voice-e', marker, 'Same words.')).toBe(false);
  });

  it('forgets a save that failed, so waiting takes keep running', () => {
    const marker = transcriptSaveMarker();
    const token = beginTranscriptSave('voice-f', 'Rejected edit.');
    expect(transcriptSavedSince('voice-f', marker, 'Old words.')).toBe(true);
    failTranscriptSave(token);
    expect(transcriptSavedSince('voice-f', marker, 'Old words.')).toBe(false);
  });

  it('catches a take queued while the save was pending once it commits', () => {
    const token = beginTranscriptSave('voice-g', 'New words.');
    // Queued mid-request: old cached transcript, marker taken before the commit.
    const marker = transcriptSaveMarker();
    expect(transcriptSavedSince('voice-g', marker, 'Old words.')).toBe(true);
    commitTranscriptSave(token);
    expect(transcriptSavedSince('voice-g', marker, 'Old words.')).toBe(true);
    // A take queued after the commit sees the new cache and runs.
    expect(transcriptSavedSince('voice-g', transcriptSaveMarker(), 'New words.')).toBe(false);
  });

  it('holds a waiting take until every change to its voice settles', async () => {
    await expect(voiceChangesSettled('voice-x')).resolves.toBeUndefined();
    const lockDone = beginVoiceChange('voice-x');
    const editDone = beginVoiceChange('voice-x');
    const otherDone = beginVoiceChange('voice-y');
    let settled = false;
    const waiting = voiceChangesSettled('voice-x').then(() => {
      settled = true;
    });
    lockDone();
    otherDone();
    await Promise.resolve();
    expect(settled).toBe(false);
    editDone();
    editDone(); // settling twice is harmless
    await waiting;
    expect(settled).toBe(true);
  });
});
