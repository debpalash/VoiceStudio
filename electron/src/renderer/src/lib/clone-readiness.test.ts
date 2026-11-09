import { describe, expect, it } from 'vitest';
import { cloneBlocker } from './clone-readiness';
import type { Profile } from './api/types';
const ready = { text: 'Hello', profileId: null, profiles: [], fileSize: 10 };
describe('clone readiness', () => {
  it('requires nonempty text and a real reference', () => {
    expect(cloneBlocker({ ...ready, fileSize: 0 })).toBe('reference');
    expect(cloneBlocker({ ...ready, text: ' \n ' })).toBe('text');
    expect(cloneBlocker(ready)).toBeNull();
  });
  it('rejects missing saved profiles and profiles without reference audio', () => {
    expect(cloneBlocker({ ...ready, profileId: 'deleted' })).toBe('reference');
    expect(
      cloneBlocker({
        ...ready,
        profileId: 'voice',
        profiles: [{ id: 'voice', kind: 'clone', ref_audio_path: null } as Profile],
      }),
    ).toBe('reference');
    expect(
      cloneBlocker({
        ...ready,
        profileId: 'voice',
        fileSize: 0,
        profiles: [{ id: 'voice', kind: 'clone', ref_audio_path: 'voice.wav' } as Profile],
      }),
    ).toBeNull();
  });
  it('waits for saved voices to load and recordings or files to finish processing', () => {
    expect(cloneBlocker({ ...ready, profileId: 'voice', profiles: undefined })).toBe('loading');
    expect(cloneBlocker({ ...ready, busy: true })).toBe('preparing');
  });
});

it('needs only text on a preset-only engine, whatever voice is selected', () => {
  // KittenTTS / Supertonic-3 speak with their own preset voices: no reference.
  expect(
    cloneBlocker({
      text: 'Hello',
      profileId: 'missing',
      profiles: [],
      fileSize: 0,
      needsReference: false,
    }),
  ).toBeNull();
  expect(
    cloneBlocker({ text: ' ', profileId: null, profiles: [], fileSize: 0, needsReference: false }),
  ).toBe('text');
});
